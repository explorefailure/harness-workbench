#!/usr/bin/env python3
"""Capture and judge one throwing ``tool_result`` handler order."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import adapter
import result_failure_oracle


HERE = Path(__file__).resolve().parent
INPUTS = (
    "run_result_failure.sh",
    "result_failure_runner.py",
    "result_failure_oracle.py",
    "result_failure_thrower.ts",
    "result_failure_audit.ts",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant", required=True, choices=("throw-first", "audit-first")
    )
    args = parser.parse_args()
    order = (
        ("result_failure_thrower.ts", "result_failure_audit.ts")
        if args.variant == "throw-first"
        else ("result_failure_audit.ts", "result_failure_thrower.ts")
    )
    try:
        capture = adapter.capture(
            HERE / "composition_adapter_config.json",
            additional_extensions=order,
            additional_inputs=INPUTS,
            evidence_files=({
                "name": "result_failure_order",
                "path": "result-failure-order.jsonl",
                "format": "jsonl",
                "required": True,
                "environment_variable": "PI_HWB_RESULT_FAILURE_PATH",
            },),
        )
        errors, comparison = result_failure_oracle.evaluate(args.variant, capture)
    except (adapter.AdapterError, OSError, KeyError, TypeError) as error:
        print(json.dumps({
            "schema": "pi-hwb-result-failure-run/v0.1",
            "error": str(error),
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "schema": "pi-hwb-result-failure-run/v0.1",
        "variant": args.variant,
        "verdict": {"passed": not errors, "errors": errors},
        "comparison": comparison,
        "adapter": capture,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
