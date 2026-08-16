#!/usr/bin/env python3
"""Exercise shared timeout, capture-limit, and redaction boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from common import capture_bytes, credential_values, run_bounded


def main() -> int:
    secret = "hwb-synthetic-credential-value"
    environment = os.environ.copy()
    environment["HWB_TEST_SECRET"] = secret
    redactions = credential_values(environment)
    with tempfile.TemporaryDirectory(prefix="hwb-contract-fault-") as directory:
        root = Path(directory)
        timeout = run_bounded(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import os,sys,time; "
                "Path('partial.txt').write_text('partial'); "
                "print(os.environ['HWB_TEST_SECRET'], flush=True); "
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
        timeout_passed = (
            timeout.returncode == 124
            and timeout.termination_reason == "timeout"
            and partial_effect
        )
        limit_passed = (
            limited.returncode == 125
            and limited.termination_reason == "stdout_limit"
            and limited.stdout_overflow
            and len(limited.stdout) == 128
        )
        serialized_captures = json.dumps(
            {"stdout": stdout_capture, "stderr": stderr_capture}, sort_keys=True
        )
        redaction_passed = (
            secret not in serialized_captures
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
