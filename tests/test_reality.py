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
    def setUp(self):
        # No test may depend on whether this machine runs Ollama.
        patcher = mock.patch.object(reality, "_local_model_digest", return_value=None)
        self.local_digest = patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_recipe_takes_this_machines_model_digest(self):
        """Regression: v0.2.0 copied the shipped August digest into every new
        Recipe, so a current Ollama made the first run INVALID."""
        self.local_digest.return_value = "f" * 64
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            local = json.loads((destination / "model_selection.json").read_text(
                encoding="utf-8"))["profiles"]["local-ollama"]
            self.assertEqual("f" * 64, local["model_digest"])
            self.assertEqual("f" * 64, local["baseline_digest"])
            shipped = json.loads((Path(subject_tree.subjects_root()) /
                                  "model_selection.json").read_text(encoding="utf-8"))
            self.assertNotEqual(shipped["profiles"]["local-ollama"]["model_digest"],
                                local["model_digest"])
            self.assertEqual("gpt-oss:20b", self.local_digest.call_args.args[0])

    def test_new_recipe_without_ollama_has_empty_baseline_and_stays_valid(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            local = json.loads((destination / "model_selection.json").read_text(
                encoding="utf-8"))["profiles"]["local-ollama"]
            self.assertEqual("", local["model_digest"])
            self.assertEqual("", local["baseline_digest"])
            self.assertTrue(local["verify_digest"])
            reality.prepare_recipe(destination)  # reopens through validation

    def test_release_gate_no_shipped_digest_reaches_the_active_profile(self):
        """Release gate for the v0.2.0 bug class: a digest pinned anywhere in
        the shipped subject tree must never become the checked digest of a new
        Recipe. Shipped pins are harvested, not listed, so a pin added later in
        another file is caught too."""
        shipped = set()

        def harvest(value):
            if isinstance(value, dict):
                for item in value.values():
                    harvest(item)
            elif isinstance(value, list):
                for item in value:
                    harvest(item)
            elif isinstance(value, str) and len(value) == 64 and all(
                    c in "0123456789abcdef" for c in value):
                shipped.add(value)

        for path in Path(subject_tree.subjects_root()).rglob("*.json"):
            harvest(json.loads(path.read_text(encoding="utf-8")))
        shipped_model_digests = {
            json.loads((Path(subject_tree.subjects_root()) / "pin.json").read_text(
                encoding="utf-8"))["ollama"]["model_digest"]}
        self.assertTrue(shipped_model_digests <= shipped)  # harvest sees the pin
        for live in (None, "f" * 64, "0" * 64):
            self.assertNotIn(live, shipped)
            with self.subTest(live=live), tempfile.TemporaryDirectory() as td:
                self.local_digest.return_value = live
                destination = Path(td) / "recipe"
                reality.prepare_recipe(destination)
                reality.refresh_live_digest(destination)
                local = self.selection(destination)
                self.assertEqual((live or "", live or ""),
                                 (local["model_digest"], local["baseline_digest"]))
                self.assertFalse({local["model_digest"], local["baseline_digest"]} & shipped)

    def selection(self, destination):
        return json.loads((destination / "model_selection.json").read_text(
            encoding="utf-8"))["profiles"]["local-ollama"]

    def test_run_refresh_records_live_digest_and_keeps_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            self.local_digest.return_value = "a" * 64
            reality.prepare_recipe(destination)
            self.local_digest.return_value = "b" * 64  # e.g. after `ollama pull`
            reality.refresh_live_digest(destination)
            local = self.selection(destination)
            self.assertEqual(("b" * 64, "a" * 64),
                             (local["model_digest"], local["baseline_digest"]))
            reality.prepare_recipe(destination)  # still a valid Recipe

    def test_run_refresh_adopts_first_readable_digest_as_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)  # Ollama unreadable: empty baseline
            self.local_digest.return_value = "c" * 64
            reality.refresh_live_digest(destination)
            local = self.selection(destination)
            self.assertEqual(("c" * 64, "c" * 64),
                             (local["model_digest"], local["baseline_digest"]))

    def test_run_refresh_repairs_a_recipe_created_by_0_2_0(self):
        """0.2.0 Recipes hold the shipped digest and no baseline; the refresh
        lives in the package, so it repairs them without recreating them."""
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            path = destination / "model_selection.json"
            selection = json.loads(path.read_text(encoding="utf-8"))
            local = selection["profiles"]["local-ollama"]
            local.pop("baseline_digest")
            local["model_digest"] = "17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7"
            path.write_text(json.dumps(selection), encoding="utf-8")
            self.local_digest.return_value = "f" * 64
            reality.refresh_live_digest(destination)
            local = self.selection(destination)
            self.assertEqual(("f" * 64, "f" * 64),
                             (local["model_digest"], local["baseline_digest"]))

    def test_run_refresh_leaves_recipe_alone_when_ollama_is_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            self.local_digest.return_value = "a" * 64
            reality.prepare_recipe(destination)
            before = (destination / "model_selection.json").read_bytes()
            self.local_digest.return_value = None
            reality.refresh_live_digest(destination)
            self.assertEqual(before, (destination / "model_selection.json").read_bytes())

    def test_model_change_is_reported_and_never_decides_the_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            self.local_digest.return_value = "a" * 64
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            same = reality.model_change(destination, {"model_digest": "a" * 64})
            self.assertEqual({"recipe_baseline": "a" * 64, "recorded": "a" * 64,
                              "matches_baseline": True}, same)
            moved = reality.model_change(destination, {"model_digest": "b" * 64})
            self.assertFalse(moved["matches_baseline"])
            unknown = reality.model_change(destination, {"model_digest": "unobserved"})
            self.assertIsNone(unknown["matches_baseline"])
            self.assertNotIn("verdict", moved)

    def test_terminal_model_line_names_the_case(self):
        line = reality._model_line
        self.assertIn("matches the Recipe's baseline",
                      line({"recorded": "a" * 64, "recipe_baseline": "a" * 64,
                            "matches_baseline": True}))
        changed = line({"recorded": "b" * 64, "recipe_baseline": "a" * 64,
                        "matches_baseline": False})
        self.assertIn("model changed since the Recipe was made", changed)
        self.assertIn("bbbbbbbbbbbb", changed)
        self.assertIn("no baseline yet", line({"recorded": "a" * 64, "recipe_baseline": None}))
        self.assertIn("not observed", line({}))

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

            def execute(specification, loaded, runs_root, environment=None, observer=None, stop=None):
                seen["python"] = environment.get("HWB_PYTHON")
                seen["overlay"] = environment.get(reality.LOCAL_PROFILES_ENV)
                seen["declared"] = specification.env
                return {"run_id": "run-id"}

            overlay = root / "profiles.json"
            overlay.write_text('{"profiles": {}}', encoding="utf-8")
            with mock.patch.dict(os.environ, {
                     reality.LOCAL_PROFILES_ENV: str(overlay),
                 }), mock.patch.object(reality.runner, "execute", side_effect=execute), \
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
            self.assertIsNone(seen["overlay"])
            self.assertEqual(["HWB_PYTHON"], seen["declared"])
            self.assertNotIn("HWB_PYTHON", os.environ)

    def test_reality_check_reports_each_phase_in_order(self):
        def execute(specification, loaded, runs_root, environment=None, observer=None, stop=None):
            observer("run_started", {"run_id": "run-id"})
            observer("step_started", {"step_id": reality.STEP_ID})
            observer("step_finished", {"step_id": reality.STEP_ID})
            return {"run_id": "run-id"}

        verified = {"verdict": "pass", "facts": [], "changed_files": [],
                    "errors": [], "setup": {}}
        for broken in (False, True):
            with self.subTest(broken_callback=broken), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                seen = []

                def progress(phase, detail):
                    seen.append((phase, detail))
                    if broken:
                        raise RuntimeError("display fault")

                with mock.patch.object(reality.runner, "execute", side_effect=execute), \
                     mock.patch.object(reality, "verify_run", return_value=verified):
                    result = reality.run_check(
                        reality.CHECK, runs_root=str(root / "runs"),
                        recipe_dir=str(root / "recipe"), progress=progress)
                self.assertEqual("pass", result["verdict"])
                self.assertEqual(list(reality.PROGRESS_PHASES), [p for p, _ in seen])
                self.assertEqual({"run_id": "run-id"}, dict(seen)["run_started"])

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



class RealityLlamaCppRecipeTests(unittest.TestCase):
    DIGEST = "a" * 64
    BUILD = "b12110-cd959bae9"

    def setUp(self):
        # Neither runtime may be contacted by a test.
        for name, value in (("_local_model_digest", None),
                            ("_llamacpp_identity", (self.DIGEST, self.BUILD))):
            patcher = mock.patch.object(reality, name, return_value=value)
            setattr(self, name.strip("_"), patcher.start())
            self.addCleanup(patcher.stop)

    def _profile(self, destination):
        selection = json.loads((destination / "model_selection.json").read_text(
            encoding="utf-8"))
        return selection["active"], selection["profiles"][selection["active"]]

    def _overlay(self, root: Path, profiles: object) -> Path:
        path = root / "profiles.json"
        path.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
        return path

    def test_owner_overlay_profile_is_copied_into_a_self_describing_recipe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = {
                "kind": "local",
                "runtime": "llama.cpp",
                "runtime_build": self.BUILD,
                "base_url": "http://127.0.0.1:8081/v1",
                "api_key_env": "HWB_LOCAL_KEY",
                "identity_strength": "local_content_digest",
                "verify_digest": True,
                "model_digest": "",
                "models": {"pi": "small-coder:1.5b"},
            }
            overlay = self._overlay(root, {"metal-small": profile})
            destination = root / "recipe"
            with mock.patch.dict(os.environ, {
                reality.LOCAL_PROFILES_ENV: str(overlay),
            }):
                reality.prepare_recipe(destination, "metal-small")
            name, local = self._profile(destination)
            self.assertEqual("metal-small", name)
            self.assertEqual("small-coder:1.5b", local["models"]["pi"])
            self.assertEqual(self.DIGEST, local["model_digest"])
            self.assertEqual(self.BUILD, local["runtime_build"])
            manifest = json.loads((destination / reality.RECIPE_NAME).read_text())
            self.assertEqual("metal-small", manifest["model_profile"])
            # The retained Recipe no longer depends on the owner overlay file.
            reality.prepare_recipe(destination)

    def test_owner_overlays_cannot_redefine_committed_or_escape_recipe_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cases = {
                "may not redefine committed profiles": {
                    "local-llamacpp": {"kind": "local"},
                },
                "profile name '../../escape' is unsafe": {
                    "../../escape": {"kind": "local"},
                },
            }
            for message, profiles in cases.items():
                with self.subTest(message=message):
                    overlay = self._overlay(root, profiles)
                    with mock.patch.dict(os.environ, {
                        reality.LOCAL_PROFILES_ENV: str(overlay),
                    }), self.assertRaisesRegex(reality.RealityError, message):
                        reality.prepare_recipe(root / ("recipe-" + str(len(message))),
                                               next(iter(profiles)))

    def test_gateway_overlay_is_not_a_reality_check_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            overlay = self._overlay(root, {
                "remote": {"kind": "gateway", "models": {"pi": "hosted"}},
            })
            with mock.patch.dict(os.environ, {
                reality.LOCAL_PROFILES_ENV: str(overlay),
            }):
                self.assertNotIn("remote", reality.available_recipe_profiles())
                with self.assertRaisesRegex(reality.RealityError,
                                            "unknown Recipe profile"):
                    reality.prepare_recipe(root / "recipe", "remote")

    def test_new_llamacpp_recipe_records_file_digest_and_build(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination, "local-llamacpp")
            name, local = self._profile(destination)
            self.assertEqual("local-llamacpp", name)
            self.assertEqual("llama.cpp", local["runtime"])
            self.assertEqual(self.DIGEST, local["baseline_digest"])
            self.assertEqual(self.DIGEST, local["model_digest"])
            self.assertEqual(self.BUILD, local["baseline_runtime_build"])
            self.assertEqual(self.BUILD, local["runtime_build"])
            manifest = json.loads((destination / "recipe.json").read_text(encoding="utf-8"))
            self.assertEqual("local-llamacpp", manifest["model_profile"])
            self.local_model_digest.assert_not_called()
            reality.prepare_recipe(destination)  # reopens through validation

    def test_new_build_and_model_are_recorded_and_reported_never_refused(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination, "local-llamacpp")
            self.llamacpp_identity.return_value = ("b" * 64, "b12200-0000000")
            reality.refresh_live_digest(destination)
            _, local = self._profile(destination)
            self.assertEqual("b" * 64, local["model_digest"])
            self.assertEqual(self.DIGEST, local["baseline_digest"])
            self.assertEqual("b12200-0000000", local["runtime_build"])
            self.assertEqual(self.BUILD, local["baseline_runtime_build"])
            change = reality.model_change(destination, {"model_digest": "b" * 64})
            self.assertIs(False, change["matches_baseline"])
            self.assertEqual({"recipe_baseline": self.BUILD, "recorded": "b12200-0000000",
                              "matches_baseline": False}, change["runtime_build"])

    def test_unreadable_llamacpp_leaves_the_recipe_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination, "local-llamacpp")
            before = (destination / "model_selection.json").read_bytes()
            self.llamacpp_identity.return_value = (None, None)
            reality.refresh_live_digest(destination)
            self.assertEqual(before, (destination / "model_selection.json").read_bytes())

    def test_ollama_recipes_keep_their_shape(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            name, local = self._profile(destination)
            self.assertEqual("local-ollama", name)
            self.assertNotIn("baseline_runtime_build", local)
            self.assertNotIn("runtime_build", reality.model_change(destination, {}))
            self.llamacpp_identity.assert_not_called()

    def test_existing_recipe_refuses_a_different_profile_and_unknown_profiles(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination)
            with self.assertRaisesRegex(reality.RealityError, "uses local-ollama"):
                reality.prepare_recipe(destination, "local-llamacpp")
            with self.assertRaisesRegex(reality.RealityError, "unknown Recipe profile"):
                reality.prepare_recipe(Path(td) / "other", "opencode-go")

    def test_recipe_profile_must_match_its_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "recipe"
            reality.prepare_recipe(destination, "local-llamacpp")
            path = destination / "model_selection.json"
            selection = json.loads(path.read_text(encoding="utf-8"))
            selection["active"] = "local-ollama"
            selection["profiles"]["local-ollama"].update(
                digest_contract=reality.MODEL_DIGEST_CONTRACT)
            path.write_text(json.dumps(selection), encoding="utf-8")
            with self.assertRaisesRegex(reality.RealityError, "Recipe profile changed"):
                reality.prepare_recipe(destination)

    def test_default_recipe_directory_is_separate_per_profile(self):
        self.assertNotEqual(reality._default_recipe(),
                            reality._default_recipe("local-llamacpp"))
        self.assertEqual(reality._default_recipe(),
                         reality._default_recipe("local-ollama"))


    def test_build_line_reports_only_llamacpp_build_changes(self):
        self.assertIsNone(reality._build_line({"recorded": "x"}))
        same = {"runtime_build": {"recorded": self.BUILD, "recipe_baseline": self.BUILD,
                                  "matches_baseline": True}}
        self.assertEqual(f"llama.cpp build: {self.BUILD}", reality._build_line(same))
        moved = {"runtime_build": {"recorded": "b2", "recipe_baseline": self.BUILD,
                                   "matches_baseline": False}}
        self.assertIn("Re-prove", reality._build_line(moved))

class LlamaCppIdentityParsingTests(unittest.TestCase):
    def _identity(self, **props):
        body = io.BytesIO(json.dumps({
            "model_alias": "qwen3.8:27b",
            "model_path": "/blobs/sha256-" + "c" * 64,
            "build_info": "b12110-cd959bae9", **props,
        }).encode())
        with mock.patch.object(reality, "urlopen", return_value=body) as opened:
            result = reality._llamacpp_identity("qwen3.8:27b", "http://127.0.0.1:8081/v1")
        self.assertEqual("http://127.0.0.1:8081/props", opened.call_args.args[0])
        return result

    def test_content_addressed_file_and_build(self):
        self.assertEqual(("c" * 64, "b12110-cd959bae9"), self._identity())

    def test_wrong_alias_reads_as_unknown(self):
        self.assertEqual((None, None), self._identity(model_alias="other"))

    def test_unaddressed_file_gives_no_digest(self):
        self.assertEqual((None, "b12110-cd959bae9"),
                         self._identity(model_path="/models/qwen.gguf"))

    def test_unreachable_server_reads_as_unknown(self):
        with mock.patch.object(reality, "urlopen", side_effect=OSError("refused")):
            self.assertEqual((None, None), reality._llamacpp_identity(
                "qwen3.8:27b", "http://127.0.0.1:8081/v1"))


if __name__ == "__main__":
    unittest.main()
