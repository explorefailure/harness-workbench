#!/usr/bin/env python3
"""Offline review of one retained exact-five fake smoke checkpoint."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from harness_workbench import canon
from harness_workbench.capture import run_bounded

from agent_task_schema import SUBJECTS, bytes_sha256, canonical_sha256, validate_run
from agent_task_validate import scan_credentials


EXPECTED_REVIEW_FILES = {
    "comparison.json",
    "credential-scan.json",
    "offline-review.json",
    "permit-usage.json",
    "phase-checkpoint.json",
}


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict:
            raise ValueError("not an object")
        return value
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"cannot read {path.name}: {error}")
        return None


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    if environment.get("PYTHONPATH"):
        environment["PYTHONPATH"] = os.pathsep.join(
            str((Path.cwd() / item).resolve())
            if not Path(item).is_absolute()
            else item
            for item in environment["PYTHONPATH"].split(os.pathsep)
        )
    return environment


def _prefix_valid(path: Path, prefix: Any) -> bool:
    if (
        type(prefix) is not dict
        or set(prefix) != {"bytes", "sha256"}
        or type(prefix["bytes"]) is not int
        or prefix["bytes"] < 0
        or type(prefix["sha256"]) is not str
    ):
        return False
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        raw = stream.read(prefix["bytes"])
    return len(raw) == prefix["bytes"] and bytes_sha256(raw) == prefix["sha256"]


def review_fake_smoke_checkpoint(
    destination: Path,
    *,
    configured_credentials: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Reopen retained evidence without producer objects or live services."""
    errors: list[str] = []
    if destination.is_symlink():
        return {
            "schema": "agent-task-fake-smoke-offline-review/v0.1",
            "passed": False,
            "errors": ["live destination is an alias"],
            "subjects": [],
            "stores": 0,
            "credential_rescan": {
                "passed": False, "findings": ["alias prevents safe rescan"],
            },
        }
    destination = destination.resolve(strict=True)
    aliases = [path for path in destination.rglob("*") if path.is_symlink()]
    if aliases:
        errors.append("retained evidence contains an alias")
    records = destination / "records" / "write-smoke"
    review_root = destination / "review" / "write-smoke"
    if records.is_symlink() or not records.is_dir():
        errors.append("write-smoke records root is not a real directory")
        stores: list[Path] = []
    else:
        stores = sorted(records.iterdir())
    if len(stores) != len(SUBJECTS):
        errors.append("write-smoke records root is not exact-five")
    if any(path.is_symlink() or not path.is_dir() for path in stores):
        errors.append("write-smoke records root contains a partial store")
    if review_root.is_symlink() or not review_root.is_dir():
        errors.append("write-smoke review root is not a real directory")
        review_names: set[str] = set()
    else:
        review_names = {path.name for path in review_root.iterdir()}
    if not review_names.issubset(EXPECTED_REVIEW_FILES) or not {
        "comparison.json", "credential-scan.json", "permit-usage.json",
        "phase-checkpoint.json",
    }.issubset(review_names):
        errors.append("write-smoke review file set is incomplete or unexpected")

    comparison = _load_json(review_root / "comparison.json", errors)
    usage = _load_json(review_root / "permit-usage.json", errors)
    retained_scan = _load_json(review_root / "credential-scan.json", errors)
    checkpoint = _load_json(review_root / "phase-checkpoint.json", errors)
    bundle_manifest = _load_json(
        destination / "bundle" / "bundle-manifest.json", errors
    )
    if bundle_manifest is not None:
        files = bundle_manifest.get("files")
        if (
            bundle_manifest.get("schema") != "agent-task-live-bundle-manifest/v0.1"
            or type(files) is not dict
            or set(files) != {
                "task.json", "workspace.zip", "fake-provider-plan.json",
            }
        ):
            errors.append("retained bundle manifest is invalid")
        else:
            for name, expected in files.items():
                path = destination / "bundle" / name
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or bytes_sha256(path.read_bytes()) != expected
                ):
                    errors.append(f"retained bundle file digest disagrees: {name}")
    if comparison is not None and (
        comparison.get("passed") is not True
        or set(comparison.get("subjects", {})) != set(SUBJECTS)
    ):
        errors.append("retained smoke comparison is not exact-five passing")
    if usage is not None and (
        type(usage.get("snapshots")) is not list
        or len(usage["snapshots"]) != len(SUBJECTS)
    ):
        errors.append("retained permit usage is not exact-five")
    if retained_scan is not None and retained_scan.get("passed") is not True:
        errors.append("retained credential scan did not pass")

    checkpoint_stores: dict[str, str] = {}
    if checkpoint is None:
        pass
    elif (
        checkpoint.get("schema") != "agent-task-phase-checkpoint/v0.1"
        or checkpoint.get("eligible") is not True
        or type(checkpoint.get("stores")) is not dict
        or set(checkpoint["stores"]) != set(SUBJECTS)
    ):
        errors.append("retained phase checkpoint is not exact-five eligible")
    else:
        checkpoint_stores = checkpoint["stores"]
        if comparison is not None and checkpoint.get(
            "comparison_sha256"
        ) != canonical_sha256(comparison):
            errors.append("checkpoint comparison digest disagrees")
        if usage is not None and checkpoint.get("usage_sha256") != canonical_sha256(
            usage
        ):
            errors.append("checkpoint permit-usage digest disagrees")
        expected_checkpoint_sha256 = canonical_sha256({
            "stores": dict(sorted(checkpoint_stores.items())),
            "comparison_sha256": checkpoint.get("comparison_sha256"),
            "usage_sha256": checkpoint.get("usage_sha256"),
        })
        if checkpoint.get("checkpoint_sha256") != expected_checkpoint_sha256:
            errors.append("checkpoint self-digest disagrees")
        cleanup = checkpoint.get("smoke_cleanup_receipts")
        if (
            type(cleanup) is not list
            or len(cleanup) != len(SUBJECTS)
            or any(
                row.get("kind") != "clean_self_issued"
                or row.get("group_alive_after_cleanup") is not False
                for row in cleanup
                if type(row) is dict
            )
            or any(type(row) is not dict for row in cleanup)
        ):
            errors.append("checkpoint cleanup receipts are not exact-five clean")
        try:
            if not _prefix_valid(
                destination / "session" / "call-control.jsonl",
                checkpoint["journal_prefix"],
            ):
                errors.append("checkpoint call-control prefix disagrees")
            if not _prefix_valid(
                destination / "session" / "process-registry.jsonl",
                checkpoint["registry_prefix"],
            ):
                errors.append("checkpoint process-registry prefix disagrees")
        except (KeyError, OSError, TypeError, ValueError):
            errors.append("checkpoint durable prefixes are invalid")

    observed_subjects: set[str] = set()
    environment = _environment()
    for store in stores:
        if store.is_symlink() or not store.is_dir():
            continue
        outputs = list(store.glob("steps/*/attempts/0/stdout.bin"))
        subject = None
        if len(outputs) != 1:
            errors.append(f"{store.name}: sealed episode output is not singular")
        else:
            try:
                episode = validate_run(json.loads(outputs[0].read_text(encoding="utf-8")))
                subject = episode["subject"]
            except (OSError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"{store.name}: sealed episode is invalid: {error}")
        if subject in observed_subjects:
            errors.append(f"{store.name}: duplicate retained subject {subject}")
        elif subject is not None:
            observed_subjects.add(subject)
            if checkpoint_stores.get(subject) != canon.digest_tree(str(store)):
                errors.append(f"{store.name}: checkpoint store digest disagrees")
        completed = run_bounded(
            [
                sys.executable, "-m", "harness_workbench", "--root",
                str(records), "verify", store.name,
            ],
            cwd=destination / "bundle",
            env=environment,
            timeout=15,
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
            or b"conforms: yes" not in completed.stdout
        ):
            errors.append(f"{store.name}: hwb verify failed")
    if observed_subjects != set(SUBJECTS):
        errors.append("sealed store subjects are not exact-five")
    rescanned = (
        {"passed": False, "findings": ["alias prevents safe rescan"]}
        if aliases
        else scan_credentials(destination, configured_credentials)
    )
    if not rescanned["passed"]:
        errors.append("offline credential rescan failed")
    return {
        "schema": "agent-task-fake-smoke-offline-review/v0.1",
        "passed": not errors,
        "errors": errors,
        "subjects": sorted(observed_subjects),
        "stores": len(stores),
        "credential_rescan": rescanned,
    }
