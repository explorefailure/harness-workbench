#!/usr/bin/env python3
"""Exercise shared timeout, capture-limit, and redaction boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from harness_workbench.capture import capture_bytes, credential_values, run_bounded


def main() -> int:
    # Deliberately non-ASCII. With an ASCII secret this whole check passes
    # whether or not redaction works on the form most likely to appear:
    # `json.dumps` escapes non-ASCII BY DEFAULT, so a secret containing one
    # reaches the serialized capture as `\uXXXX`, and a plain `secret not in
    # serialized` is then trivially true. The control could not fail for the
    # case it exists to catch.
    secret = "hwb-synthétic-credential-valué"
    environment = os.environ.copy()
    environment["HWB_TEST_SECRET"] = secret
    redactions = credential_values(environment)
    with tempfile.TemporaryDirectory(prefix="hwb-contract-fault-") as directory:
        root = Path(directory)
        timeout = run_bounded(
            [
                sys.executable,
                "-c",
                # Two DIFFERENT emissions on purpose, because the two paths
                # fail independently. stderr echoes the raw value, which is
                # what a careless script does. stdout emits it inside JSON the
                # way every subject in this tree emits its transcript -- and
                # `json.dumps` escapes non-ASCII by default, so this is the
                # form a raw-only scrubber silently misses.
                "from pathlib import Path; import json,os,sys,time; "
                "Path('partial.txt').write_text('partial'); "
                "print(json.dumps({'token': os.environ['HWB_TEST_SECRET']}), flush=True); "
                "print(os.environ['HWB_TEST_SECRET'], file=sys.stderr, flush=True); "
                "time.sleep(10)",
            ],
            cwd=root,
            env=environment,
            timeout=0.1,
            stdout_limit=4096,
            stderr_limit=4096,
        )
        limited = run_bounded(
            [sys.executable, "-c", 'print("x" * 10000)'],
            cwd=root,
            env=environment,
            timeout=2,
            stdout_limit=128,
            stderr_limit=128,
        )
        stdout_capture = capture_bytes(timeout.stdout, redactions=redactions)
        stderr_capture = capture_bytes(timeout.stderr, redactions=redactions)
        partial_effect = (
            (root / "partial.txt").exists()
            and (root / "partial.txt").read_bytes() == b"partial"
        )
        # A bound that fired is read from `termination_reason`, never from the
        # exit status. The status of a subject the bound killed is a signal
        # code, and 124/125 are exit codes a subject can also produce on its
        # own -- checking them cannot tell the two apart, so it is not checked.
        timeout_passed = (
            timeout.termination_reason == "timeout"
            and timeout.returncode != 0
            and partial_effect
        )
        limit_passed = (
            limited.termination_reason == "stdout_limit"
            and limited.stdout_overflow
            and len(limited.stdout) == 128
        )
        serialized_captures = json.dumps(
            {"stdout": stdout_capture, "stderr": stderr_capture}, sort_keys=True
        )
        # Both forms, because the serialized blob is where the escaped one
        # lives. Checking only the raw value is what made this pass vacuously.
        escaped_secret = json.dumps(secret)[1:-1]
        redaction_passed = (
            secret not in serialized_captures
            and escaped_secret not in serialized_captures
            and stdout_capture["redaction_count"] == 1
            and stderr_capture["redaction_count"] == 1
        )
    result = {
        "schema": "cross-harness-contract-faults/v0.1",
        "passed": timeout_passed and limit_passed and redaction_passed,
        "timeout": {
            "passed": timeout_passed,
            "returncode": timeout.returncode,
            "termination_reason": timeout.termination_reason,
            "partial_effect": partial_effect,
            "native_terminal_event": False,
        },
        "capture_limit": {
            "passed": limit_passed,
            "returncode": limited.returncode,
            "termination_reason": limited.termination_reason,
            "stored_stdout_bytes": len(limited.stdout),
            "source_stdout_bytes": limited.stdout_source_bytes,
        },
        "redaction": {
            "passed": redaction_passed,
            "stdout": stdout_capture,
            "stderr": stderr_capture,
        },
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
