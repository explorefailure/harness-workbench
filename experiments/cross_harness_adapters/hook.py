#!/usr/bin/env python3
"""Record one Hermes shell-hook payload without changing its decision."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    evidence = os.environ.get("HWB_HERMES_HOOK_EVIDENCE")
    if not evidence:
        print("HWB_HERMES_HOOK_EVIDENCE is required", file=sys.stderr)
        return 2
    try:
        limit = int(os.environ.get("HWB_HERMES_HOOK_MAX_BYTES", "524288"))
    except ValueError:
        print("invalid HWB_HERMES_HOOK_MAX_BYTES", file=sys.stderr)
        return 2
    if limit <= 0:
        print("HWB_HERMES_HOOK_MAX_BYTES must be positive", file=sys.stderr)
        return 2
    try:
        redactions = json.loads(os.environ.get("HWB_REDACT_VALUES_JSON", "[]"))
    except json.JSONDecodeError:
        print("invalid HWB_REDACT_VALUES_JSON", file=sys.stderr)
        return 2
    if not isinstance(redactions, list) or not all(
        isinstance(value, str) for value in redactions
    ):
        print("HWB_REDACT_VALUES_JSON must be a string list", file=sys.stderr)
        return 2

    def scrub(value):
        if isinstance(value, str):
            for secret in sorted(redactions, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    payload = scrub(json.load(sys.stdin))
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    evidence_path = Path(evidence)
    current = evidence_path.stat().st_size if evidence_path.exists() else 0
    if current + len(encoded) > limit:
        print("Hermes hook evidence limit exceeded", file=sys.stderr)
        return 3
    with evidence_path.open("ab") as stream:
        stream.write(encoded)
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
