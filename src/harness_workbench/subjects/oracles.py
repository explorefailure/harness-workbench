"""The two effect oracles this experiment judges its subjects by.

What used to live beside these -- bounded subprocess capture, credential
redaction, filesystem manifests, digests -- was a second implementation of
`harness_workbench.capture` and is gone. The tree imports the primitive now.
What is left here is the only part that was never subject-neutral: what
*this* experiment counts as the task being done.

Both oracles read before/after manifests collected from outside the subject.
A subject's own claim that it wrote a file is a claim; these are the check on
it, which is why neither consults the subject's transcript for the effect
itself. `repair_outcome` does read the subject's tool evidence, but only for
the *ordering* question a manifest cannot answer -- whether the test was run
red before the edit and green after -- never for whether the edit happened.
"""
from __future__ import annotations

import hashlib
from typing import Any

from harness_workbench.capture import Bounded


EXPECTED_CONTENT = b"cross-harness control\n"


def outcome(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge the exact-write workload: one new file, exact bytes, nothing else.

    Exactness is the point. A subject that produces `shared.txt` *and* a stray
    scratch file has not done this task, and an oracle that only checked the
    declared effect would score it identical to one that did.
    """
    before_map = {entry["path"]: entry for entry in before}
    after_map = {entry["path"]: entry for entry in after}
    errors = []
    if set(before_map) != {"hook.py", "task.md"}:
        errors.append("workspace fixture is not exact")
    if set(after_map) != {"hook.py", "task.md", "shared.txt"}:
        errors.append("workspace effects are not exact")
    for path in ("hook.py", "task.md"):
        if before_map.get(path) != after_map.get(path):
            errors.append(f"fixture input changed: {path}")
    expected_sha = hashlib.sha256(EXPECTED_CONTENT).hexdigest()
    if after_map.get("shared.txt", {}).get("sha256") != expected_sha:
        errors.append("shared.txt does not contain the exact expected bytes")
    return {
        "passed": not errors,
        "errors": errors,
        "declared_effect": "shared.txt",
        "effect_sha256": after_map.get("shared.txt", {}).get("sha256"),
        "expected_sha256": expected_sha,
    }


def repair_outcome(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    initial_test: Bounded,
    final_test: Bounded,
    tool_executions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Judge the red -> edit -> green workload against an external test run.

    The external runs are the evidence that the suite actually changed colour;
    the subject's transcript is consulted only to confirm it drove that change
    in the required order. `termination_reason` is checked alongside each
    return code because a test process the bound killed has no colour at all,
    and reading its exit status as red or green would invent a result.
    """
    before_map = {entry["path"]: entry for entry in before}
    after_map = {entry["path"]: entry for entry in after}
    expected_paths = {"hook.py", "repair_task.md", "slugger.py", "test_slugger.py"}
    errors = []
    if set(before_map) != expected_paths or set(after_map) != expected_paths:
        errors.append("repair workspace file set is not exact")
    for path in ("hook.py", "repair_task.md", "test_slugger.py"):
        if before_map.get(path) != after_map.get(path):
            errors.append(f"repair invariant changed: {path}")
    if before_map.get("slugger.py") == after_map.get("slugger.py"):
        errors.append("slugger.py did not change")
    if initial_test.returncode != 1 or initial_test.termination_reason is not None:
        errors.append("external initial test was not red")
    if final_test.returncode != 0 or final_test.termination_reason is not None:
        errors.append("external final test was not green")

    failed_command = None
    mutation = None
    passing_command = None
    for index, execution in enumerate(tool_executions):
        kind = execution.get("effect_kind")
        reported_error = execution.get("reported_error")
        operation = execution.get("operation")
        operation_exit_code = execution.get("operation_exit_code")
        command_failed = reported_error is True or (
            isinstance(operation_exit_code, int) and operation_exit_code != 0
        )
        command_passed = (
            operation_exit_code == 0
            or (operation_exit_code is None and reported_error is False)
        )
        if (
            failed_command is None
            and kind == "command"
            and operation == "python_unittest_v"
            and command_failed
        ):
            failed_command = index
        elif (
            failed_command is not None
            and mutation is None
            and kind == "write"
            and reported_error is False
        ):
            mutation = index
        elif (
            mutation is not None
            and kind == "command"
            and operation == "python_unittest_v"
            and command_passed
        ):
            passing_command = index
            break
    if failed_command is None or mutation is None or passing_command is None:
        errors.append("subject evidence lacks red-command -> write -> green-command")

    return {
        "passed": not errors,
        "errors": errors,
        "declared_effect": "slugger.py repair",
        "effect_sha256": after_map.get("slugger.py", {}).get("sha256"),
        "external_tests": {
            "initial_returncode": initial_test.returncode,
            "final_returncode": final_test.returncode,
        },
        "subject_sequence": {
            "failed_command_index": failed_command,
            "mutation_index": mutation,
            "passing_command_index": passing_command,
        },
    }
