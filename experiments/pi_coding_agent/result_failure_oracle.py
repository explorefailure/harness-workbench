"""Oracle for throwing post-execution ``tool_result`` handlers."""
from __future__ import annotations

import hashlib
from typing import Any

from normalizer import canonical_digest


EXPECTED_ADAPTER_ERRORS = [
    "Pi extension result_failure_thrower.ts failed: "
    "deterministic tool_result handler failure"
]
EXPECTED_EXTENSION_ERRORS = [
    {
        "schema": "pi-hwb-extension-error/v0.1",
        "extension": "result_failure_thrower.ts",
        "error": "deterministic tool_result handler failure",
    }
]
TREATMENT = {
    "tool_call_id": "hwb-compose-treatment",
    "tool_name": "write",
    "target_path": "requested.txt",
    "arguments_stage": "pre_tool_call_hook",
    "arguments_sha256": canonical_digest(
        {"path": "requested.txt", "content": "composition treatment\n"}
    ),
    "result_sha256": (
        "sha256:2f60a5c6561bb330a01bb53d8acb5b04a0d96bd54f6d2915f2b859ad1d84d8fa"
    ),
    "is_error": False,
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
    variant: [
        {
            "schema": "pi-hwb-result-failure/v0.1",
            "handler": handler,
            "observed": "requested.txt",
            "isError": False,
        }
        for handler in handlers
    ]
    for variant, handlers in {
        "throw-first": ("thrower", "audit"),
        "audit-first": ("audit", "thrower"),
    }.items()
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
    """Require a detected post-effect failure without false rollback claims."""
    errors: list[str] = []
    adapter_verdict = capture.get("verdict", {})
    if adapter_verdict.get("passed") is not False:
        errors.append("adapter did not reject the Pi extension runtime error")
    if adapter_verdict.get("errors") != EXPECTED_ADAPTER_ERRORS:
        errors.append("adapter errors do not isolate the expected result-hook failure")
    if capture.get("pi", {}).get("extension_errors") != EXPECTED_EXTENSION_ERRORS:
        errors.append("structured extension error does not match the treatment")

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
        errors.append("tool evidence does not prove two successful writes")
    if projection.get("assistant_stop_reasons") != ["toolUse", "toolUse", "stop"]:
        errors.append("assistant did not complete after the result-hook failure")

    events = (
        capture.get("evidence", {})
        .get("result_failure_order", {})
        .get("jsonl")
    )
    if events != EVENTS[variant]:
        errors.append("result-handler evidence does not match the declared order")

    before = _manifest(capture.get("workspace", {}).get("before"))
    after = _manifest(capture.get("workspace", {}).get("after"))
    expected_before = {"composition_task.md", "seed.txt"}
    expected_after = expected_before | {"requested.txt", "permitted.txt"}
    if set(before) != expected_before or set(after) != expected_after:
        errors.append("workspace does not prove both completed effects")
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

    handlers = [
        item.get("handler") for item in events or [] if isinstance(item, dict)
    ]
    comparison = {
        "schema": "pi-hwb-result-failure-comparison/v0.1",
        "variant": variant,
        "observed_handlers": handlers,
        "adapter_detected_extension_error": (
            adapter_verdict.get("errors") == EXPECTED_ADAPTER_ERRORS
        ),
        "effect_survived_post_hook_failure": (
            len(observed) == 2
            and observed[0].get("is_error") is False
            and "requested.txt" in after
        ),
        "remaining_handler_ran": set(handlers) == {"thrower", "audit"},
        "positive_control": (
            len(observed) == 2
            and observed[1].get("is_error") is False
            and "permitted.txt" in after
        ),
        "session_completed": projection.get("assistant_stop_reasons")
        == ["toolUse", "toolUse", "stop"],
    }
    return errors, comparison
