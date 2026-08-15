#!/usr/bin/env python3
"""Capture one arm of the Pi plan-mode control."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import adapter
import plan_oracle


HERE = Path(__file__).resolve().parent
PLAN_INPUTS = (
    "run_plan_mode.sh",
    "plan_adapter_config.json",
    "adapter.py",
    "normalizer.py",
    "plan_runner.py",
    "plan_oracle.py",
    "verify_plan_pair.py",
    "plan_mode_extension.ts",
    "plan_provider.ts",
    "pin.json",
    "plan_task.md",
    "plan_fixture/seed.txt",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=("plan", "act"))
    parser.add_argument("--workspace-parent")
    args = parser.parse_args()
    try:
        config_path = HERE / "plan_adapter_config.json"
        configured = set(adapter.load_config(config_path)["inputs"])
        capture = adapter.capture(
            config_path,
            workspace_parent=args.workspace_parent,
            environment={"PI_HWB_PLAN_MODE": args.variant},
            additional_inputs=tuple(item for item in PLAN_INPUTS if item not in configured),
            evidence_files=({
                "name": "plan_decisions",
                "path": "plan-decisions.jsonl",
                "format": "jsonl",
                "required": True,
                "environment_variable": "PI_HWB_PLAN_DECISION_PATH",
            },),
        )
        errors, comparison = plan_oracle.evaluate(args.variant, capture)
    except (adapter.AdapterError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"schema": "pi-hwb-plan-run/v0.1", "error": str(error)}, sort_keys=True))
        return 2
    envelope = {
        "schema": "pi-hwb-plan-run/v0.1",
        "variant": args.variant,
        "verdict": {"passed": not errors, "errors": errors},
        "adapter": capture,
        "comparison": comparison,
    }
    print(json.dumps(envelope, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
