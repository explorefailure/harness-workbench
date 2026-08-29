#!/usr/bin/env python3
"""Bounded launcher plus durable process cleanup and phase-prefix receipts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
from typing import Any

from harness_workbench import capture as capture_module
from harness_workbench.capture import (
    _group_alive,
    _signal_group,
    _wait_for_group_exit,
    capture_bytes,
    run_bounded,
)

from agent_task_process import prove_registered_process
from agent_task_schema import (
    PHASE_CHECKPOINT_SCHEMA,
    PROCESS_REGISTRY_SCHEMA,
    SUPERVISOR_STOP_SCHEMA,
    bytes_sha256,
    canonical_sha256,
)


HERE = Path(__file__).resolve().parent
LAUNCHER = HERE / "agent_task_launcher.py"


def _append(path: Path, row: dict[str, Any]) -> None:
    raw = json.dumps(
        row, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_registry_row(path: Path, row: dict[str, Any]) -> None:
    """Append one supervisor-owned registry fact with the broker's durability."""
    _append(path, row)


def durable_prefix(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        data = bytearray()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            data.extend(chunk)
    finally:
        os.close(descriptor)
    return {"bytes": len(data), "sha256": bytes_sha256(bytes(data))}


def validate_prefix(path: Path, prefix: dict[str, Any]) -> bool:
    with path.open("rb") as stream:
        raw = stream.read(prefix["bytes"])
    return len(raw) == prefix["bytes"] and bytes_sha256(raw) == prefix["sha256"]


class SpawnBroker:
    def __init__(self, registry: Path, *, python: Path) -> None:
        self.registry = registry
        self.python = Path(os.path.abspath(os.fspath(python)))
        if not self.python.is_file():
            raise FileNotFoundError(f"broker interpreter does not exist: {self.python}")
        registry.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(registry, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        _append(registry, {
            "schema": PROCESS_REGISTRY_SCHEMA,
            "event": "broker_started",
            "kind": "control_plane",
        })
        self.receipts: list[dict[str, Any]] = []
        self.closed = False

    def launch(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        phase: str,
        timeout: float,
        stdout_limit: int,
        stderr_limit: int,
        redactions: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.closed:
            raise RuntimeError("spawn broker is closed")
        registration_id = secrets.token_hex(16)
        command = [
            str(self.python), str(LAUNCHER),
            "--registry", str(self.registry),
            "--registration-id", registration_id,
            "--phase", phase,
            "--", *argv,
        ]
        result = run_bounded(
            command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            termination_grace=1.0,
            forward_signals=False,
        )
        stdout = capture_bytes(
            result.stdout, redactions=redactions,
            source_bytes=result.stdout_source_bytes,
        )
        stdout["limit"] = stdout_limit
        stdout["overflow"] = result.stdout_overflow
        stderr = capture_bytes(
            result.stderr, redactions=redactions,
            source_bytes=result.stderr_source_bytes,
        )
        stderr["limit"] = stderr_limit
        stderr["overflow"] = result.stderr_overflow
        receipt = {
            "schema": PROCESS_REGISTRY_SCHEMA,
            "event": "cleanup",
            "registration_id": registration_id,
            "phase": phase,
            "kind": "clean_self_issued",
            "returncode": result.returncode,
            "termination_reason": result.termination_reason,
            "group_alive_before_cleanup": result.group_alive_before_cleanup,
            "group_alive_after_cleanup": result.group_alive_after_cleanup,
        }
        _append(self.registry, receipt)
        self.receipts.append(receipt)
        capture = {
            "returncode": result.returncode,
            "termination_reason": result.termination_reason,
            "stdout": stdout,
            "stderr": stderr,
            "group_alive_after_cleanup": result.group_alive_after_cleanup,
        }
        return capture, receipt

    def close(self) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError("spawn broker already closed")
        receipt = {
            "schema": PROCESS_REGISTRY_SCHEMA,
            "event": "broker_stopped",
            "kind": "clean_self_issued",
        }
        _append(self.registry, receipt)
        self.closed = True
        return receipt


def witness_abnormal_termination(
    registry: Path,
    stop_record: Path,
    *,
    child: str,
    reason: str,
) -> dict[str, Any]:
    prefix = durable_prefix(registry)
    rows = [
        json.loads(line)
        for line in registry.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    closed = {
        row["registration_id"]
        for row in rows
        if row.get("event") == "cleanup" and "registration_id" in row
    }
    cleanup: list[dict[str, Any]] = []
    for row in rows:
        if row.get("event") != "registered" or row["registration_id"] in closed:
            continue
        pid = row.get("pid")
        pgid = row.get("pgid")
        identity_proved = False
        if type(pid) is int and type(pgid) is int:
            try:
                identity_proved = prove_registered_process(row)
            except ProcessLookupError:
                identity_proved = True
        alive_before = type(pgid) is int and _group_alive(pgid)
        if identity_proved and alive_before:
            _signal_group(pgid, capture_module.signal.SIGTERM)
            if not _wait_for_group_exit(pgid, 1.0):
                _signal_group(pgid, capture_module.signal.SIGKILL)
                _wait_for_group_exit(pgid, 1.0)
        alive_after = type(pgid) is int and _group_alive(pgid)
        receipt = {
            "schema": PROCESS_REGISTRY_SCHEMA,
            "event": "cleanup",
            "registration_id": row["registration_id"],
            "phase": row.get("phase"),
            "kind": "abnormal_supervisor_witnessed",
            "identity_proved": identity_proved,
            "group_alive_before_cleanup": alive_before,
            "group_alive_after_cleanup": alive_after,
        }
        _append(registry, receipt)
        cleanup.append(receipt)
    witness = {
        "schema": PROCESS_REGISTRY_SCHEMA,
        "event": "control_plane_termination",
        "kind": "abnormal_supervisor_witnessed",
        "control_plane_child": child,
        "reason": reason,
        "last_prefix_sha256": prefix["sha256"],
        "cleanup": cleanup,
    }
    _append(registry, witness)
    stop = {
        "schema": SUPERVISOR_STOP_SCHEMA,
        "control_plane_child": child,
        "reason": reason,
        "last_prefix_sha256": prefix["sha256"],
        "latched_at": "supervisor-observation",
        "candidate_eligible": False,
        "cleanup": cleanup,
    }
    raw = json.dumps(stop, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    descriptor = os.open(stop_record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return stop


def build_phase_checkpoint(
    *,
    journal: Path,
    registry: Path,
    store_digests: dict[str, str],
    comparison_sha256: str,
    usage_sha256: str,
    cleanup_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    eligible = (
        len(store_digests) == 5
        and all(row.get("kind") == "clean_self_issued" for row in cleanup_receipts)
        and all(not row.get("group_alive_after_cleanup", True) for row in cleanup_receipts)
    )
    return {
        "schema": PHASE_CHECKPOINT_SCHEMA,
        "journal_prefix": durable_prefix(journal),
        "registry_prefix": durable_prefix(registry),
        "stores": dict(sorted(store_digests.items())),
        "comparison_sha256": comparison_sha256,
        "usage_sha256": usage_sha256,
        "smoke_cleanup_receipts": cleanup_receipts,
        "eligible": eligible,
        "checkpoint_sha256": canonical_sha256({
            "stores": dict(sorted(store_digests.items())),
            "comparison_sha256": comparison_sha256,
            "usage_sha256": usage_sha256,
        }),
    }
