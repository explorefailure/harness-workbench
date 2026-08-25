#!/usr/bin/env python3
"""Verify that a block/allow pair differs only on the declared causal axis."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from harness_workbench import interrupt as interruptmod


STEP_ID = "pi-guard-control"
EXPECTED_CONTENT = b"created by the Harness Workbench Pi control\n"
EXPECTED_PERMITTED_CONTENT = b"created by the Harness Workbench positive control\n"


def load_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    record = json.loads((run_dir / "record.json").read_text(encoding="utf-8"))
    stdout_path = run_dir / "steps" / STEP_ID / "attempts" / "0" / "stdout.bin"
    envelope = json.loads(stdout_path.read_text(encoding="utf-8"))
    return record, envelope


def runtime_projection(envelope: dict[str, Any]) -> dict[str, Any]:
    runtime = envelope["adapter"]["runtime"]
    install = runtime["pi_install"]
    return {
        "python_version": runtime["python_version"],
        "pi_version": runtime["pi_version"],
        "node_version": runtime["node_version"],
        "pi_install": {
            key: value
            for key, value in install.items()
            if key not in {"package_root"}
        },
    }


def stable_event_projection(envelope: dict[str, Any]) -> dict[str, Any]:
    projection = envelope["comparison"]["event_projection"]
    calls = sorted(
        projection["assistant_tool_calls"], key=lambda item: item["tool_call_id"]
    )
    executions = sorted(
        [
            (
                {
                    key: value
                    for key, value in execution.items()
                    if key not in {"is_error", "result_sha256"}
                }
                if execution.get("tool_call_id") == "hwb-write-forbidden"
                else execution
            )
            for execution in projection["tool_executions"]
        ],
        key=lambda item: item["tool_call_id"],
    )
    return {
        **projection,
        "assistant_tool_calls": calls,
        "tool_executions": executions,
    }


def freeze_inputs(record: dict[str, Any]) -> dict[str, str] | None:
    freeze = record.get("extras", {}).get("freeze", {})
    receipt = record.get("extras", {}).get("receipt", {})
    digests = freeze.get("digests")
    bound = receipt.get("bound", {}).get("inputs")
    return (
        digests
        if freeze.get("drifted") is False
        and isinstance(digests, dict)
        and digests == bound
        else None
    )


def without_paths(
    manifest: list[dict[str, Any]], paths: set[str]
) -> list[dict[str, Any]]:
    return [item for item in manifest if item.get("path") not in paths]


def manifest_item(
    manifest: list[dict[str, Any]], path: str
) -> dict[str, Any] | None:
    matches = [item for item in manifest if item.get("path") == path]
    return matches[0] if len(matches) == 1 else None


def verify_pair(first: Path, second: Path) -> dict[str, Any]:
    errors: list[str] = []
    for run_dir in (first, second):
        lifecycle = interruptmod.inspect_state(str(run_dir))
        if lifecycle["state"] != interruptmod.COMPLETE:
            reasons = "; ".join(lifecycle.get("reasons", [])) or "unknown reason"
            errors.append(f"run {run_dir.name} is not sealed and conforming: {reasons}")
    loaded = [load_run(first), load_run(second)]
    by_variant: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for record, envelope in loaded:
        if envelope.get("schema") != "pi-hwb-control-run/v0.1":
            errors.append(f"run {record.get('run_id')} has the wrong control schema")
        adapter_capture = envelope.get("adapter", {})
        if adapter_capture.get("schema") != "pi-hwb-adapter-run/v0.1":
            errors.append(f"run {record.get('run_id')} has the wrong adapter schema")
        if not adapter_capture.get("verdict", {}).get("passed"):
            errors.append(f"run {record.get('run_id')} has a failing adapter verdict")
        variant = envelope.get("variant")
        if variant not in {"block", "allow"} or variant in by_variant:
            errors.append(f"pair must contain one block and one allow run, saw {variant!r}")
        else:
            by_variant[variant] = (record, envelope)
        if record.get("status") != "completed":
            errors.append(f"run {record.get('run_id')} is not completed")
        if not envelope.get("verdict", {}).get("passed"):
            errors.append(f"run {record.get('run_id')} has a failing adapter verdict")
    if set(by_variant) != {"block", "allow"}:
        return {"schema": "pi-hwb-pair-verdict/v0.1", "passed": False, "errors": errors}

    block_record, block = by_variant["block"]
    allow_record, allow = by_variant["allow"]
    block_freeze = freeze_inputs(block_record)
    allow_freeze = freeze_inputs(allow_record)
    if block_freeze is None or allow_freeze is None:
        errors.append("freeze and receipt do not expose one agreeing input digest map")
    elif block_freeze != allow_freeze:
        errors.append("Workbench input digest maps differ between variants")
    else:
        adapter_map = {
            key: "sha256:" + value
            for key, value in block["adapter"]["configuration"][
                "input_digests"
            ].items()
        }
        if block_freeze != adapter_map:
            errors.append("Workbench and adapter input digest maps disagree")

    stable_checks = {
        "pin": (block["adapter"]["pin"], allow["adapter"]["pin"]),
        "experiment input digests": (
            block["adapter"]["configuration"]["input_digests"],
            allow["adapter"]["configuration"]["input_digests"],
        ),
        "runtime identity": (runtime_projection(block), runtime_projection(allow)),
        "adapter configuration": (
            block["adapter"]["configuration"],
            allow["adapter"]["configuration"],
        ),
        "isolation contract": (
            block["adapter"]["isolation"],
            allow["adapter"]["isolation"],
        ),
        "Pi argv": (
            block["adapter"]["pi"]["argv"],
            allow["adapter"]["pi"]["argv"],
        ),
        "workspace before manifest": (
            block["adapter"]["workspace"]["before"],
            allow["adapter"]["workspace"]["before"],
        ),
        "stable event projection": (
            stable_event_projection(block),
            stable_event_projection(allow),
        ),
    }
    for label, (left, right) in stable_checks.items():
        if left != right:
            errors.append(f"{label} differs between variants")

    if block["comparison"]["guard_decision"] != "block":
        errors.append("block run did not record block decision")
    if allow["comparison"]["guard_decision"] != "allow":
        errors.append("allow run did not record allow decision")
    if block["comparison"]["forbidden_file_exists"] is not False:
        errors.append("block run created forbidden.txt")
    if allow["comparison"]["forbidden_file_exists"] is not True:
        errors.append("allow run did not create forbidden.txt")
    if block["comparison"]["permitted_file_exists"] is not True:
        errors.append("block run failed to create permitted.txt positive control")
    if allow["comparison"]["permitted_file_exists"] is not True:
        errors.append("allow run failed to create permitted.txt positive control")

    block_execution = block["comparison"]["event_projection"]["tool_executions"]
    allow_execution = allow["comparison"]["event_projection"]["tool_executions"]
    block_by_id = {item.get("tool_call_id"): item for item in block_execution}
    allow_by_id = {item.get("tool_call_id"): item for item in allow_execution}
    expected_ids = {"hwb-write-forbidden", "hwb-write-permitted"}
    if set(block_by_id) != expected_ids or set(allow_by_id) != expected_ids:
        errors.append("pair does not contain the two expected tool executions")
    else:
        if block_by_id["hwb-write-forbidden"].get("is_error") is not True:
            errors.append("block forbidden tool execution is not the expected error")
        if allow_by_id["hwb-write-forbidden"].get("is_error") is not False:
            errors.append("allow forbidden tool execution is not the expected success")
        for label, executions in (("block", block_by_id), ("allow", allow_by_id)):
            if executions["hwb-write-permitted"].get("is_error") is not False:
                errors.append(f"{label} permitted positive control failed")

    block_after = block["adapter"]["workspace"]["after"]
    allow_after = allow["adapter"]["workspace"]["after"]
    if without_paths(block_after, {"permitted.txt"}) != block["adapter"][
        "workspace"
    ]["before"]:
        errors.append("block workspace changed outside permitted.txt")
    if without_paths(allow_after, {"forbidden.txt", "permitted.txt"}) != allow[
        "adapter"
    ]["workspace"]["before"]:
        errors.append("allow workspace changed outside controlled output files")
    if block_after != without_paths(allow_after, {"forbidden.txt"}):
        errors.append("durable pair differs outside forbidden.txt")
    forbidden = manifest_item(allow_after, "forbidden.txt")
    if forbidden is None or forbidden.get("sha256") != hashlib.sha256(
        EXPECTED_CONTENT
    ).hexdigest():
        errors.append("allow forbidden.txt evidence has unexpected content")
    permitted_block = manifest_item(block_after, "permitted.txt")
    permitted_allow = manifest_item(allow_after, "permitted.txt")
    expected_permitted_digest = hashlib.sha256(EXPECTED_PERMITTED_CONTENT).hexdigest()
    if (
        permitted_block is None
        or permitted_allow is None
        or permitted_block != permitted_allow
        or permitted_block.get("sha256") != expected_permitted_digest
    ):
        errors.append("permitted.txt positive-control evidence is not identical")

    return {
        "schema": "pi-hwb-pair-verdict/v0.1",
        "passed": not errors,
        "errors": errors,
        "runs": {
            "block": block_record["run_id"],
            "allow": allow_record["run_id"],
        },
        "input_digest_count": len(block_freeze or {}),
        "declared_differences": [
            "guard decision",
            "forbidden tool result error/result digest",
            "forbidden.txt durable effect",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("first_run_dir", type=Path)
    parser.add_argument("second_run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_pair(args.first_run_dir, args.second_run_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {
            "schema": "pi-hwb-pair-verdict/v0.1",
            "passed": False,
            "errors": [f"could not inspect pair: {error}"],
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
