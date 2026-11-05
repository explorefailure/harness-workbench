#!/usr/bin/env python3
"""Run the deterministic coding repair and apply its outcome oracle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import adapter
import coding_oracle


HERE = Path(__file__).resolve().parent
CODING_INPUTS = (
    "run_coding_adapter.sh",
    "coding_adapter_config.json",
    "adapter.py",
    "coding_runner.py",
    "coding_oracle.py",
    "normalizer.py",
    "coding_provider.ts",
    "pin.json",
    "coding_task.md",
    "coding_fixture/slugger.py",
    "coding_fixture/test_slugger.py",
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--pi", default="pi")
    result.add_argument("--timeout", type=float, default=20.0)
    result.add_argument("--workspace-parent")
    return result


def failure_envelope(message: str) -> int:
    print(
        json.dumps(
            {"schema": "pi-hwb-coding-repair-run/v0.1", "error": message},
            sort_keys=True,
        )
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if sys.version_info < (3, 11):
        return failure_envelope("Python 3.11 or newer is required")
    try:
        config_path = HERE / "coding_adapter_config.json"
        configured_inputs = set(adapter.load_config(config_path)["inputs"])
        capture = adapter.capture(
            config_path,
            pi_name=args.pi,
            timeout=args.timeout,
            workspace_parent=args.workspace_parent,
            additional_inputs=tuple(
                item for item in CODING_INPUTS if item not in configured_inputs
            ),
        )
        errors, comparison = coding_oracle.evaluate(capture)
    except (adapter.AdapterError, OSError, KeyError, TypeError) as error:
        return failure_envelope(str(error))

    envelope = {
        "schema": "pi-hwb-coding-repair-run/v0.1",
        "verdict": {"passed": not errors, "errors": errors},
        "adapter": capture,
        "comparison": comparison,
    }
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
