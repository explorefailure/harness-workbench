#!/usr/bin/env python3
"""Generate an exact, zero-call live plan without releasing any provider."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import secrets
from typing import Any

from agent_task_offline import build_conformance_documents
from agent_task_providers import RealProviderPlanTransport
from agent_task_schema import SUBJECTS, bytes_sha256, canonical_bytes, canonical_sha256


HERE = Path(__file__).resolve().parent
ROLLING_STOP = 80
WEEKLY_STOP = 90
SUBJECT_TIMEOUTS = {
    "claude": 120,
    "codex": 120,
    "deepseek": 240,
    "hermes": 180,
    "pi": 240,
}
APPARATUS_FILES = (
    "adapters.py",
    "agent_task.py",
    "agent_task_archives.py",
    "agent_task_broker.py",
    "agent_task_control.py",
    "agent_task_emit.py",
    "agent_task_fake_provider.py",
    "agent_task_launcher.py",
    "agent_task_live_plan.py",
    "agent_task_offline.py",
    "agent_task_process.py",
    "agent_task_providers.py",
    "agent_task_routes.py",
    "agent_task_runtime.py",
    "agent_task_schema.py",
    "agent_task_schemas.json",
    "agent_task_services.py",
    "agent_task_test_vectors.json",
    "agent_task_validate.py",
    "dsh_patch.yml",
    "hermes_config.yaml",
    "model_selection.json",
    "pin.json",
)


class LivePlanError(ValueError):
    """The zero-call plan cannot bind a live destination or required input."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _resolved_nonexistent_destination(destination: Path) -> Path:
    if destination.exists() or destination.is_symlink():
        raise LivePlanError(f"live destination must not exist: {destination}")
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir():
        raise LivePlanError("live destination parent is not a directory")
    resolved = parent / destination.name
    if resolved.exists() or resolved.is_symlink():
        raise LivePlanError(f"resolved live destination must not exist: {resolved}")
    return resolved


def _usage_status(
    usage: dict[str, Any] | None,
    *,
    now: dt.datetime,
    maximum_age_seconds: int = 120,
) -> dict[str, Any]:
    if usage is None:
        return {
            "available": False,
            "fresh": False,
            "gate_passed": False,
            "reason": "no fresh usage snapshot was injected; zero-call planning does not query the gateway",
        }
    try:
        read_at = dt.datetime.fromisoformat(str(usage["read_at"]).replace("Z", "+00:00"))
        if read_at.tzinfo is None:
            raise ValueError("read_at is not timezone-aware")
        age = (now - read_at.astimezone(dt.timezone.utc)).total_seconds()
    except (KeyError, TypeError, ValueError) as error:
        raise LivePlanError(f"usage snapshot has no valid read_at: {error}") from error
    fresh = 0 <= age <= maximum_age_seconds
    if usage.get("metered") is False:
        gate_passed = fresh
        values: dict[str, Any] = {}
    else:
        windows = usage.get("windows")
        if type(windows) is not dict:
            raise LivePlanError("metered usage snapshot has no windows object")
        try:
            values = {
                "rolling": windows["rolling"]["percent"],
                "weekly": windows["weekly"]["percent"],
            }
        except (KeyError, TypeError) as error:
            raise LivePlanError("usage snapshot lacks rolling or weekly percent") from error
        if any(type(value) is not int for value in values.values()):
            raise LivePlanError("usage percentages must be integers")
        gate_passed = (
            fresh
            and values["rolling"] < ROLLING_STOP
            and values["weekly"] < WEEKLY_STOP
        )
    return {
        "available": True,
        "fresh": fresh,
        "age_seconds": age,
        "gate_passed": gate_passed,
        "snapshot_sha256": canonical_sha256(usage),
        "observed": values,
    }


def _spec(
    *, subject: str, phase: str, task_sha256: str, archive_sha256: str,
    store_nonce: str,
) -> dict[str, Any]:
    draws = 1 if phase == "write-smoke" else 3
    return {
        "schema": "agent-task-virtual-spec/v0.1",
        "subject": subject,
        "phase": phase,
        "task_sha256": task_sha256,
        "input_archive_sha256": archive_sha256,
        "store_nonce": store_nonce,
        "features": [
            {"name": "freeze"},
            {"name": "receipt"},
            {"name": "retry", "config": {"max": 2}},
            {"name": "sample", "config": {"n": draws}},
            {"name": "timing"},
        ],
        "step_timeout_ms_present": False,
    }


def generate_live_plan(
    destination: Path,
    *,
    usage_snapshot: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return a digest-bound preview. This function performs no network I/O."""
    resolved_destination = _resolved_nonexistent_destination(destination)
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise LivePlanError("plan time must be timezone-aware")
    task, archive, fake_plan = build_conformance_documents()
    task_bytes = canonical_bytes(task) + b"\n"
    fake_plan_bytes = canonical_bytes(fake_plan) + b"\n"
    task_sha256 = canonical_sha256(task)
    archive_sha256 = bytes_sha256(archive)
    apparatus = {
        name: _file_sha256(HERE / name)
        for name in APPARATUS_FILES
    }
    validator_sha256 = apparatus["agent_task_validate.py"]
    comparator_sha256 = validator_sha256
    routes = RealProviderPlanTransport().routes(task["prompt"])
    specs: dict[str, dict[str, Any]] = {}
    store_nonces: dict[str, dict[str, str]] = {}
    for phase in ("write-smoke", "repair-matrix"):
        store_nonces[phase] = {}
        specs[phase] = {}
        for subject in SUBJECTS:
            nonce = secrets.token_hex(16)
            store_nonces[phase][subject] = nonce
            document = _spec(
                subject=subject, phase=phase, task_sha256=task_sha256,
                archive_sha256=archive_sha256, store_nonce=nonce,
            )
            specs[phase][subject] = {
                "sha256": canonical_sha256(document),
                "document": document,
            }
    selection = json.loads((HERE / "model_selection.json").read_text(encoding="utf-8"))
    pins = json.loads((HERE / "pin.json").read_text(encoding="utf-8"))
    one_draw_maximum_seconds = sum(value * 2 for value in SUBJECT_TIMEOUTS.values())
    plan = {
        "schema": "agent-task-live-execution-plan/v0.1",
        "generated_at": current.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": "plan_only",
        "network_calls_authorized": 0,
        "paid_provider_calls_authorized": 0,
        "destination": {
            "resolved": str(resolved_destination),
            "observed_nonexistent": True,
            "atomic_mode": "0700",
        },
        "inputs": {
            "task_sha256": task_sha256,
            "task_file_sha256": bytes_sha256(task_bytes),
            "workspace_archive_sha256": archive_sha256,
            "fake_transport_plan_sha256": bytes_sha256(fake_plan_bytes),
            "apparatus": dict(sorted(apparatus.items())),
            "apparatus_map_sha256": canonical_sha256(apparatus),
            "validator_program_sha256": validator_sha256,
            "comparator_program_sha256": comparator_sha256,
            "specs": specs,
        },
        "provider_pins": {
            "pin_document_sha256": _file_sha256(HERE / "pin.json"),
            "model_selection_sha256": _file_sha256(HERE / "model_selection.json"),
            "active_profile": selection["active"],
            "pins": pins,
            "routes": routes,
        },
        "usage": {
            "stop_thresholds": {"rolling": ROLLING_STOP, "weekly": WEEKLY_STOP},
            "fresh": _usage_status(usage_snapshot, now=current),
            "permit_time_refresh_required": True,
        },
        "timeouts": {
            "subject_episode_seconds": SUBJECT_TIMEOUTS,
            "route_canary_outer_seconds": 540,
            "write_smoke_outer_seconds": 540 + one_draw_maximum_seconds + 300,
            "repair_matrix_outer_seconds": one_draw_maximum_seconds * 3 + 600,
            "derivation": "sum(subject episode × maximum base attempts) plus bounded phase headroom",
        },
        "calls": {
            "canary_write_smoke": {"nominal": 8, "maximum": 13},
            "repair_matrix": {"nominal": 15, "maximum": 30},
            "combined_informational_only": {"nominal": 23, "maximum": 43},
        },
        "store_nonces": store_nonces,
        "release": {
            "enabled": False,
            "authorization_artifact_present": False,
            "separate_authorization_artifact_required": True,
            "one_authorization_covers": "one paid attempt",
            "automatic_retry_after_campaign_failure": False,
            "adapter_certification_promotion": False,
        },
    }
    return {
        "execution_plan": plan,
        "execution_plan_sha256": canonical_sha256(plan),
    }
