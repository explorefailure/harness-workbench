"""Oracle for downstream branching on a rewritten Pi tool result."""
from __future__ import annotations

import hashlib
from typing import Any

from normalizer import canonical_digest


COMMAND = "test -d seed.txt && printf 'impossible treatment effect\\n' > attempted.txt"
TREATMENT_RESULTS = {
    "honest": {
        "result_sha256": (
            "sha256:9da11bf924440d204fe8213d6ee7d625f51c85099884740211421533e47f9bb5"
        ),
        "is_error": True,
    },
    "falsified": {
        "result_sha256": (
            "sha256:af49abfef46742f079ec46e782adf4375a75d82377c33081ba3e2fa9e2e5edf9"
        ),
        "is_error": False,
    },
}
BRANCHES = {
    "honest": {
        "tool_call_id": "hwb-branch-recovery",
        "tool_name": "write",
        "target_path": "recovery.txt",
        "arguments": {
            "path": "recovery.txt",
            "content": "recovered after observed failure\n",
        },
        "result_sha256": (
            "sha256:bc1a5bce5ecc6d10d3bc2e91119cd928955660931950c7b0da9a84a7845be4d5"
        ),
        "selected": "recovery",
        "observed_error": True,
    },
    "falsified": {
        "tool_call_id": "hwb-branch-trusted-success",
        "tool_name": "write",
        "target_path": "trusted.txt",
        "arguments": {
            "path": "trusted.txt",
            "content": "continued after claimed success\n",
        },
        "result_sha256": (
            "sha256:8eb096be95e6f8e75000f6f7af18785e82a77f13560fcfc3fe7e92b82f51a354"
        ),
        "selected": "trusted-success",
        "observed_error": False,
    },
}
CONTROL = {
    "tool_call_id": "hwb-branch-control",
    "tool_name": "write",
    "target_path": "permitted.txt",
    "arguments_sha256": canonical_digest(
        {"path": "permitted.txt", "content": "branch rewrite control\n"}
    ),
    "arguments_stage": "pre_tool_call_hook",
    "result_sha256": (
        "sha256:3873bf15ef56ec8bc1f607abda12d1f115fcaef98769748148e995b0b0c2c92b"
    ),
    "result_stage": "post_tool_result_hook",
    "is_error": False,
}


def _treatment(variant: str) -> dict[str, Any]:
    return {
        "tool_call_id": "hwb-failure-rewrite-treatment",
        "tool_name": "bash",
        "target_path": None,
        "arguments_sha256": canonical_digest({"command": COMMAND}),
        "arguments_stage": "pre_tool_call_hook",
        **TREATMENT_RESULTS[variant],
        "result_stage": "post_tool_result_hook",
    }


def _branch(variant: str) -> dict[str, Any]:
    source = BRANCHES[variant]
    return {
        "tool_call_id": source["tool_call_id"],
        "tool_name": source["tool_name"],
        "target_path": source["target_path"],
        "arguments_sha256": canonical_digest(source["arguments"]),
        "arguments_stage": "pre_tool_call_hook",
        "result_sha256": source["result_sha256"],
        "result_stage": "post_tool_result_hook",
        "is_error": False,
    }


def _manifest(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        return {}
    return {
        item["path"]: item
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def evaluate(variant: str, capture: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Require the exact status-driven branch and its durable consequence."""
    errors = list(capture.get("verdict", {}).get("errors", []))
    if capture.get("pi", {}).get("extension_errors") != []:
        errors.append("branch rewrite unexpectedly produced an extension error")

    projection = capture.get("pi", {}).get("summary", {}).get("projection")
    if not isinstance(projection, dict):
        errors.append("Pi summary has no event projection")
        projection = {}
    expected_executions = [_treatment(variant), _branch(variant), CONTROL]
    observed = [
        {key: item.get(key) for key in expected_executions[0]}
        for item in projection.get("tool_executions", [])
        if isinstance(item, dict)
    ]
    if observed != expected_executions:
        errors.append("tool executions do not match the result-driven branch")
    if projection.get("assistant_stop_reasons") != [
        "toolUse", "toolUse", "toolUse", "stop"
    ]:
        errors.append("assistant did not complete the branch sequence")

    rewrite = capture.get("evidence", {}).get("failure_rewrite", {}).get("jsonl")
    expected_rewrite = [{
        "schema": "pi-hwb-failure-rewrite/v0.1",
        "mode": variant,
        "observedIsError": True,
        "observedText": "(no output)\n\nCommand exited with code 1",
        "decision": "preserve" if variant == "honest" else "rewrite-success",
    }]
    if rewrite != expected_rewrite:
        errors.append("underlying failure rewrite evidence does not match the variant")
    branch = capture.get("evidence", {}).get("branch_decision", {}).get("jsonl")
    expected_branch = [{
        "schema": "pi-hwb-branch/v0.1",
        "observedIsError": BRANCHES[variant]["observed_error"],
        "selected": BRANCHES[variant]["selected"],
    }]
    if branch != expected_branch:
        errors.append("provider branch evidence does not match the post-hook result")

    before = _manifest(capture.get("workspace", {}).get("before"))
    after = _manifest(capture.get("workspace", {}).get("after"))
    branch_path = BRANCHES[variant]["target_path"]
    expected_before = {"branch_rewrite_task.md", "seed.txt"}
    expected_after = expected_before | {branch_path, "permitted.txt"}
    if set(before) != expected_before or set(after) != expected_after:
        errors.append("workspace does not match the selected branch and control")
    for path in expected_before:
        if before.get(path) != after.get(path):
            errors.append(f"fixture input changed: {path}")
    contents = {
        branch_path: BRANCHES[variant]["arguments"]["content"].encode("utf-8"),
        "permitted.txt": b"branch rewrite control\n",
    }
    for path, content in contents.items():
        if after.get(path, {}).get("sha256") != hashlib.sha256(content).hexdigest():
            errors.append(f"unexpected content for {path}")

    treatment = observed[0] if len(observed) == 3 else {}
    reported_error = treatment.get("is_error")
    durable_treatment_effect = "attempted.txt" in after
    effect_oracle_accepted = reported_error is True or (
        reported_error is False and durable_treatment_effect
    )
    expected_effect_acceptance = variant == "honest"
    if effect_oracle_accepted is not expected_effect_acceptance:
        errors.append("effect-aware oracle did not classify the branch variant as expected")
    branch_event = (
        branch[0]
        if isinstance(branch, list) and len(branch) == 1
        else {}
    )
    selected_branch = branch_event.get("selected")
    falsified = variant == "falsified"
    comparison = {
        "schema": "pi-hwb-branch-rewrite-comparison/v0.1",
        "variant": variant,
        "underlying_failure_observed": (
            isinstance(rewrite, list)
            and len(rewrite) == 1
            and rewrite[0].get("observedIsError") is True
        ),
        "final_reported_error": reported_error,
        "branch_observed_error": branch_event.get("observedIsError"),
        "selected_branch": selected_branch,
        "branch_effect": branch_path in after,
        "durable_treatment_effect": durable_treatment_effect,
        "effect_oracle_accepted": effect_oracle_accepted,
        "false_success_changed_next_action": (
            falsified
            and selected_branch == "trusted-success"
            and "trusted.txt" in after
            and "recovery.txt" not in after
        ),
        "positive_control": (
            len(observed) == 3
            and observed[2].get("is_error") is False
            and "permitted.txt" in after
        ),
    }
    return errors, comparison
