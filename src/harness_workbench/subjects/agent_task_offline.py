#!/usr/bin/env python3
"""Build and verify the complete zero-network five-subject simulation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
from typing import Any

from harness_workbench import canon
from harness_workbench.capture import credential_values, run_bounded

from agent_task_archives import build_workspace_archive_from_entries
from agent_task_broker import build_phase_checkpoint, validate_prefix
from agent_task_runtime import run_episode
from agent_task_services import ControlPlaneSupervisor
from agent_task_schema import SUBJECTS, bytes_sha256, canonical_sha256, validate_task
from agent_task_validate import compare_exact_five, scan_credentials


HERE = Path(__file__).resolve().parent
EMITTER = HERE / "agent_task_emit.py"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _file_row(path: str, data: bytes, mode: int = 0o644) -> dict[str, Any]:
    return {
        "path": path,
        "kind": "file",
        "mode": mode,
        "size": len(data),
        "sha256": bytes_sha256(data),
    }


def build_conformance_documents() -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    red = b"red\n"
    green = b"green\n"
    unchanged = b"unchanged neighbor\n"
    created = "created Unicode route\n".encode("utf-8")
    workspace_archive = build_workspace_archive_from_entries([
        ("nested dir", "directory", 0o755, None),
        ("nested dir/na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve file.txt", "file", 0o644, red),
        ("unchanged.txt", "file", 0o644, unchanged),
    ])
    operations = sorted([
        {"op": "create", **_file_row("created \N{GREEK SMALL LETTER BETA}.txt", created)},
        {"op": "modify", **_file_row("nested dir/na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve file.txt", green)},
    ], key=lambda row: row["path"])
    task = {
        "schema": "agent-task/v0.1",
        "task_id": "offline-five-route-conformance",
        "prompt": (
            "Change nested dir/na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve file.txt from red to green, create "
            "created \N{GREEK SMALL LETTER BETA}.txt with the declared content, and leave unchanged.txt untouched."
        ),
        "workspace_archive_sha256": bytes_sha256(workspace_archive),
        "effects_policy": {"operations": operations},
        "verification": {
            "pre": sorted([
                {"path": "created \N{GREEK SMALL LETTER BETA}.txt", "kind": "absent", "mode": 0},
                _file_row("nested dir/na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve file.txt", red),
                _file_row("unchanged.txt", unchanged),
            ], key=lambda row: row["path"]),
            "post": sorted([
                _file_row("created \N{GREEK SMALL LETTER BETA}.txt", created),
                _file_row("nested dir/na\N{LATIN SMALL LETTER I WITH DIAERESIS}ve file.txt", green),
                _file_row("unchanged.txt", unchanged),
            ], key=lambda row: row["path"]),
        },
        "limits": {
            "episode_seconds": 15,
            "stdout_bytes": 128 * 1024,
            "stderr_bytes": 128 * 1024,
            "archive_bytes": 2 * 1024 * 1024,
            "effects_bytes": 512 * 1024,
            "files": 64,
            "file_bytes": 256 * 1024,
            "total_file_bytes": 1024 * 1024,
        },
    }
    validate_task(task)
    fake_plan = {
        "schema": "agent-task-fake-provider-plan/v0.1",
        "operations": [
            {
                "op": row["op"], "path": row["path"], "kind": row["kind"],
                "mode": row["mode"],
                "content_base64": base64.b64encode(
                    created if row["path"].startswith("created") else green
                ).decode("ascii"),
            }
            for row in operations
        ],
    }
    return task, workspace_archive, fake_plan


def build_conformance_bundle(bundle: Path) -> tuple[dict[str, Any], bytes, Path]:
    task, workspace_archive, fake_plan_document = build_conformance_documents()
    (bundle / "workspace.zip").write_bytes(workspace_archive)
    _write_json(bundle / "task.json", task)
    fake_plan = bundle / "fake-provider-plan.json"
    _write_json(fake_plan, fake_plan_document)
    return task, workspace_archive, fake_plan


def _freeze_lock(spec_path: Path, inputs: list[str]) -> None:
    digests = {
        name: canon.digest_file(str(spec_path.parent / name))
        for name in inputs
    }
    _write_json(spec_path.with_suffix(".freeze.lock"), {"digests": digests})


def _store_episode(
    *,
    subject: str,
    episode_path: Path,
    spec_root: Path,
    records: Path,
) -> tuple[Path, dict[str, Any]]:
    own = spec_root / subject
    own.mkdir(parents=True)
    shutil.copy2(EMITTER, own / "agent_task_emit.py")
    shutil.copy2(episode_path, own / "episode.json")
    episode = json.loads(episode_path.read_text(encoding="utf-8"))
    inputs = ["agent_task_emit.py", "episode.json"]
    spec_path = own / f"{subject}.json"
    _write_json(spec_path, {
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
        "steps": [{
            "id": f"{subject}-agent-task",
            "argv": [
                sys.executable, "agent_task_emit.py",
                "--store-nonce", episode["store_nonce"], "episode.json"
            ],
            "inputs": inputs,
        }],
    })
    _freeze_lock(spec_path, inputs)
    environment = dict(os.environ)
    if environment.get("PYTHONPATH"):
        environment["PYTHONPATH"] = os.pathsep.join(
            str((Path.cwd() / item).resolve()) if not Path(item).is_absolute() else item
            for item in environment["PYTHONPATH"].split(os.pathsep)
        )
    completed = run_bounded(
        [
            sys.executable, "-m", "harness_workbench",
            "--root", str(records), "run", str(spec_path),
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
        raise RuntimeError(
            "offline Workbench run was not bounded and clean: "
            + completed.stderr.decode("utf-8", errors="replace")[:1000]
        )
    lines = completed.stdout.decode("utf-8", errors="strict").splitlines()
    fields = lines[0].split() if lines else []
    if len(fields) < 5 or fields[-1] != "completed":
        raise RuntimeError("offline Workbench run did not report completion")
    run_id = fields[0]
    run_dir = records / run_id
    verify = run_bounded(
        [
            sys.executable, "-m", "harness_workbench",
            "--root", str(records), "verify", run_id,
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
        raise RuntimeError("offline Workbench store failed hwb verify")
    record = json.loads((run_dir / "record.json").read_text(encoding="utf-8"))
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
        raise RuntimeError("Workbench attempt order does not reconcile with call control")
    if record["extras"]["freeze"]["baseline"] != "compared" or record["extras"]["freeze"]["drifted"]:
        raise RuntimeError("offline Workbench freeze baseline did not compare cleanly")
    stdout = run_dir / "steps" / f"{subject}-agent-task" / "attempts" / "0" / "stdout.bin"
    expected = episode_path.read_bytes()
    if stdout.read_bytes() != expected:
        raise RuntimeError("Workbench store did not retain the exact episode bytes")
    return run_dir, record


def run_offline_campaign(destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError(f"offline destination already exists: {destination}")
    destination.mkdir(mode=0o700, parents=True)
    bundle = destination / "bundle"
    records = destination / "records"
    session = destination / "session"
    episodes = bundle / "episodes"
    specs = bundle / "specs"
    for path in (bundle, records, episodes, specs):
        path.mkdir()
    task, archive, fake_plan = build_conformance_bundle(bundle)
    supervisor = ControlPlaneSupervisor(
        session,
        campaign_nonce=secrets.token_hex(16),
        maximum_calls=5,
        authorized_phases={"offline-conformance"},
        phase_maximums={"offline-conformance": 5},
        usage_mode="injected",
        usage_snapshots=[
            {"schema": "offline-usage/v0.1", "metered": False}
            for _ in SUBJECTS
        ],
    )
    control = supervisor.control
    broker = supervisor.broker
    runs: list[dict[str, Any]] = []
    store_rows: dict[str, Any] = {}
    for index, subject in enumerate(SUBJECTS):
        run = run_episode(
            subject=subject,
            task=task,
            workspace_archive=archive,
            fake_plan=fake_plan,
            store_nonce=f"offline-{subject}-{secrets.token_hex(8)}",
            request_id=f"offline-{index}-{subject}",
            control=control,
            broker=broker,
        )
        episode_path = episodes / f"{subject}.json"
        _write_json(episode_path, run)
        run_dir, record = _store_episode(
            subject=subject,
            episode_path=episode_path,
            spec_root=specs,
            records=records,
        )
        runs.append(run)
        store_rows[subject] = {
            "run_id": record["run_id"],
            "run_store_tree_sha256": canon.digest_tree(str(run_dir)),
        }
    comparison = compare_exact_five(runs, task=task, workspace_archive=archive)
    _write_json(destination / "comparison.json", comparison)
    checkpoint = build_phase_checkpoint(
        journal=control.journal,
        registry=broker.registry,
        store_digests={
            subject: row["run_store_tree_sha256"] for subject, row in store_rows.items()
        },
        comparison_sha256=canonical_sha256(comparison),
        usage_sha256=canonical_sha256({"schema": "offline-usage/v0.1", "metered": False}),
        cleanup_receipts=broker.receipts,
    )
    _write_json(destination / "phase-checkpoint.json", checkpoint)
    if not checkpoint["eligible"]:
        raise RuntimeError("offline phase checkpoint is ineligible")
    if not validate_prefix(control.journal, checkpoint["journal_prefix"]):
        raise RuntimeError("call-control checkpoint prefix does not validate")
    if not validate_prefix(broker.registry, checkpoint["registry_prefix"]):
        raise RuntimeError("process-registry checkpoint prefix does not validate")
    allocated_calls = control.status()["allocated_calls"]
    shutdown = supervisor.close()
    credential_scan = scan_credentials(destination, credential_values(os.environ))
    _write_json(destination / "credential-scan.json", credential_scan)
    report = {
        "schema": "cross-harness-agent-task-offline-conformance/v0.1",
        "passed": comparison["passed"] and checkpoint["eligible"] and credential_scan["passed"],
        "provider_calls": allocated_calls,
        "subjects": list(SUBJECTS),
        "stores": store_rows,
        "comparison_sha256": canonical_sha256(comparison),
        "checkpoint_sha256": canonical_sha256(checkpoint),
        "control_plane_shutdown": shutdown,
    }
    _write_json(destination / "offline-report.json", report)
    return report
