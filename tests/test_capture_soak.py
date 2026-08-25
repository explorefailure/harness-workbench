"""The determinism soak, at a size the suite can afford.

Stability alone is a weak claim -- a primitive that is broken the same way
every time is perfectly stable. So each scenario also asserts the contract it
was written to provoke, and the soak asserts that the answer does not move.

Raise the count for a real soak without editing this file:

    HWB_SOAK_RUNS=200 python3 -m unittest tests.test_capture_soak

or run `tests/capture_soak.py` directly, which also takes `--concurrency`.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import capture_soak  # noqa: E402
from harness_workbench import capture  # noqa: E402


RUNS = int(os.environ.get("HWB_SOAK_RUNS", "4"))


class CaptureSoakTests(unittest.TestCase):
    """One soak, then per-scenario assertions against its projection."""

    report: dict

    @classmethod
    def setUpClass(cls):
        if os.name != "posix":
            raise unittest.SkipTest("process-group contract is POSIX-only")
        cls.report = capture_soak.soak(runs=RUNS)

    def projection(self, name):
        return self.report["scenarios"][name]["projection"]

    def test_every_scenario_is_stable_across_runs(self):
        unstable = self.report["unstable"]
        self.assertEqual([], unstable, self.report["scenarios"])
        self.assertTrue(self.report["passed"])

    def test_all_eight_failure_modes_are_covered(self):
        """Named, so removing one is a failure rather than a smaller soak."""
        self.assertEqual(
            {
                "success",
                "nonzero_exit",
                "malformed_output",
                "saturation",
                "timeout",
                "ignored_termination",
                "orphan_child",
                "evidence_corruption",
            },
            set(self.report["scenarios"]),
        )

    def test_nothing_survives_cleanup_in_any_scenario(self):
        """The one invariant that must hold whatever the subject did."""
        for name, item in self.report["scenarios"].items():
            with self.subTest(scenario=name):
                self.assertFalse(item["projection"]["group_alive_after_cleanup"])

    def test_success_reports_no_bound(self):
        item = self.projection("success")
        self.assertIsNone(item["termination_reason"])
        self.assertEqual(0, item["returncode"])
        self.assertEqual(5, item["stdout_bytes"])

    def test_nonzero_exit_is_data_not_a_bound(self):
        item = self.projection("nonzero_exit")
        self.assertEqual(7, item["returncode"])
        self.assertIsNone(item["termination_reason"])

    def test_malformed_output_is_captured_whole(self):
        """Broken encoding is evidence; it must not be coerced or dropped."""
        item = self.projection("malformed_output")
        self.assertIsNone(item["termination_reason"])
        self.assertEqual(item["stdout_bytes"], item["stdout_source_bytes"])

    def test_saturation_keeps_exactly_the_limit_and_says_so(self):
        item = self.projection("saturation")
        self.assertEqual(capture.STDOUT_LIMIT, item["termination_reason"])
        self.assertTrue(item["stdout_overflow"])
        self.assertEqual(4096, item["stdout_bytes"])

    def test_timeout_names_the_bound_and_does_not_fake_an_exit_code(self):
        item = self.projection("timeout")
        self.assertEqual(capture.TIMEOUT, item["termination_reason"])
        self.assertTrue(item["exit_was_signal"])
        # 124 would be the synthesized value the promotion deliberately dropped.
        self.assertNotIn("returncode", item)

    def test_ignored_termination_still_ends(self):
        """SIGTERM refused, SIGKILL applied, group empty afterwards."""
        item = self.projection("ignored_termination")
        self.assertEqual(capture.TIMEOUT, item["termination_reason"])
        self.assertTrue(item["exit_was_signal"])

    def test_orphan_child_is_detected_and_then_cleaned_up(self):
        """The grandchild holds the pipe; the run ends at child exit anyway.

        The `before` assertion is the one that matters: it proves the orphan
        was seen. Without it this scenario would pass against an
        implementation that never checked the group at all.
        """
        item = self.projection("orphan_child")
        self.assertEqual(0, item["returncode"])
        self.assertIsNone(item["termination_reason"])
        self.assertTrue(item["group_alive_before_cleanup"])

    def test_only_the_orphan_scenario_leaves_anything_before_cleanup(self):
        for name, entry in self.report["scenarios"].items():
            with self.subTest(scenario=name):
                self.assertEqual(
                    name == "orphan_child",
                    entry["projection"]["group_alive_before_cleanup"],
                )

    def test_evidence_corruption_keeps_the_records_that_parsed(self):
        sidecar = self.projection("evidence_corruption")["sidecar"]
        self.assertEqual(2, sidecar["jsonl_records"])
        self.assertEqual(2, len(sidecar["errors"]))
        self.assertTrue(sidecar["exists"])


if __name__ == "__main__":
    unittest.main()
