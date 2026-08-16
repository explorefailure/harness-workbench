#!/usr/bin/env python3
"""Validate and compare the five shared-contract candidate envelopes."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any


SUBJECTS = {"claude", "codex", "deepseek", "hermes", "pi"}


def load_source(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Every draw of the step, not the first one.

    This read `attempts/0` and stopped. That was correct only for as long as
    nothing sampled: attach `sample` and the comparison would keep reading one
    draw of three and reporting it as the result, which is the precise failure
    `sample` exists to prevent -- "one draw is not a measurement". Attempts are
    numbered directories, so they are collected in numeric order and all of
    them are returned.
    """
    if path.is_file():
        return None, [json.loads(path.read_text(encoding="utf-8"))]
    record = json.loads((path / "record.json").read_text(encoding="utf-8"))
    steps = record.get("steps", [])
    if len(steps) != 1 or not isinstance(steps[0].get("id"), str):
        raise ValueError(f"{path} does not contain exactly one recorded step")
    attempts = path / "steps" / steps[0]["id"] / "attempts"
    numbered = sorted(
        (int(d.name) for d in attempts.iterdir() if d.name.isdigit()),
    ) if attempts.is_dir() else []
    if not numbered:
        raise ValueError(f"{path} recorded no attempts")
    return record, [
        json.loads((attempts / str(n) / "stdout.bin").read_text(encoding="utf-8"))
        for n in numbered
    ]


def _agreed(values: Any) -> Any:
    """One value if every draw agreed, otherwise the disagreement itself.

    Returning the first and moving on would let a subject that changed its
    evidence surface between draws be summarised as though it had not.
    """
    distinct = sorted({json.dumps(value, sort_keys=True) for value in values})
    if len(distinct) == 1:
        return json.loads(distinct[0])
    return {"disagreed": [json.loads(value) for value in distinct]}


def verify_capture(label: str, capture: dict[str, Any], errors: list[str]) -> None:
    for stream in ("stdout", "stderr", "sidecar"):
        item = capture.get(stream)
        if not isinstance(item, dict):
            errors.append(f"{label} has no {stream} capture")
            continue
        # Whatever the capture itself complained about, reported as its own
        # cause. These used to be invisible here: a sidecar refused for being
        # oversize stores no bytes, so the only error this function produced
        # was "not valid base64" -- which names the symptom and hides the
        # reason -- and a required sidecar that was never created produced no
        # error at all, because empty bytes digest perfectly well.
        for complaint in item.get("errors") or []:
            errors.append(f"{label} {stream}: {complaint}")
        if item.get("exists") is False and item.get("errors"):
            # Already stated by the capture, and there are no bytes to check.
            continue
        encoded = item.get("base64")
        if encoded is None:
            # Deliberate absence, not corruption: the capture declined to store
            # these bytes and said why above. Checking a digest against nothing
            # would add a second, misleading complaint.
            continue
        try:
            raw = base64.b64decode(encoded, validate=True)
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
        None, "timeout", "stdout_limit", "stderr_limit", "signalled"
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
        record, outers = load_source(path)
        subjects_seen = {outer.get("subject") for outer in outers}
        if len(subjects_seen) != 1:
            errors.append(f"{path} mixes subjects across draws: {sorted(subjects_seen)}")
            continue
        subject = subjects_seen.pop()
        if subject not in SUBJECTS or subject in by_subject:
            errors.append(f"{path} has an unexpected or duplicate subject: {subject!r}")
            continue
        adapters_for_subject: list[dict[str, Any]] = []
        for draw, outer in enumerate(outers):
            # Every draw carries the whole contract. A sampled subject that
            # satisfies the evidence contract on two draws of three has not
            # satisfied it -- the shape of the evidence is not the thing that
            # is allowed to vary between draws. What the model DID may vary,
            # and that is reported below rather than judged here.
            label = subject if len(outers) == 1 else f"{subject} draw {draw}"
            adapter = outer.get("adapter")
            if outer.get("schema") != "cross-harness-experiment-run/v0.1":
                errors.append(f"{label} has the wrong outer schema")
                continue
            if not isinstance(adapter, dict) or adapter.get("schema") != (
                "cross-harness-adapter-run/v0.1"
            ):
                errors.append(f"{label} has the wrong adapter schema")
                continue
            adapters_for_subject.append(adapter)
            verify_capture(label, adapter.get("capture", {}), errors)
            verify_record(label, record, adapter, errors)
            for field in (
                "subject",
                "request",
                "apparatus",
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
                    errors.append(f"{label} adapter is missing {field}")
        if not adapters_for_subject:
            continue
        by_subject[subject] = (record, adapters_for_subject)

    if set(by_subject) != SUBJECTS:
        errors.append(
            "comparison requires exactly Claude, Codex, DeepSeek, Hermes, and Pi"
        )

    every_adapter = [
        adapter for _, adapters in by_subject.values() for adapter in adapters
    ]
    if by_subject:
        # Across every draw of every subject, not one representative each: a
        # sampled run that changed prompt or apparatus midway is exactly what
        # these sets exist to catch.
        prompts = {
            adapter["request"].get("prompt_sha256") for adapter in every_adapter
        }
        inputs = {
            json.dumps(adapter["request"].get("input_digests"), sort_keys=True)
            for adapter in every_adapter
        }
        expected_effects = {
            adapter["outcome"].get("expected_sha256") for adapter in every_adapter
        }
        apparatus = {
            json.dumps(adapter.get("apparatus"), sort_keys=True)
            for adapter in every_adapter
        }
        if len(prompts) != 1:
            errors.append("subjects did not receive the same prompt bytes")
        if len(inputs) != 1:
            errors.append("subjects did not bind the same experiment inputs")
        if len(expected_effects) != 1:
            errors.append("subjects did not use the same outcome oracle")
        if len(apparatus) != 1:
            # The capture primitive is imported from the installed package, so
            # it is the one input a spec's `inputs` cannot bind. Runs
            # measured by different builds of it are not a comparison, and
            # without this check nothing else in the pipeline would say so.
            errors.append("subjects were not captured by the same apparatus")

    subjects = {}
    for subject, (_, adapters) in sorted(by_subject.items()):
        first = adapters[0]
        subjects[subject] = {
            "draws": len(adapters),
            # Counts, not a rate. A rate over three draws reads as a
            # probability and is not one, and reduction belongs to whoever is
            # asking the question -- never to capture.
            "adapter_passed": sum(
                1 for a in adapters if a["verdict"].get("passed")
            ),
            "outcome_passed": sum(
                1 for a in adapters if a["outcome"].get("passed")
            ),
            "timed_out": sum(
                1 for a in adapters if a["capture"].get("timed_out")
            ),
            # Evidence-shape facts, which the contract requires to be identical
            # on every draw. Reported once, and reported as disagreement rather
            # than silently taking the first, if a subject ever varies them.
            "acquisition": _agreed(a["lifecycle"].get("acquisition") for a in adapters),
            "completeness": _agreed(
                a["lifecycle"].get("completeness") for a in adapters
            ),
            "tool_attempts": [
                len(a["lifecycle"].get("tool_executions", [])) for a in adapters
            ],
            "capabilities": first["capabilities"],
        }
    return {
        "schema": "cross-harness-contract-comparison/v0.1",
        "contract_passed": not errors,
        "errors": errors,
        "subjects": subjects,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs=len(SUBJECTS), type=Path)
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
