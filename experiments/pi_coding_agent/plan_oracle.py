"""Outcome oracle for the Pi plan-mode control pair."""
from __future__ import annotations

import hashlib
from typing import Any

from normalizer import canonical_digest


STEPS = (
    ("hwb-plan-read", "read", {"path": "seed.txt"}),
    ("hwb-plan-safe-bash", "bash", {"command": "test -f seed.txt"}),
    (
        "hwb-plan-direct-write",
        "write",
        {"path": "direct.txt", "content": "direct plan-mode effect\n"},
    ),
    (
        "hwb-plan-shell-write",
        "bash",
        {"command": "printf 'shell plan-mode effect\\n' > shell.txt"},
    ),
)
RESULTS = {
    "read": "sha256:3c822d84bab7ad843738bb2c6c7ff35aa9f6864eff20c5c6040d781b74735fd5",
    "success": "sha256:9c434ac41c33b1a01c402d5ad9a925bf229ed921fbc4fadb1928b58671984aa1",
    "inactive": "sha256:9412d3293173973e2a9837aadfcbcdf99fccbf36df538dbc867b176103f03e37",
    "blocked": "sha256:5e47d7eac7b97605630e0278631e54d0f7e2fe1cae3a785e50faef46397c411b",
    "write": "sha256:553c9d11b6ff08cba0dfe0d0ad717609c363196a5e08d1f1e217030e0603c9d3",
}


def _expected_executions(variant: str) -> list[dict[str, Any]]:
    errors = [False, False, variant == "plan", variant == "plan"]
    result_keys = (
        "read",
        "success",
        "inactive" if variant == "plan" else "write",
        "blocked" if variant == "plan" else "success",
    )
    return [
        {
            "tool_call_id": call_id,
            "tool_name": name,
            "target_path": arguments.get("path"),
            "arguments_sha256": canonical_digest(arguments),
            "result_sha256": RESULTS[result_key],
            "is_error": is_error,
        }
        for (call_id, name, arguments), result_key, is_error in zip(
            STEPS, result_keys, errors, strict=True
        )
    ]


def _expected_decisions(variant: str) -> list[dict[str, Any]]:
    active = ["read", "bash"] if variant == "plan" else ["read", "bash", "write"]
    records = [
        {
            "schema": "pi-hwb-plan-decision/v0.1",
            "mode": variant,
            "event": "active_tools",
            "activeTools": active,
        }
    ]
    for call_id, name, _arguments in STEPS:
        if variant == "plan" and name == "write":
            continue
        records.append(
            {
                "schema": "pi-hwb-plan-decision/v0.1",
                "mode": variant,
                "event": "tool_call",
                "toolCallId": call_id,
                "toolName": name,
                "decision": (
                    "block"
                    if variant == "plan" and call_id == "hwb-plan-shell-write"
                    else "allow"
                ),
            }
        )
    return records


def _manifest(manifest: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(manifest, list):
        return {}
    return {
        item["path"]: item
        for item in manifest
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def evaluate(variant: str, capture: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors = list(capture.get("verdict", {}).get("errors", []))
    projection = capture.get("pi", {}).get("summary", {}).get("projection")
    expected_executions = _expected_executions(variant)
    if not isinstance(projection, dict):
        errors.append("Pi summary has no event projection")
        projection = {}
    expected_calls = [
        {k: v for k, v in item.items() if k not in {"result_sha256", "is_error"}}
        for item in expected_executions
    ]
    if projection.get("assistant_tool_calls") != expected_calls:
        errors.append("assistant tool calls do not match the plan-mode fixture")
    observed = [
        {key: item.get(key) for key in expected_executions[0]}
        for item in projection.get("tool_executions", [])
        if isinstance(item, dict)
    ]
    if observed != expected_executions:
        errors.append("tool results do not match the declared plan-mode policy")
    if projection.get("assistant_stop_reasons") != [
        "toolUse", "toolUse", "toolUse", "toolUse", "stop"
    ]:
        errors.append("assistant did not complete the plan-mode control sequence")

    decisions = capture.get("evidence", {}).get("plan_decisions", {}).get("jsonl")
    expected_decisions = _expected_decisions(variant)
    if decisions != expected_decisions:
        errors.append("active-tool and policy decisions do not match the variant")

    before = _manifest(capture.get("workspace", {}).get("before"))
    after = _manifest(capture.get("workspace", {}).get("after"))
    expected_before = {"plan_task.md", "seed.txt"}
    expected_after = (
        expected_before
        if variant == "plan"
        else expected_before | {"direct.txt", "shell.txt"}
    )
    if set(before) != expected_before or set(after) != expected_after:
        errors.append("workspace paths do not match the plan-mode variant")
    for path in expected_before:
        if before.get(path) != after.get(path):
            errors.append(f"positive-control input changed: {path}")
    expected_contents = {
        "direct.txt": b"direct plan-mode effect\n",
        "shell.txt": b"shell plan-mode effect\n",
    }
    for path, content in expected_contents.items():
        item = after.get(path)
        if variant == "act" and (
            item is None or item.get("sha256") != hashlib.sha256(content).hexdigest()
        ):
            errors.append(f"action arm has incorrect {path} effect")

    comparison = {
        "schema": "pi-hwb-plan-comparison/v0.1",
        "variant": variant,
        "active_tools": expected_decisions[0]["activeTools"],
        "read_succeeded": len(observed) == 4 and observed[0].get("is_error") is False,
        "safe_bash_succeeded": len(observed) == 4 and observed[1].get("is_error") is False,
        "direct_write_effect": "direct.txt" in after,
        "shell_write_effect": "shell.txt" in after,
        "event_projection": projection,
    }
    return errors, comparison
