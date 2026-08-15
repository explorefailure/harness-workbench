#!/usr/bin/env python3
"""Capture and judge one conflicting-policy handler order."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import adapter
import policy_order_oracle


HERE = Path(__file__).resolve().parent
INPUTS = (
    "run_policy_order.sh",
    "policy_order_runner.py",
    "policy_order_oracle.py",
    "policy_blocker.ts",
    "policy_allower.ts",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant", required=True, choices=("block-first", "allow-first")
    )
    args = parser.parse_args()
    order = (
        ("policy_blocker.ts", "policy_allower.ts")
        if args.variant == "block-first"
        else ("policy_allower.ts", "policy_blocker.ts")
    )
    try:
        capture = adapter.capture(
            HERE / "composition_adapter_config.json",
            additional_extensions=order,
            additional_inputs=INPUTS,
            evidence_files=({
                "name": "policy_order",
                "path": "policy-order.jsonl",
                "format": "jsonl",
                "required": True,
                "environment_variable": "PI_HWB_POLICY_PATH",
            },),
        )
        errors, comparison = policy_order_oracle.evaluate(args.variant, capture)
    except (adapter.AdapterError, OSError, KeyError, TypeError) as error:
        print(json.dumps({
            "schema": "pi-hwb-policy-order-run/v0.1",
            "error": str(error),
        }, sort_keys=True))
        return 2
    print(json.dumps({
        "schema": "pi-hwb-policy-order-run/v0.1",
        "variant": args.variant,
        "verdict": {"passed": not errors, "errors": errors},
        "comparison": comparison,
        "adapter": capture,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
