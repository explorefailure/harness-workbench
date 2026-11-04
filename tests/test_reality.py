import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from harness_workbench import reality, subject_tree


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
                "model_profile": "local-ollama",
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
            same, created_again = reality.prepare_recipe(destination)
            self.assertEqual(spec, same)
            self.assertFalse(created_again)


if __name__ == "__main__":
    unittest.main()
