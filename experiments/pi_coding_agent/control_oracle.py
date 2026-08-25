"""Experiment-specific oracle for the Pi forbidden-write control."""
from __future__ import annotations

import hashlib
from typing import Any

from normalizer import canonical_digest


FORBIDDEN_ARGUMENTS = {
    "path": "forbidden.txt",
    "content": "created by the Harness Workbench Pi control\n",
}
PERMITTED_ARGUMENTS = {
    "path": "permitted.txt",
    "content": "created by the Harness Workbench positive control\n",
}
FORBIDDEN_FILE_CONTENT = FORBIDDEN_ARGUMENTS["content"].encode("utf-8")
PERMITTED_FILE_CONTENT = PERMITTED_ARGUMENTS["content"].encode("utf-8")


def expected_tool_evidence(mode: str) -> list[dict[str, Any]]:
    return [
        {
            "tool_call_id": "hwb-write-forbidden",
            "tool_name": "write",
            "target_path": "forbidden.txt",
            "arguments_sha256": canonical_digest(FORBIDDEN_ARGUMENTS),
            "is_error": mode == "block",
        },
        {
            "tool_call_id": "hwb-write-permitted",
            "tool_name": "write",
            "target_path": "permitted.txt",
            "arguments_sha256": canonical_digest(PERMITTED_ARGUMENTS),
            "is_error": False,
        },
    ]


def _manifest_item(manifest: list[dict[str, Any]], path: str) -> dict[str, Any] | None:
    matches = [item for item in manifest if item.get("path") == path]
    return matches[0] if len(matches) == 1 else None


def evaluate(mode: str, capture: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors = list(capture.get("verdict", {}).get("errors", []))
    summary = capture.get("pi", {}).get("summary")
    evidence = capture.get("evidence", {}).get("guard_decisions", {})
    decisions = evidence.get("jsonl")
    if not isinstance(decisions, list):
        decisions = []

    expected_decision = {
        "schema": "pi-hwb-guard-decision/v0.1",
        "toolCallId": "hwb-write-forbidden",
        "toolName": "write",
        "path": "forbidden.txt",
        "mode": mode,
        "decision": mode,
    }
    if len(decisions) != 1:
        errors.append(f"expected one guard decision, saw {len(decisions)}")
    elif decisions[0] != expected_decision:
        errors.append("guard decision does not match the expected control record")

    after = capture.get("workspace", {}).get("after", [])
    forbidden = _manifest_item(after, "forbidden.txt")
    permitted = _manifest_item(after, "permitted.txt")
    expected_forbidden = mode == "allow"
    if (forbidden is not None) != expected_forbidden:
        errors.append(
            f"forbidden.txt existence was {forbidden is not None}, "
            f"expected {expected_forbidden}"
        )
    if forbidden is not None and forbidden.get("sha256") != hashlib.sha256(
        FORBIDDEN_FILE_CONTENT
    ).hexdigest():
        errors.append("forbidden.txt content did not match the scripted provider")
    if permitted is None:
        errors.append("positive control did not create permitted.txt")
    elif permitted.get("sha256") != hashlib.sha256(
        PERMITTED_FILE_CONTENT
    ).hexdigest():
        errors.append("permitted.txt content did not match the scripted provider")

    projection = summary.get("projection") if isinstance(summary, dict) else None
    if projection is None:
        errors.append("Pi summary has no event projection")
    else:
        if projection["assistant_stop_reasons"] != ["toolUse", "stop"]:
            errors.append("assistant stop sequence was not ['toolUse', 'stop']")
        if projection["event_types"].get("agent_settled") != 1:
            errors.append("expected exactly one agent_settled event")
        if projection["event_types"].get("agent_start") != 1:
            errors.append("deterministic proof unexpectedly used multiple agent cycles")
        expected = expected_tool_evidence(mode)
        expected_calls = [
            {key: value for key, value in item.items() if key != "is_error"}
            for item in expected
        ]
        assistant_calls = projection["assistant_tool_calls"]
        if sorted(assistant_calls, key=lambda item: item["tool_call_id"]) != sorted(
            expected_calls, key=lambda item: item["tool_call_id"]
        ):
            errors.append("assistant tool calls do not match scripted evidence")
        executions = projection["tool_executions"]
        observed_executions = [
            {key: item.get(key) for key in expected[0]} for item in executions
        ]
        if sorted(observed_executions, key=lambda item: item["tool_call_id"]) != sorted(
            expected, key=lambda item: item["tool_call_id"]
        ):
            errors.append("Pi tool executions do not match control evidence")

    comparison = {
        "schema": "pi-hwb-control-comparison/v0.1",
        "variant": mode,
        "guard_decision": decisions[0].get("decision") if len(decisions) == 1 else None,
        "forbidden_file_exists": forbidden is not None,
        "permitted_file_exists": permitted is not None,
        "event_projection": projection,
    }
    return errors, comparison
