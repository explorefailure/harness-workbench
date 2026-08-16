#!/usr/bin/env python3
"""Validate and compare four shared-contract candidate envelopes."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any


SUBJECTS = {"claude", "codex", "deepseek", "hermes"}


def load_source(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if path.is_file():
        envelope = json.loads(path.read_text(encoding="utf-8"))
        return None, envelope
    record = json.loads((path / "record.json").read_text(encoding="utf-8"))
    steps = record.get("steps", [])
    if len(steps) != 1 or not isinstance(steps[0].get("id"), str):
        raise ValueError(f"{path} does not contain exactly one recorded step")
    stdout = path / "steps" / steps[0]["id"] / "attempts" / "0" / "stdout.bin"
    return record, json.loads(stdout.read_text(encoding="utf-8"))


def verify_capture(label: str, capture: dict[str, Any], errors: list[str]) -> None:
    for stream in ("stdout", "stderr", "sidecar"):
        item = capture.get(stream)
        if not isinstance(item, dict):
            errors.append(f"{label} has no {stream} capture")
            continue
        try:
            raw = base64.b64decode(item.get("base64", ""), validate=True)
        except (ValueError, TypeError):
            errors.append(f"{label} {stream} is not valid base64")
            continue
        if len(raw) != item.get("bytes"):
            errors.append(f"{label} {stream} byte count disagrees")
        if hashlib.sha256(raw).hexdigest() != item.get("sha256"):
            errors.append(f"{label} {stream} digest disagrees")
        if not isinstance(item.get("source_bytes"), int) or item["source_bytes"] < 0:
            errors.append(f"{label} {stream} has invalid source byte count")
        if (
            not isinstance(item.get("redaction_count"), int)
            or item["redaction_count"] < 0
        ):
            errors.append(f"{label} {stream} has invalid redaction count")
    limits = capture.get("limits")
    if (
        not isinstance(limits, dict)
        or set(limits) != {
            "stdout_bytes", "stderr_bytes", "sidecar_bytes"
        }
        or not all(isinstance(value, int) and value > 0 for value in limits.values())
    ):
        errors.append(f"{label} has invalid capture limits")
    overflow = capture.get("overflow")
    if not isinstance(overflow, dict) or set(overflow) != {
        "stdout", "stderr", "sidecar"
    } or not all(isinstance(value, bool) for value in overflow.values()):
        errors.append(f"{label} has invalid overflow evidence")
    overflow_map = overflow if isinstance(overflow, dict) else {}
    if capture.get("termination_reason") not in {
        None, "timeout", "stdout_limit", "stderr_limit"
    }:
        errors.append(f"{label} has an invalid termination reason")
    if not isinstance(capture.get("returncode"), int):
        errors.append(f"{label} has an invalid return code")
    if capture.get("timed_out") is not (
        capture.get("termination_reason") == "timeout"
    ):
        errors.append(f"{label} timeout flag disagrees with termination reason")
    if capture.get("termination_reason") == "stdout_limit" and not overflow_map.get(
        "stdout", False
    ):
        errors.append(f"{label} stdout limit reason lacks overflow evidence")
    if capture.get("termination_reason") == "stderr_limit" and not overflow_map.get(
        "stderr", False
    ):
        errors.append(f"{label} stderr limit reason lacks overflow evidence")


def verify_record(
    label: str,
    record: dict[str, Any] | None,
    adapter: dict[str, Any],
    errors: list[str],
) -> None:
    if record is None:
        return
    freeze = record.get("extras", {}).get("freeze", {})
    receipt = record.get("extras", {}).get("receipt", {})
    digests = freeze.get("digests")
    bound = receipt.get("bound", {}).get("inputs")
    expected = {
        name: "sha256:" + digest
        for name, digest in adapter.get("request", {}).get(
            "input_digests", {}
        ).items()
    }
    if freeze.get("drifted") is not False:
        errors.append(f"{label} Workbench freeze reports drift")
    if not isinstance(digests, dict) or digests != bound or digests != expected:
        errors.append(f"{label} freeze, receipt, and adapter input maps disagree")


def compare(paths: list[Path]) -> dict[str, Any]:
    errors: list[str] = []
    by_subject: dict[str, tuple[dict[str, Any] | None, dict[str, Any]]] = {}
    for path in paths:
        record, outer = load_source(path)
        adapter = outer.get("adapter")
        subject = outer.get("subject")
        if outer.get("schema") != "cross-harness-experiment-run/v0.1":
            errors.append(f"{path} has the wrong outer schema")
            continue
        if subject not in SUBJECTS or subject in by_subject:
            errors.append(f"{path} has an unexpected or duplicate subject: {subject!r}")
            continue
        if not isinstance(adapter, dict) or adapter.get("schema") != (
            "cross-harness-adapter-run/v0.1"
        ):
            errors.append(f"{subject} has the wrong adapter schema")
            continue
        by_subject[subject] = (record, adapter)
        verify_capture(subject, adapter.get("capture", {}), errors)
        verify_record(subject, record, adapter, errors)
        for field in (
            "subject",
            "request",
            "capabilities",
            "invocation",
            "isolation",
            "capture",
            "lifecycle",
            "workspace",
            "verdict",
            "outcome",
        ):
            if field not in adapter:
                errors.append(f"{subject} adapter is missing {field}")

    if set(by_subject) != SUBJECTS:
        errors.append(
            "comparison requires exactly Claude, Codex, DeepSeek, and Hermes"
        )

    if by_subject:
        prompts = {
            adapter["request"].get("prompt_sha256")
            for _, adapter in by_subject.values()
        }
        inputs = {
            json.dumps(adapter["request"].get("input_digests"), sort_keys=True)
            for _, adapter in by_subject.values()
        }
        expected_effects = {
            adapter["outcome"].get("expected_sha256")
            for _, adapter in by_subject.values()
        }
        if len(prompts) != 1:
            errors.append("subjects did not receive the same prompt bytes")
        if len(inputs) != 1:
            errors.append("subjects did not bind the same experiment inputs")
        if len(expected_effects) != 1:
            errors.append("subjects did not use the same outcome oracle")

    subjects = {}
    for subject, (_, adapter) in sorted(by_subject.items()):
        lifecycle = adapter["lifecycle"]
        subjects[subject] = {
            "adapter_passed": adapter["verdict"].get("passed"),
            "outcome_passed": adapter["outcome"].get("passed"),
            "timed_out": adapter["capture"].get("timed_out"),
            "acquisition": lifecycle.get("acquisition"),
            "completeness": lifecycle.get("completeness"),
            "tool_attempts": len(lifecycle.get("tool_executions", [])),
            "capabilities": adapter["capabilities"],
        }
    return {
        "schema": "cross-harness-contract-comparison/v0.1",
        "contract_passed": not errors,
        "errors": errors,
        "subjects": subjects,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs=4, type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.sources)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        result = {
            "schema": "cross-harness-contract-comparison/v0.1",
            "contract_passed": False,
            "errors": [str(error)],
            "subjects": {},
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["contract_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
