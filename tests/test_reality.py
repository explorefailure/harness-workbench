import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from harness_workbench import features, reality, runner, spec as specmod, subject_tree


def captured_document(*, claim=True, initial=1, final=0, outcome=True):
    before = [
        {"path": "hook.py", "sha256": "a"},
        {"path": "repair_task.md", "sha256": "b"},
        {"path": "slugger.py", "sha256": "red"},
        {"path": "test_slugger.py", "sha256": "c"},
    ]
    after = copy.deepcopy(before)
    after[2]["sha256"] = "green"
    return {
        "verdict": {"passed": claim},
        "adapter": {
            "subject": {
                "name": "pi", "version": "0.84.1", "model": "gpt-oss:20b",
                "model_profile": "local-ollama", "model_digest": "observed-digest",
            },
            "verdict": {"passed": True, "errors": []},
            "outcome": {"passed": outcome, "errors": []},
            "oracle_evidence": {
                "initial_test": {"returncode": initial, "termination_reason": None},
                "final_test": {"returncode": final, "termination_reason": None},
            },
            "workspace": {"before": before, "after": after},
        },
    }


class RealityCheckTests(unittest.TestCase):
    def canonical_recipe(self, root: Path) -> Path:
        recipe = root / "recipe"
        recipe.mkdir()
        source = Path(subject_tree.subjects_root())
        for name in reality.CANONICAL_FILES:
            target = recipe / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source / name).read_bytes())
        return recipe

    def test_model_claim_cannot_turn_failed_external_test_into_pass(self):
        with tempfile.TemporaryDirectory() as td:
            recipe = self.canonical_recipe(Path(td))
            false_success = captured_document(claim=True, initial=0, final=1,
                                              outcome=True)
            with mock.patch.object(reality.interrupt, "inspect_state", return_value={
                "state": reality.interrupt.COMPLETE,
            }), mock.patch.object(reality, "_attempt_document",
                                  return_value=false_success):
                result = reality.verify_run(Path(td) / "run", recipe)
            self.assertNotEqual("pass", result["verdict"])
            facts = {item["fact"]: item["observed"] for item in result["facts"]}
            self.assertFalse(facts["baseline_failed_before_attempt"])
            self.assertFalse(facts["required_test_passed_after_attempt"])
            self.assertFalse(facts["model_completion_claim_used_as_verdict"])

    def test_independent_red_green_and_exact_change_can_pass(self):
        with tempfile.TemporaryDirectory() as td:
            recipe = self.canonical_recipe(Path(td))
            with mock.patch.object(reality.interrupt, "inspect_state", return_value={
                "state": reality.interrupt.COMPLETE,
            }), mock.patch.object(reality, "_attempt_document",
                                  return_value=captured_document()):
                result = reality.verify_run(Path(td) / "run", recipe)
            self.assertEqual("pass", result["verdict"])
            self.assertEqual(["slugger.py"], result["changed_files"])

    def test_recipe_selects_local_profile_and_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            spec, created = reality.prepare_recipe(destination)
            self.assertTrue(created)
            self.assertTrue(spec.is_file())
            selection = json.loads(
                (destination / "model_selection.json").read_text(encoding="utf-8")
            )
            self.assertEqual("local-ollama", selection["active"])
            self.assertEqual(reality.MODEL_DIGEST_CONTRACT,
                             selection["profiles"]["local-ollama"]["digest_contract"])
            same, created_again = reality.prepare_recipe(destination)
            self.assertEqual(spec, same)
            self.assertFalse(created_again)

    def test_reality_check_passes_invoking_python_as_declared_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            seen = {}

            def execute(specification, loaded, runs_root, environment=None):
                seen["python"] = environment.get("HWB_PYTHON")
                seen["declared"] = specification.env
                return {"run_id": "run-id"}

            with mock.patch.object(reality.runner, "execute", side_effect=execute), \
                 mock.patch.object(reality, "verify_run", return_value={
                     "verdict": "pass", "facts": [], "changed_files": [],
                     "errors": [], "setup": {},
                 }):
                reality.run_check(
                    reality.CHECK,
                    runs_root=str(root / "runs"),
                    recipe_dir=str(root / "recipe"),
                )
            self.assertEqual(sys.executable, seen["python"])
            self.assertEqual(["HWB_PYTHON"], seen["declared"])
            self.assertNotIn("HWB_PYTHON", os.environ)

    def test_recipe_allows_only_recorded_editable_model_selection(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            spec, _ = reality.prepare_recipe(destination)
            selection_path = destination / reality.EDITABLE_RECIPE_FILE
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            selection["profiles"]["local-ollama"]["base_url"] = "http://localhost:11434/v1"
            selection_path.write_text(json.dumps(selection), encoding="utf-8")
            reopened, created = reality.prepare_recipe(destination)
            self.assertEqual(spec, reopened)
            self.assertFalse(created)

    def test_recipe_rejects_bound_file_tampering_and_missing_input(self):
        for name in ("runner.py", "hook.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                destination = Path(td) / "recipe"
                reality.prepare_recipe(destination)
                path = destination / name
                if name == "runner.py":
                    path.write_text("# edited\n", encoding="utf-8")
                else:
                    path.unlink()
                with self.assertRaisesRegex(reality.RealityError,
                                            "bound file was edited|required file"):
                    reality.prepare_recipe(destination)

    def test_recipe_rejects_manifest_and_digest_contract_drift(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            manifest_path = destination / reality.RECIPE_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["bound_file_digests"]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(reality.RealityError, "manifest is incomplete"):
                reality.prepare_recipe(destination)
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            selection_path = destination / reality.EDITABLE_RECIPE_FILE
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            selection["profiles"]["local-ollama"]["digest_contract"] = "pinned"
            selection_path.write_text(json.dumps(selection), encoding="utf-8")
            with self.assertRaisesRegex(reality.RealityError, "recorded-only"):
                reality.prepare_recipe(destination)

    def _verify_document(self, document, *, complete=True, mutate_template=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        recipe = self.canonical_recipe(Path(temporary.name))
        if mutate_template:
            (recipe / "repair_task.md").write_text("changed", encoding="utf-8")
        state = reality.interrupt.COMPLETE if complete else reality.interrupt.INCOMPLETE
        with mock.patch.object(reality.interrupt, "inspect_state",
                               return_value={"state": state}), \
             mock.patch.object(reality, "_attempt_document", return_value=document):
            return reality.verify_run(Path(temporary.name) / "run", recipe)

    def test_verdict_boundary_rejects_untrustworthy_evidence(self):
        cases = {
            "incomplete record": ({}, {"complete": False}),
            "changed template": ({}, {"mutate_template": True}),
            "bad baseline": ({"initial": 0}, {}),
            "invalid adapter measurement": ({"measurement": False}, {}),
            "missing oracle": ({"missing": "oracle_evidence"}, {}),
            "malformed workspace": ({"workspace": "bad"}, {}),
        }
        for label, (changes, options) in cases.items():
            with self.subTest(label=label):
                document = captured_document()
                if "initial" in changes:
                    document["adapter"]["oracle_evidence"]["initial_test"]["returncode"] = changes["initial"]
                if "measurement" in changes:
                    document["adapter"]["verdict"]["passed"] = changes["measurement"]
                if "missing" in changes:
                    del document["adapter"][changes["missing"]]
                if "workspace" in changes:
                    document["adapter"]["workspace"] = changes["workspace"]
                result = self._verify_document(document, **options)
                self.assertEqual("invalid", result["verdict"])

    def test_valid_unsuccessful_attempts_classify_fail_or_partial(self):
        failed = self._verify_document(captured_document(final=1, outcome=False))
        self.assertEqual("fail", failed["verdict"])
        unrelated = captured_document()
        unrelated["adapter"]["workspace"]["after"].append(
            {"path": "notes.txt", "sha256": "unexpected"}
        )
        partial = self._verify_document(unrelated)
        self.assertEqual("partial", partial["verdict"])
        self.assertIn("notes.txt", partial["changed_files"])

    def test_native_known_red_runs_through_capture_seal_and_verifier(self):
        """A deterministic native subject proves final-test sensitivity.

        The subject process and normal Workbench capture/sealing are native;
        only the model/adapter protocol document is simulated.  No model or
        network behavior is observed by this test.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            recipe = self.canonical_recipe(root)
            native_source = (
                "import json\n"
                "from pathlib import Path\n"
                "print(json.dumps(json.loads(Path('protocol.json').read_text()), "
                "sort_keys=True))\n"
            )

            def execute(document, condition):
                attempt = root / condition
                attempt.mkdir()
                fixture = attempt / "native_subject.py"
                fixture.write_text(native_source, encoding="utf-8")
                (attempt / "protocol.json").write_text(
                    json.dumps(document), encoding="utf-8"
                )
                specification_path = attempt / "known-red.json"
                specification_path.write_text(json.dumps({
                    "schema": "hwbspec/v0.1",
                    "features_root": "harness_workbench:builtin",
                    "features": [{"name": "freeze"}, {"name": "receipt"}],
                    "steps": [{
                        "id": reality.STEP_ID,
                        "argv": [sys.executable, "native_subject.py"],
                        "inputs": ["native_subject.py", "protocol.json"],
                    }],
                }), encoding="utf-8")
                specification = specmod.load(str(specification_path))
                record = runner.execute(specification, features.resolve(specification),
                                        str(attempt / "runs"))
                run_dir = attempt / "runs" / record["run_id"]
                self.assertEqual(reality.interrupt.COMPLETE,
                                 reality.interrupt.inspect_state(str(run_dir))["state"])
                self.assertTrue((run_dir / "steps" / reality.STEP_ID / "attempts" / "0" / "stdout.bin").is_file())
                return reality.verify_run(run_dir, recipe)

            diagnostics = io.StringIO()
            with contextlib.redirect_stderr(diagnostics):
                known_red = execute(
                    captured_document(claim=True, final=1, outcome=False), "red"
                )
                self.assertEqual("fail", known_red["verdict"])
                self.assertFalse({x["fact"]: x["observed"] for x in known_red["facts"]}
                                 ["required_test_passed_after_attempt"])
                self.assertFalse({x["fact"]: x["observed"] for x in known_red["facts"]}
                                 ["model_completion_claim_used_as_verdict"])
                green_control = execute(
                    captured_document(claim=True, final=0, outcome=True), "green"
                )
                self.assertEqual("pass", green_control["verdict"])
            self.assertNotIn("[freeze] DRIFT", diagnostics.getvalue())


if __name__ == "__main__":
    unittest.main()
