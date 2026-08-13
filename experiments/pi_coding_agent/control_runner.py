#!/usr/bin/env python3
"""Run the forbidden-write control through the reusable Pi adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import adapter
import control_oracle


HERE = Path(__file__).resolve().parent
EXPERIMENT_INPUTS = (
    "run_adapter.sh",
    "adapter_config.json",
    "adapter.py",
    "control_runner.py",
    "control_oracle.py",
    "normalizer.py",
    "scripted_provider.ts",
    "guard_extension.ts",
    "pin.json",
    "task.md",
    "verify_pair.py",
    "fixture/seed.txt",
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--variant", required=True, choices=("block", "allow"))
    result.add_argument("--pi", default="pi")
    result.add_argument("--timeout", type=float, default=20.0)
    result.add_argument("--workspace-parent")
    return result


def failure_envelope(variant: str, message: str) -> int:
    print(
        json.dumps(
            {
                "schema": "pi-hwb-control-run/v0.1",
                "variant": variant,
                "error": message,
            },
            sort_keys=True,
        )
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if sys.version_info < (3, 11):
        return failure_envelope(args.variant, "Python 3.11 or newer is required")
    try:
        configured_inputs = set(adapter.load_config(HERE / "adapter_config.json")["inputs"])
        capture = adapter.capture(
            HERE / "adapter_config.json",
            pi_name=args.pi,
            timeout=args.timeout,
            workspace_parent=args.workspace_parent,
            environment={"PI_HWB_GUARD_MODE": args.variant},
            additional_extensions=("guard_extension.ts",),
            additional_inputs=tuple(
                item
                for item in EXPERIMENT_INPUTS
                if item not in configured_inputs
            ),
            evidence_files=(
                {
                    "name": "guard_decisions",
                    "path": "guard-decisions.jsonl",
                    "format": "jsonl",
                    "required": True,
                    "environment_variable": "PI_HWB_DECISION_PATH",
                },
            ),
        )
        errors, comparison = control_oracle.evaluate(args.variant, capture)
    except (adapter.AdapterError, OSError, KeyError, TypeError) as error:
        return failure_envelope(args.variant, str(error))

    envelope = {
        "schema": "pi-hwb-control-run/v0.1",
        "variant": args.variant,
        "verdict": {"passed": not errors, "errors": errors},
        "adapter": capture,
        "comparison": comparison,
    }
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
