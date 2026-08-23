#!/usr/bin/env python3
"""Record one Hermes shell-hook payload without changing its decision."""
from __future__ import annotations

import fcntl
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
        input_limit = int(
            os.environ.get("HWB_HERMES_HOOK_INPUT_MAX_BYTES", "262144")
        )
    except ValueError:
        print("invalid HWB_HERMES_HOOK_INPUT_MAX_BYTES", file=sys.stderr)
        return 2
    if input_limit <= 0:
        print("HWB_HERMES_HOOK_INPUT_MAX_BYTES must be positive", file=sys.stderr)
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

    raw = sys.stdin.buffer.read(input_limit + 1)
    if len(raw) > input_limit:
        print("Hermes hook input limit exceeded", file=sys.stderr)
        return 3
    try:
        payload = scrub(json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        print(f"invalid Hermes hook payload: {error}", file=sys.stderr)
        return 2
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    evidence_path = Path(evidence)
    descriptor = os.open(
        evidence_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    with os.fdopen(descriptor, "ab") as stream:
        # Hermes may dispatch independent hooks concurrently.  The cap check
        # and append therefore form one cross-process critical section, not a
        # stat followed later by an append. `flock` is available on both
        # supported host platforms (Linux and macOS).
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        current = os.fstat(stream.fileno()).st_size
        if current + len(encoded) > limit:
            print("Hermes hook evidence limit exceeded", file=sys.stderr)
            return 3
        stream.write(encoded)
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
