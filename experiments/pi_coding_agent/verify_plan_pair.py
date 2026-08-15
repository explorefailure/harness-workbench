#!/usr/bin/env python3
"""Verify the sealed Pi plan/action confirmation pair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness_workbench import interrupt as interruptmod


STEP_ID = "pi-plan-mode-control"


def _load(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    record = json.loads((path / "record.json").read_text(encoding="utf-8"))
    stdout = path / "steps" / STEP_ID / "attempts" / "0" / "stdout.bin"
    return record, json.loads(stdout.read_text(encoding="utf-8"))


def _bound_inputs(record: dict[str, Any]) -> dict[str, str] | None:
    freeze = record.get("extras", {}).get("freeze", {})
    receipt = record.get("extras", {}).get("receipt", {})
    digests = freeze.get("digests")
    bound = receipt.get("bound", {}).get("inputs")
    return digests if freeze.get("drifted") is False and digests == bound else None


def verify(first: Path, second: Path) -> dict[str, Any]:
    errors: list[str] = []
    by_variant = {}
    for path in (first, second):
        state = interruptmod.inspect_state(str(path))
        if state["state"] != interruptmod.COMPLETE:
            errors.append(f"run {path.name} is not sealed and conforming")
        record, envelope = _load(path)
        variant = envelope.get("variant")
        if variant not in {"plan", "act"} or variant in by_variant:
            errors.append(f"pair has invalid variant {variant!r}")
            continue
        by_variant[variant] = (record, envelope)
        if envelope.get("schema") != "pi-hwb-plan-run/v0.1":
            errors.append(f"{variant} run has the wrong envelope schema")
        if envelope.get("verdict", {}).get("passed") is not True:
            errors.append(f"{variant} outcome oracle failed")
        if envelope.get("adapter", {}).get("verdict", {}).get("passed") is not True:
            errors.append(f"{variant} adapter failed")
    if set(by_variant) != {"plan", "act"}:
        return {"schema": "pi-hwb-plan-pair-verdict/v0.1", "passed": False, "errors": errors}

    plan_record, plan = by_variant["plan"]
    act_record, act = by_variant["act"]
    plan_inputs = _bound_inputs(plan_record)
    act_inputs = _bound_inputs(act_record)
    if plan_inputs is None or act_inputs is None or plan_inputs != act_inputs:
        errors.append("pair does not share one frozen and receipted input map")
    else:
        adapter_inputs = {
            name: "sha256:" + digest
            for name, digest in plan["adapter"]["configuration"]["input_digests"].items()
        }
        if plan_inputs != adapter_inputs:
            errors.append("Workbench and adapter input maps disagree")
    for label, left, right in (
        ("pin", plan["adapter"]["pin"], act["adapter"]["pin"]),
        ("runtime", plan["adapter"]["runtime"], act["adapter"]["runtime"]),
        (
            "configuration",
            plan["adapter"]["configuration"],
            act["adapter"]["configuration"],
        ),
        ("workspace before", plan["adapter"]["workspace"]["before"], act["adapter"]["workspace"]["before"]),
    ):
        if left != right:
            errors.append(f"{label} differs between variants")

    expected = {
        "plan": (True, True, False, False),
        "act": (True, True, True, True),
    }
    keys = (
        "read_succeeded",
        "safe_bash_succeeded",
        "direct_write_effect",
        "shell_write_effect",
    )
    for variant, envelope in (("plan", plan), ("act", act)):
        observed = tuple(envelope["comparison"].get(key) for key in keys)
        if observed != expected[variant]:
            errors.append(f"{variant} comparison was {observed!r}")
    return {
        "schema": "pi-hwb-plan-pair-verdict/v0.1",
        "passed": not errors,
        "errors": errors,
        "runs": {"plan": plan_record["run_id"], "act": act_record["run_id"]},
        "input_digest_count": len(plan_inputs or {}),
        "learned_boundary": "inactive-tool rejection and hook-policy rejection are distinct",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.first, args.second)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {
            "schema": "pi-hwb-plan-pair-verdict/v0.1",
            "passed": False,
            "errors": [f"could not inspect pair: {error}"],
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
