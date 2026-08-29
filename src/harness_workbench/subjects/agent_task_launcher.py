#!/usr/bin/env python3
"""Register this process group durably, then replace it with the requested child."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent_task_process import registration_identity
from agent_task_schema import PROCESS_REGISTRY_SCHEMA


def _append(path: Path, row: dict) -> None:
    raw = json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--registration-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv:
        raise SystemExit("launcher requires an argv after --")
    executable = Path(os.path.abspath(argv[0]))
    if not executable.is_file():
        raise SystemExit(f"launcher executable does not exist: {executable}")
    pid = os.getpid()
    _append(args.registry, {
        "schema": PROCESS_REGISTRY_SCHEMA,
        "event": "registered",
        "registration_id": args.registration_id,
        "phase": args.phase,
        "pid": pid,
        "pgid": os.getpgid(pid),
        **registration_identity(executable),
    })
    os.execve(str(executable), argv, dict(os.environ))
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
