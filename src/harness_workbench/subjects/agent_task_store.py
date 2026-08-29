#!/usr/bin/env python3
"""Materialize and verify one sealed single-draw episode as a Workbench store."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from harness_workbench import canon
from harness_workbench.capture import run_bounded

from agent_task_schema import SUBJECTS, bytes_sha256, validate_run


HERE = Path(__file__).resolve().parent
EMITTER = HERE / "agent_task_emit.py"


class StoreError(RuntimeError):
    """A sealed episode could not become one exact verified run store."""


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _freeze_lock(spec_path: Path, inputs: list[str]) -> None:
    digests = {
        name: canon.digest_file(str(spec_path.parent / name)) for name in inputs
    }
    _write_json(spec_path.with_suffix(".freeze.lock"), {"digests": digests})


def _run_directories(records: Path) -> set[str]:
    if records.is_symlink() or not records.is_dir():
        raise StoreError("Workbench phase root is not a real directory")
    names: set[str] = set()
    for child in records.iterdir():
        if child.is_symlink() or not child.is_dir():
            raise StoreError("Workbench phase root contains a partial store")
        names.add(child.name)
    return names


def materialize_single_draw_store(
    *,
    subject: str,
    phase: str,
    episode_path: Path,
    spec_root: Path,
    records: Path,
    expected_emitter_sha256: str | None = None,
) -> dict[str, Any]:
    """Create exactly one ordinary store and prove its sealed episode bytes."""
    if subject not in SUBJECTS:
        raise StoreError("store subject is not one of the exact five")
    if not phase or phase != Path(phase).name:
        raise StoreError("store phase is not a basename")
    if episode_path.is_symlink():
        raise StoreError("sealed episode is an alias")
    episode_path = episode_path.resolve(strict=True)
    if not episode_path.is_file():
        raise StoreError("sealed episode is not a regular file")
    try:
        episode = validate_run(json.loads(episode_path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as error:
        raise StoreError(f"sealed episode is invalid: {error}") from error
    if episode["subject"] != subject:
        raise StoreError("sealed episode subject disagrees with the store")
    if episode["base_attempt"]["ordinal"] != 0:
        raise StoreError("single-draw store requires the first base attempt")
    if EMITTER.is_symlink() or not EMITTER.is_file():
        raise StoreError("Workbench episode emitter is not a regular apparatus file")
    if (
        expected_emitter_sha256 is not None
        and bytes_sha256(EMITTER.read_bytes()) != expected_emitter_sha256
    ):
        raise StoreError("Workbench episode emitter drifted from the execution plan")

    before = _run_directories(records)
    if len(before) >= len(SUBJECTS):
        raise StoreError("Workbench phase root already has the exact-five maximum")
    if spec_root.is_symlink() or not spec_root.is_dir():
        raise StoreError("Workbench spec root is not a real directory")
    own = spec_root / subject
    try:
        own.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError as error:
        raise StoreError("subject spec directory already exists") from error
    copied_emitter = own / "agent_task_emit.py"
    shutil.copy2(EMITTER, copied_emitter)
    if (
        expected_emitter_sha256 is not None
        and bytes_sha256(copied_emitter.read_bytes()) != expected_emitter_sha256
    ):
        raise StoreError("copied Workbench episode emitter drifted")
    shutil.copy2(episode_path, own / "episode.json")
    inputs = ["agent_task_emit.py", "episode.json"]
    spec_path = own / f"{subject}.json"
    _write_json(
        spec_path,
        {
            "schema": "hwbspec/v0.1",
            "run_class": "discovery",
            "features_root": "harness_workbench:builtin",
            "features": [
                {"name": "freeze"},
                {"name": "receipt"},
                {"name": "retry", "config": {"max": 2}},
                {"name": "sample", "config": {"n": 1}},
                {"name": "timing"},
            ],
            "steps": [
                {
                    "id": f"{subject}-agent-task",
                    "argv": [
                        sys.executable,
                        "agent_task_emit.py",
                        "--store-nonce",
                        episode["store_nonce"],
                        "episode.json",
                    ],
                    "inputs": inputs,
                }
            ],
        },
    )
    _freeze_lock(spec_path, inputs)
    environment = dict(os.environ)
    if environment.get("PYTHONPATH"):
        environment["PYTHONPATH"] = os.pathsep.join(
            str((Path.cwd() / item).resolve())
            if not Path(item).is_absolute()
            else item
            for item in environment["PYTHONPATH"].split(os.pathsep)
        )
    completed = run_bounded(
        [
            sys.executable,
            "-m",
            "harness_workbench",
            "--root",
            str(records),
            "run",
            str(spec_path),
        ],
        cwd=own,
        env=environment,
        timeout=30,
        stdout_limit=1024 * 1024,
        stderr_limit=1024 * 1024,
        termination_grace=1.0,
        forward_signals=False,
    )
    if (
        completed.returncode != 0
        or completed.termination_reason is not None
        or completed.stdout_overflow
        or completed.stderr_overflow
        or completed.group_alive_after_cleanup
    ):
        raise StoreError(
            "Workbench run was not bounded and clean: "
            + completed.stderr.decode("utf-8", errors="replace")[:1000]
        )
    lines = completed.stdout.decode("utf-8", errors="strict").splitlines()
    fields = lines[0].split() if lines else []
    if len(fields) < 5 or fields[-1] != "completed":
        raise StoreError("Workbench run did not report completion")
    run_id = fields[0]
    if run_id != Path(run_id).name:
        raise StoreError("Workbench reported a non-basename run identity")
    after = _run_directories(records)
    if after - before != {run_id} or before - after:
        raise StoreError("Workbench did not create exactly its reported store")
    run_dir = records / run_id
    verify = run_bounded(
        [
            sys.executable,
            "-m",
            "harness_workbench",
            "--root",
            str(records),
            "verify",
            run_id,
        ],
        cwd=own,
        env=environment,
        timeout=15,
        stdout_limit=1024 * 1024,
        stderr_limit=1024 * 1024,
        termination_grace=1.0,
        forward_signals=False,
    )
    if (
        verify.returncode != 0
        or verify.termination_reason is not None
        or verify.stdout_overflow
        or verify.stderr_overflow
        or verify.group_alive_after_cleanup
        or b"conforms: yes" not in verify.stdout
    ):
        raise StoreError("Workbench store failed hwb verify")
    record_path = run_dir / "record.json"
    integrity_path = run_dir / "integrity.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    attempts = [
        json.loads(line)
        for line in (run_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    expected_cause = [
        {"feature": "sample", "i": 0},
        {"feature": "retry", "i": 0},
    ]
    if (
        len(attempts) != 1
        or attempts[0].get("n") != episode["base_attempt"]["ordinal"]
        or attempts[0].get("caused_by") != expected_cause
        or attempts[0].get("exit") != 0
    ):
        raise StoreError("Workbench attempt order does not reconcile with call control")
    freeze = record.get("extras", {}).get("freeze", {})
    if freeze.get("baseline") != "compared" or freeze.get("drifted"):
        raise StoreError("Workbench freeze baseline did not compare cleanly")
    receipt = record.get("extras", {}).get("receipt", {}).get("bound", {})
    if (
        receipt.get("inputs_from") != "freeze"
        or receipt.get("inputs") != freeze.get("digests")
    ):
        raise StoreError("Workbench receipt inputs do not equal the freeze inputs")
    if record.get("run_id") != run_id:
        raise StoreError("Workbench record identity disagrees with the reported store")
    stdout = (
        run_dir
        / "steps"
        / f"{subject}-agent-task"
        / "attempts"
        / "0"
        / "stdout.bin"
    )
    if stdout.read_bytes() != episode_path.read_bytes():
        raise StoreError("Workbench store did not retain the exact episode bytes")
    return {
        "schema": "agent-task-single-draw-store/v0.1",
        "phase": phase,
        "subject": subject,
        "run_id": record["run_id"],
        "run_store_tree_sha256": canon.digest_tree(str(run_dir)),
        "record_json_sha256": canon.digest_file(str(record_path)),
        "integrity_json_sha256": canon.digest_file(str(integrity_path)),
    }
