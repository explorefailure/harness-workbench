#!/usr/bin/env python3
"""Run a bounded concurrent reproducibility soak against one adapter config."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


HERE = Path(__file__).resolve().parent


def stable_projection(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "configuration": envelope["configuration"],
        "pin": envelope["pin"],
        "runtime": {
            key: value
            for key, value in envelope["runtime"].items()
            if key not in {"python_executable", "pi_executable"}
        },
        "isolation": envelope["isolation"],
        "workspace_before": envelope["workspace"]["before"],
        "workspace_after": envelope["workspace"]["after"],
        "summary": envelope["pi"]["summary"],
        "summary_sha256": envelope["pi"]["summary_sha256"],
    }


def run_once(config: Path, workspace_parent: Path, timeout: float) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            str(HERE / "adapter.py"),
            str(config),
            "--workspace-parent",
            str(workspace_parent),
            "--timeout",
            str(timeout),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout + 10,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"adapter exited {result.returncode}: {result.stderr or result.stdout}"
        )
    envelope = json.loads(result.stdout)
    if not envelope.get("verdict", {}).get("passed"):
        raise RuntimeError(f"adapter verdict failed: {envelope.get('verdict')}")
    if envelope["pi"]["post_cleanup_group_alive"]:
        raise RuntimeError("adapter left a live process group")
    return envelope


def soak(config: Path, *, runs: int, concurrency: int, timeout: float) -> dict[str, Any]:
    if runs <= 0 or concurrency <= 0:
        raise ValueError("runs and concurrency must be positive")
    config = config.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="hwb-pi-soak-") as directory:
        workspace_parent = Path(directory)
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            envelopes = list(
                pool.map(
                    lambda _index: run_once(config, workspace_parent, timeout),
                    range(runs),
                )
            )

    roots = [item["workspace"]["retained_root"] for item in envelopes]
    if len(set(roots)) != runs:
        raise RuntimeError("retained workspace roots were not unique")
    expected = stable_projection(envelopes[0])
    expected_bytes = json.dumps(
        expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    mismatches = [
        index
        for index, envelope in enumerate(envelopes[1:], 1)
        if stable_projection(envelope) != expected
    ]
    return {
        "schema": "pi-hwb-adapter-soak/v0.1",
        "passed": not mismatches,
        "runs": runs,
        "concurrency": concurrency,
        "unique_retained_roots": len(set(roots)),
        "stable_projection_sha256": hashlib.sha256(expected_bytes).hexdigest(),
        "mismatched_runs": mismatches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "config", nargs="?", type=Path, default=HERE / "text_adapter_config.json"
    )
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    try:
        result = soak(
            args.config,
            runs=args.runs,
            concurrency=args.concurrency,
            timeout=args.timeout,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "schema": "pi-hwb-adapter-soak/v0.1",
                    "passed": False,
                    "error": str(error),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
