#!/usr/bin/env python3
"""Capture and judge one real-failure result-rewrite variant."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import adapter
import failure_rewrite_oracle


HERE = Path(__file__).resolve().parent
INPUTS = (
    "run_failure_rewrite.sh",
    "failure_rewrite_runner.py",
    "failure_rewrite_oracle.py",
    "failure_rewrite_extension.ts",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=("honest", "falsified"))
    args = parser.parse_args()
    try:
        capture = adapter.capture(
            HERE / "failure_rewrite_adapter_config.json",
            environment={"PI_HWB_FAILURE_REWRITE_MODE": args.variant},
            additional_extensions=("failure_rewrite_extension.ts",),
            additional_inputs=INPUTS,
            evidence_files=({
                "name": "failure_rewrite",
                "path": "failure-rewrite.jsonl",
                "format": "jsonl",
                "required": True,
                "environment_variable": "PI_HWB_FAILURE_REWRITE_PATH",
            },),
        )
        errors, comparison = failure_rewrite_oracle.evaluate(args.variant, capture)
    except (adapter.AdapterError, OSError, KeyError, TypeError) as error:
        print(json.dumps({
            "schema": "pi-hwb-failure-rewrite-run/v0.1",
            "error": str(error),
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "schema": "pi-hwb-failure-rewrite-run/v0.1",
        "variant": args.variant,
        "verdict": {"passed": not errors, "errors": errors},
        "comparison": comparison,
        "adapter": capture,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
