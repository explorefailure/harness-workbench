#!/usr/bin/env python3
"""Layer-1 determinism soak for the capture primitive, and nothing else.

The method is the one `experiments/pi_coding_agent/stress_adapter.py` uses:
run the same thing N times, project each result down to what the contract
actually determines, and require one identical SHA-256 across every run.

**No model, no credentials, no network.** Every subject here is a fake
executable -- a short Python script chosen to provoke one specific failure.
This measures the promoted code's steadiness. It is deliberately *not* the
stochastic outcome-rate campaign, which measures a model and would tell you
nothing about whether this module is fit to promote.

The interesting part is what each projection leaves out, and why. A projection
that included everything would fail on facts the contract never promised, and
"the soak is flaky" is how a real instability gets explained away. Each
exclusion below is a fact the primitive measures but does not determine:

- `argv` holds an absolute interpreter path, which is the machine's, not the run's.
- `*_source_bytes` under saturation counts how much a racing writer got out
  before the limit tripped. The *kept* bytes are exactly the limit and are
  digested; how much was thrown away is a property of scheduling.
- The exact signal number after a timeout is SIGTERM or SIGKILL depending on
  whether the child honoured the first one. Whether it died is contractual;
  which signal finished it is not, so the projection keeps the sign.

Run it directly for a bigger N than the suite uses:

    PYTHONPATH=src python3 tests/capture_soak.py --runs 200
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from harness_workbench import capture  # noqa: E402


SCHEMA = "hwb-capture-soak/v0.1"

# Short enough that eight scenarios times N runs stays a test, long enough that
# a loaded machine does not trip the deadline before the child has started.
TIMEOUT = 0.25
GRACE = 0.35


def _fake(script: str) -> list[str]:
    """A fake executable: no model, no network, no credentials, no ambiguity."""
    return [sys.executable, "-c", script]


# Each scenario is (argv, run kwargs, sidecar bytes or None).
SCENARIOS: dict[str, dict[str, Any]] = {
    "success": {
        "argv": _fake("import sys; sys.stdout.write('done\\n')"),
        "kwargs": {"timeout": 10},
    },
    "nonzero_exit": {
        "argv": _fake("import sys; sys.stdout.write('partial\\n'); raise SystemExit(7)"),
        "kwargs": {"timeout": 10},
    },
    "malformed_output": {
        # Invalid UTF-8 followed by a truncated JSON line: the two ways evidence
        # is normally broken, in one subject.
        "argv": _fake("import sys; sys.stdout.buffer.write(b'\\xff\\xfe{\"a\":1}\\n{\"b\":')"),
        "kwargs": {"timeout": 10},
    },
    "saturation": {
        "argv": _fake("import os,time; os.write(1, b'x' * 200000); time.sleep(30)"),
        "kwargs": {"timeout": 10, "stdout_limit": 4096, "termination_grace": GRACE},
    },
    "timeout": {
        "argv": _fake("import time; time.sleep(30)"),
        "kwargs": {"timeout": TIMEOUT, "termination_grace": GRACE},
    },
    "ignored_termination": {
        "argv": _fake(
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(30)"
        ),
        "kwargs": {"timeout": TIMEOUT, "termination_grace": GRACE},
    },
    "orphan_child": {
        # The parent exits at once; the grandchild inherits the pipe and lives.
        # Reading to EOF here would hang, so this is the case that proves the
        # loop stops at child exit rather than at end-of-stream.
        "argv": _fake(
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
        ),
        "kwargs": {"timeout": 10, "termination_grace": GRACE},
    },
    "evidence_corruption": {
        "argv": _fake("import sys; sys.stdout.write('ok\\n')"),
        "kwargs": {"timeout": 10},
        # Two valid records, one unparseable line, one line cut off mid-write --
        # what a sidecar looks like when its writer was killed. The records that
        # did parse must survive, deterministically, or a partial run becomes
        # unanalysable exactly when it matters most.
        "sidecar": b'{"a":1}\nnot json at all\n{"b":2}\n{"c":',
    },
}


def stable_projection(name: str, result: capture.Bounded, sidecar: Any) -> dict[str, Any]:
    """What the contract determines for this scenario, and only that."""
    signalled = name in {"timeout", "ignored_termination"}
    projection: dict[str, Any] = {
        "scenario": name,
        "termination_reason": result.termination_reason,
        "stdout_overflow": result.stdout_overflow,
        "stderr_overflow": result.stderr_overflow,
        "stdout_sha256": capture.digest_bytes(result.stdout),
        "stderr_sha256": capture.digest_bytes(result.stderr),
        "stdout_bytes": len(result.stdout),
        "stderr_bytes": len(result.stderr),
        # The whole point of the cleanup contract: whatever the subject did,
        # nothing of it is left behind. This must hold in every scenario.
        "group_alive_after_cleanup": result.group_alive_after_cleanup,
        "forwarded_signals": list(result.forwarded_signals),
    }
    if signalled:
        # Died on a signal: contractual. Which signal: not.
        projection["exit_was_signal"] = result.returncode < 0
    else:
        projection["returncode"] = result.returncode
    if not result.stdout_overflow:
        # Only meaningful when nothing was discarded; under saturation this
        # counts a race, not a result.
        projection["stdout_source_bytes"] = result.stdout_source_bytes
    if sidecar is not None:
        projection["sidecar"] = {
            "exists": sidecar["exists"],
            "size": sidecar["size"],
            "file_sha256": sidecar["file_sha256"],
            "sha256": sidecar["sha256"],
            "errors": sidecar["errors"],
            "jsonl_records": len(sidecar["jsonl"] or []),
        }
    return projection


def run_once(name: str) -> dict[str, Any]:
    scenario = SCENARIOS[name]
    with tempfile.TemporaryDirectory(prefix="hwb-capture-soak-") as directory:
        root = Path(directory)
        env = capture.minimal_environment(root, {"HWB_SOAK": "1"})
        sidecar_bytes = scenario.get("sidecar")
        sidecar = None
        if sidecar_bytes is not None:
            path = root / "sidecar.jsonl"
            path.write_bytes(sidecar_bytes)
        result = capture.run_bounded(
            scenario["argv"], cwd=root, env=env, **scenario["kwargs"]
        )
        if sidecar_bytes is not None:
            sidecar = capture.capture_file(
                root / "sidecar.jsonl", required=True, format_name="jsonl"
            )
        return stable_projection(name, result, sidecar)


def _digest(projection: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def soak(
    *,
    runs: int = 8,
    concurrency: int = 1,
    scenarios: tuple[str, ...] = (),
    observer: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if runs <= 0 or concurrency <= 0:
        raise ValueError("runs and concurrency must be positive")
    names = scenarios or tuple(SCENARIOS)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "runs": runs,
        "concurrency": concurrency,
        "scenarios": {},
    }
    unstable: list[str] = []
    for name in names:
        if observer:
            observer(name)
        if concurrency == 1:
            projections = [run_once(name) for _ in range(runs)]
        else:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=concurrency
            ) as pool:
                projections = list(pool.map(lambda _i: run_once(name), range(runs)))
        first = projections[0]
        mismatched = [
            index
            for index, projection in enumerate(projections[1:], 1)
            if projection != first
        ]
        if mismatched:
            unstable.append(name)
        report["scenarios"][name] = {
            "stable": not mismatched,
            "projection_sha256": _digest(first),
            "mismatched_runs": mismatched,
            "projection": first,
            "differing": [projections[i] for i in mismatched[:2]],
        }
    report["passed"] = not unstable
    report["unstable"] = unstable
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = soak(
            runs=args.runs,
            concurrency=args.concurrency,
            scenarios=tuple(args.scenario),
            observer=None if args.quiet else lambda name: print(
                f"soaking {name} ...", file=sys.stderr
            ),
        )
    except (OSError, ValueError) as error:
        print(json.dumps({"schema": SCHEMA, "passed": False, "error": str(error)}))
        return 1
    for name, item in report["scenarios"].items():
        print(
            f"{name:22} {'stable' if item['stable'] else 'UNSTABLE'}  "
            f"{item['projection_sha256'][:12]}",
            file=sys.stderr,
        )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
