"""Experiment-specific oracle for the deterministic Pi coding repair."""
from __future__ import annotations

from typing import Any
import json
import math

from normalizer import canonical_digest


INITIAL_SOURCE = (
    'def slugify(text):\n'
    '    """Return a URL-safe identifier for a short ASCII label."""\n'
    '    return text.lower().replace(" ", "-")'
)
REPAIRED_SOURCE = (
    'import re\n\n\n'
    'def slugify(text):\n'
    '    """Return a URL-safe identifier for a short ASCII label."""\n'
    '    words = re.findall(r"[a-z0-9]+", text.casefold())\n'
    '    return "-".join(words)'
)
TEST_COMMAND = "python3 -B -m unittest -q >/dev/null 2>&1"

EXPECTED_BEFORE = {
    "coding_task.md": {
        "mode": 0o644,
        "size": 108,
        "sha256": "804c043d7f094a8b2d31db101975b3f47a78aae8522ae900d409fce993a46f56",
    },
    "slugger.py": {
        "mode": 0o644,
        "size": 125,
        "sha256": "d8aa64123083adb5c20da1f380f7eba7d61d7f9d7d491a336a2d589f308c2093",
    },
    "test_slugger.py": {
        "mode": 0o644,
        "size": 464,
        "sha256": "c754e69d6daac92a070e38c6e3938d617712a6f37606b8bcbc7779947220c867",
    },
}
EXPECTED_AFTER = {
    **EXPECTED_BEFORE,
    "slugger.py": {
        "mode": 0o644,
        "size": 176,
        "sha256": "83833a7772c5bdcd317089dc5fb2d641e5de6896c1698ab97198a99484b8849e",
    },
}


def expected_tool_evidence(pi_version: str = "0.84.1") -> list[dict[str, Any]]:
    steps = [
        (
            "hwb-coding-read-implementation",
            "read",
            {"path": "slugger.py"},
            False,
            "sha256:f80b0ef38d061c7edc87f0c7328bdcecbefb4a87debd3824339f89afa460c3f9",
        ),
        (
            "hwb-coding-read-tests",
            "read",
            {"path": "test_slugger.py"},
            False,
            "sha256:7170e445c0f7cfcd8b18eb12ad108c0a60915a0696f31ed55aca36fb2f8ee23f",
        ),
        (
            "hwb-coding-run-failing-tests",
            "bash",
            {"command": TEST_COMMAND},
            True,
            "sha256:9da11bf924440d204fe8213d6ee7d625f51c85099884740211421533e47f9bb5",
        ),
        (
            "hwb-coding-edit-implementation",
            "edit",
            {
                "path": "slugger.py",
                "edits": [
                    {"oldText": INITIAL_SOURCE, "newText": REPAIRED_SOURCE}
                ],
            },
            False,
            "sha256:54ae6891ac5c0f66e9c3a54f18fdb4b25c697d1525564619f873a531a6ff7cf3",
        ),
        (
            "hwb-coding-run-passing-tests",
            "bash",
            {"command": TEST_COMMAND},
            False,
            "sha256:9c434ac41c33b1a01c402d5ad9a925bf229ed921fbc4fadb1928b58671984aa1",
        ),
    ]
    result = [
        {
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "target_path": arguments.get("path"),
            "arguments_sha256": canonical_digest(arguments),
            "result_sha256": result_sha256,
            "is_error": is_error,
        }
        for call_id, tool_name, arguments, is_error, result_sha256 in steps
    ]
    if pi_version == "1.4.0":
        # Reviewed 1.4.0 result shapes: read adds structuredContent; bash
        # adds structured exit/output/truncation/time and explicit isError.
        for index, digest in {
            0: "a4c7d71c1696f82369975afe23327e683f9982c1da79c8fd1ee78a19ef6bbbda",
            1: "edbf4b72671c1ce951c52ad067b616e3d951b7ca42d4f0f96a91283301d0f578",
            2: "ce9d17c286dbbff2dba146d065733dc68d5717279d8ab936b09841fe8e9b4b6f",
            4: "c5521494cda4f3379d17fda6d80e6d9b45b9e4d1c9617dd0f04c657071869025",
        }.items():
            result[index]["result_sha256"] = "sha256:" + digest
    return result


def expected_manifests() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def materialize(source: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"path": path, **source[path]} for path in sorted(source)]

    return materialize(EXPECTED_BEFORE), materialize(EXPECTED_AFTER)


def _manifest_map(
    value: Any, label: str, errors: list[str]
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f"workspace {label} manifest is not a list")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append(f"workspace {label} manifest contains a malformed item")
            continue
        path = item["path"]
        if path in result:
            errors.append(f"workspace {label} manifest repeats {path!r}")
        result[path] = item
    return result


def _check_manifest(
    label: str,
    observed: dict[str, dict[str, Any]],
    expected: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    if set(observed) != set(expected):
        errors.append(
            f"workspace {label} paths were {sorted(observed)!r}, "
            f"expected {sorted(expected)!r}"
        )
    for path in sorted(set(observed) & set(expected)):
        actual = {key: observed[path].get(key) for key in ("mode", "size", "sha256")}
        if actual != expected[path]:
            errors.append(f"workspace {label} state for {path!r} was not expected")


def evaluate(capture: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    verdict = capture.get("verdict")
    errors = list(verdict.get("errors", [])) if isinstance(verdict, dict) else []
    if not isinstance(verdict, dict):
        errors.append("adapter capture has no verdict")
    elif verdict.get("passed") is not True and not errors:
        errors.append("adapter capture failed without a typed error")

    workspace = capture.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
        errors.append("adapter capture has no workspace evidence")
    before = _manifest_map(workspace.get("before"), "before", errors)
    after = _manifest_map(workspace.get("after"), "after", errors)
    _check_manifest("before", before, EXPECTED_BEFORE, errors)
    _check_manifest("after", after, EXPECTED_AFTER, errors)

    changed_paths = sorted(
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )
    if changed_paths != ["slugger.py"]:
        errors.append(
            f"changed workspace paths were {changed_paths!r}, expected ['slugger.py']"
        )

    pi = capture.get("pi")
    summary = pi.get("summary") if isinstance(pi, dict) else None
    projection = summary.get("projection") if isinstance(summary, dict) else None
    executions: list[dict[str, Any]] = []
    pi_version = capture.get("runtime", {}).get("pi_version", "0.84.1")
    expected = expected_tool_evidence(pi_version)
    if not isinstance(summary, dict) or summary.get("valid") is not True:
        errors.append("Pi summary is not valid")
    if not isinstance(projection, dict):
        errors.append("Pi summary has no event projection")
    else:
        if projection.get("assistant_stop_reasons") != [
            "toolUse",
            "toolUse",
            "toolUse",
            "toolUse",
            "toolUse",
            "stop",
        ]:
            errors.append("assistant did not follow the complete repair lifecycle")
        event_types = projection.get("event_types")
        if not isinstance(event_types, dict) or event_types.get("agent_start") != 1:
            errors.append("deterministic repair unexpectedly used multiple agent cycles")
        if not isinstance(event_types, dict) or event_types.get("agent_settled") != 1:
            errors.append("expected exactly one agent_settled event")

        expected_calls = [
            {
                key: value
                for key, value in item.items()
                if key not in {"is_error", "result_sha256"}
            }
            for item in expected
        ]
        if projection.get("assistant_tool_calls") != expected_calls:
            errors.append("assistant tool calls do not match the scripted repair")

        raw_executions = projection.get("tool_executions")
        if isinstance(raw_executions, list):
            executions = raw_executions
        observed_executions = [
            {key: item.get(key) for key in expected[0]}
            for item in executions
            if isinstance(item, dict)
        ]
        if pi_version == "1.4.0":
            try:
                raw_events = [json.loads(line) for line in pi["stdout_jsonl"].splitlines()]
                raw_tools = {event["toolCallId"]: event["result"] for event in raw_events
                             if event.get("type") == "tool_execution_end"}
                for item in observed_executions:
                    raw = raw_tools[item["tool_call_id"]]
                    if canonical_digest(raw) != item["result_sha256"]:
                        raise ValueError("raw result digest disagrees with summary")
                    if item["tool_name"] == "bash":
                        content = raw["structuredContent"]
                        seconds = content["wall_time_seconds"]
                        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                            raise ValueError("invalid bash wall time")
                        # Preserve raw bytes/digests; only this explicit oracle
                        # comparison masks the measured execution duration.
                        stable = {**raw, "structuredContent": {**content, "wall_time_seconds": 0}}
                        item["result_sha256"] = canonical_digest(stable)
            except (KeyError, TypeError, ValueError) as error:
                errors.append(f"Pi 1.4.0 raw result evidence invalid: {error}")
        if observed_executions != expected:
            errors.append("Pi tool executions do not prove red, repair, then green")

    first_test = executions[2] if len(executions) == len(expected) else {}
    final_test = executions[4] if len(executions) == len(expected) else {}
    first_test_failed = (
        len(executions) == len(expected)
        and isinstance(first_test, dict)
        and first_test.get("tool_call_id") == "hwb-coding-run-failing-tests"
        and first_test.get("is_error") is True
    )
    final_test_passed = (
        len(executions) == len(expected)
        and isinstance(final_test, dict)
        and final_test.get("tool_call_id") == "hwb-coding-run-passing-tests"
        and final_test.get("is_error") is False
    )
    comparison = {
        "schema": "pi-hwb-coding-repair-comparison/v0.1",
        "first_test_failed": first_test_failed,
        "final_test_passed": final_test_passed,
        "changed_paths": changed_paths,
        "invariants_unchanged": all(
            path in before and path in after and before[path] == after[path]
            for path in ("coding_task.md", "test_slugger.py")
        ),
        "event_projection": projection,
        **({"masked_fields": ["bash.result.structuredContent.wall_time_seconds"]}
           if pi_version == "1.4.0" else {}),
    }
    return errors, comparison
