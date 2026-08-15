#!/usr/bin/env python3
"""Capture one extension-composition order."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import adapter
import composition_oracle

HERE = Path(__file__).resolve().parent
INPUTS = ("run_composition.sh", "composition_runner.py", "composition_oracle.py",
    "composition_mutator.ts", "composition_guard.ts")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True, choices=("mutate-first", "guard-first"))
    args = parser.parse_args()
    order = (("composition_mutator.ts", "composition_guard.ts")
             if args.variant == "mutate-first"
             else ("composition_guard.ts", "composition_mutator.ts"))
    capture = adapter.capture(
        HERE / "composition_adapter_config.json",
        additional_extensions=order,
        additional_inputs=INPUTS,
        evidence_files=({"name": "composition", "path": "composition.jsonl",
            "format": "jsonl", "required": True,
            "environment_variable": "PI_HWB_COMPOSITION_PATH"},),
    )
    errors, comparison = composition_oracle.evaluate(args.variant, capture)
    print(json.dumps({"schema": "pi-hwb-composition-run/v0.1", "variant": args.variant,
        "verdict": {"passed": not errors, "errors": errors}, "comparison": comparison,
        "adapter": capture}, sort_keys=True))
    return 0 if not errors else 1

if __name__ == "__main__": raise SystemExit(main())
