#!/usr/bin/env python3
"""Capture and judge one result-rewriting handler order."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import adapter
import result_rewrite_oracle


HERE = Path(__file__).resolve().parent
INPUTS = (
    "run_result_rewrite.sh",
    "result_rewrite_runner.py",
    "result_rewrite_oracle.py",
    "result_masker.ts",
    "result_restorer.ts",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant", required=True, choices=("mask-first", "restore-first")
    )
    args = parser.parse_args()
    order = (
        ("result_masker.ts", "result_restorer.ts")
        if args.variant == "mask-first"
        else ("result_restorer.ts", "result_masker.ts")
    )
    try:
        capture = adapter.capture(
            HERE / "composition_adapter_config.json",
            additional_extensions=order,
            additional_inputs=INPUTS,
            evidence_files=({
                "name": "result_rewrite_order",
                "path": "result-rewrite-order.jsonl",
                "format": "jsonl",
                "required": True,
                "environment_variable": "PI_HWB_RESULT_REWRITE_PATH",
            },),
        )
        errors, comparison = result_rewrite_oracle.evaluate(args.variant, capture)
    except (adapter.AdapterError, OSError, KeyError, TypeError) as error:
        print(json.dumps({
            "schema": "pi-hwb-result-rewrite-run/v0.1",
            "error": str(error),
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "schema": "pi-hwb-result-rewrite-run/v0.1",
        "variant": args.variant,
        "verdict": {"passed": not errors, "errors": errors},
        "comparison": comparison,
        "adapter": capture,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
