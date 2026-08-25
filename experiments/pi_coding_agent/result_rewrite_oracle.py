"""Oracle for ordered ``tool_result`` status and content rewriting."""
from __future__ import annotations

import hashlib
from typing import Any

from normalizer import canonical_digest


RESULTS = {
    "mask-first": {
        "result_sha256": (
            "sha256:910af7e5800ac86405c0f1711a48f21e8bd44452f2eed9bb51c59859e44a6465"
        ),
        "is_error": False,
    },
    "restore-first": {
        "result_sha256": (
            "sha256:b55f2f8d7430cfd5587a2dfaa6fcca7259d72f52778afe4d652d97ef80022101"
        ),
        "is_error": True,
    },
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
    "result_stage": "post_tool_result_hook",
    "is_error": False,
}
EVENTS = {
    "mask-first": [
        {
            "schema": "pi-hwb-result-rewrite/v0.1",
            "handler": "masker",
            "observedIsError": False,
            "observedText": "Successfully wrote 22 bytes to requested.txt",
        },
        {
            "schema": "pi-hwb-result-rewrite/v0.1",
            "handler": "restorer",
            "observedIsError": True,
            "observedText": "synthetic masked failure",
        },
    ],
    "restore-first": [
        {
            "schema": "pi-hwb-result-rewrite/v0.1",
            "handler": "restorer",
            "observedIsError": False,
            "observedText": "Successfully wrote 22 bytes to requested.txt",
        },
        {
            "schema": "pi-hwb-result-rewrite/v0.1",
            "handler": "masker",
            "observedIsError": False,
            "observedText": "synthetic restored success",
        },
    ],
}


def _treatment(variant: str) -> dict[str, Any]:
    return {
        "tool_call_id": "hwb-compose-treatment",
        "tool_name": "write",
        "target_path": "requested.txt",
        "arguments_stage": "pre_tool_call_hook",
        "arguments_sha256": canonical_digest(
            {"path": "requested.txt", "content": "composition treatment\n"}
        ),
        **RESULTS[variant],
        "result_stage": "post_tool_result_hook",
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
    """Require last-writer result semantics and independently verify effects."""
    errors = list(capture.get("verdict", {}).get("errors", []))
    if capture.get("pi", {}).get("extension_errors") != []:
        errors.append("result rewriting unexpectedly produced an extension error")

    projection = capture.get("pi", {}).get("summary", {}).get("projection")
    if not isinstance(projection, dict):
        errors.append("Pi summary has no event projection")
        projection = {}
    expected_executions = [_treatment(variant), CONTROL]
    observed = [
        {key: item.get(key) for key in expected_executions[0]}
        for item in projection.get("tool_executions", [])
        if isinstance(item, dict)
    ]
    if observed != expected_executions:
        errors.append("tool results do not match the declared rewrite order")
    if projection.get("assistant_stop_reasons") != ["toolUse", "toolUse", "stop"]:
        errors.append("assistant did not complete the result-rewrite sequence")

    events = (
        capture.get("evidence", {})
        .get("result_rewrite_order", {})
        .get("jsonl")
    )
    if events != EVENTS[variant]:
        errors.append("result-handler evidence does not match the declared order")

    before = _manifest(capture.get("workspace", {}).get("before"))
    after = _manifest(capture.get("workspace", {}).get("after"))
    expected_before = {"composition_task.md", "seed.txt"}
    expected_after = expected_before | {"requested.txt", "permitted.txt"}
    if set(before) != expected_before or set(after) != expected_after:
        errors.append("workspace does not prove both completed writes")
    for path in expected_before:
        if before.get(path) != after.get(path):
            errors.append(f"fixture input changed: {path}")
    contents = {
        "requested.txt": b"composition treatment\n",
        "permitted.txt": b"composition control\n",
    }
    for path, content in contents.items():
        if after.get(path, {}).get("sha256") != hashlib.sha256(content).hexdigest():
            errors.append(f"unexpected content for {path}")

    treatment = observed[0] if len(observed) == 2 else {}
    effect = "requested.txt" in after
    last_handler = "restorer" if variant == "mask-first" else "masker"
    final_error = treatment.get("is_error")
    comparison = {
        "schema": "pi-hwb-result-rewrite-comparison/v0.1",
        "variant": variant,
        "observed_handlers": [
            item.get("handler") for item in events or [] if isinstance(item, dict)
        ],
        "last_handler": last_handler,
        "last_rewrite_won": final_error is (last_handler == "masker"),
        "final_reported_error": final_error,
        "durable_treatment_effect": effect,
        "reported_status_agrees_with_effect": final_error is False and effect,
        "positive_control": (
            len(observed) == 2
            and observed[1].get("is_error") is False
            and "permitted.txt" in after
        ),
        "result_stage": treatment.get("result_stage"),
    }
    return errors, comparison
