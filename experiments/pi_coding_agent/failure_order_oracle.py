"""Oracle for throwing ``tool_call`` handler ordering."""
from __future__ import annotations

import hashlib
from typing import Any

from normalizer import canonical_digest


TREATMENT = {
    "tool_call_id": "hwb-compose-treatment",
    "tool_name": "write",
    "target_path": "requested.txt",
    "arguments_stage": "pre_tool_call_hook",
    "arguments_sha256": canonical_digest(
        {"path": "requested.txt", "content": "composition treatment\n"}
    ),
    "result_sha256": (
        "sha256:10664ac229ee4b336924214d0e648d361df6a77de722e5b58a7149fad097819a"
    ),
    "is_error": True,
}
CONTROL = {
    "tool_call_id": "hwb-compose-control",
    "tool_name": "write",
    "target_path": "permitted.txt",
    "arguments_stage": "pre_tool_call_hook",
    "arguments_sha256": canonical_digest(
        {"path": "permitted.txt", "content": "composition control\n"}
    ),
    "result_sha256": (
        "sha256:9bb5b0a205951a9fdaa9ad4b58c03e69220ceedae6be8420bf02f8f5fbc88785"
    ),
    "is_error": False,
}
EVENTS = {
    "throw-first": [
        {
            "schema": "pi-hwb-handler-failure/v0.1",
            "handler": "thrower",
            "observed": "requested.txt",
        }
    ],
    "audit-first": [
        {
            "schema": "pi-hwb-handler-failure/v0.1",
            "handler": "audit",
            "observed": "requested.txt",
        },
        {
            "schema": "pi-hwb-handler-failure/v0.1",
            "handler": "thrower",
            "observed": "requested.txt",
        },
    ],
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
    """Require fail-closed execution, exact hook order, and a healthy control."""
    errors = list(capture.get("verdict", {}).get("errors", []))
    projection = capture.get("pi", {}).get("summary", {}).get("projection")
    if not isinstance(projection, dict):
        errors.append("Pi summary has no event projection")
        projection = {}

    expected_executions = [TREATMENT, CONTROL]
    observed = [
        {key: item.get(key) for key in TREATMENT}
        for item in projection.get("tool_executions", [])
        if isinstance(item, dict)
    ]
    if observed != expected_executions:
        errors.append("tool results do not prove fail-closed treatment and healthy control")
    if projection.get("assistant_stop_reasons") != ["toolUse", "toolUse", "stop"]:
        errors.append("assistant did not complete the throwing-handler sequence")

    events = capture.get("evidence", {}).get("handler_order", {}).get("jsonl")
    if events != EVENTS[variant]:
        errors.append("handler evidence does not match the declared failure order")

    before = _manifest(capture.get("workspace", {}).get("before"))
    after = _manifest(capture.get("workspace", {}).get("after"))
    expected_before = {"composition_task.md", "seed.txt"}
    expected_after = expected_before | {"permitted.txt"}
    if set(before) != expected_before or set(after) != expected_after:
        errors.append("workspace does not prove a blocked treatment and healthy control")
    for path in expected_before:
        if before.get(path) != after.get(path):
            errors.append(f"fixture input changed: {path}")
    if after.get("permitted.txt", {}).get("sha256") != hashlib.sha256(
        b"composition control\n"
    ).hexdigest():
        errors.append("positive-control content is incorrect")

    handlers = [
        item.get("handler") for item in events or [] if isinstance(item, dict)
    ]
    treatment_effect = "requested.txt" in after or "redirected.txt" in after
    comparison = {
        "schema": "pi-hwb-handler-failure-comparison/v0.1",
        "variant": variant,
        "observed_handlers": handlers,
        "audit_observed": "audit" in handlers,
        "throw_observed": "thrower" in handlers,
        "failed_closed": (
            len(observed) == 2
            and observed[0].get("is_error") is True
            and not treatment_effect
        ),
        "treatment_effect": treatment_effect,
        "positive_control": (
            len(observed) == 2
            and observed[1].get("is_error") is False
            and "permitted.txt" in after
        ),
        "projected_target_stage": "pre_tool_call_hook",
    }
    return errors, comparison
