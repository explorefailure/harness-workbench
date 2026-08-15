#!/usr/bin/env python3
"""Run one external harness and apply its independent effect oracle."""
from __future__ import annotations

import argparse
import json

import adapters


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True, choices=("claude", "codex", "hermes"))
    args = parser.parse_args()
    try:
        capture = adapters.capture(args.subject)
    except (adapters.AdapterError, OSError, TypeError, ValueError) as error:
        print(json.dumps({
            "schema": "cross-harness-experiment-run/v0.1",
            "subject": args.subject,
            "error": str(error),
        }, sort_keys=True))
        return 2
    passed = capture["verdict"]["passed"] and capture["outcome"]["passed"]
    print(json.dumps({
        "schema": "cross-harness-experiment-run/v0.1",
        "subject": args.subject,
        "verdict": {
            "passed": passed,
            "adapter_passed": capture["verdict"]["passed"],
            "outcome_passed": capture["outcome"]["passed"],
        },
        "adapter": capture,
    }, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
