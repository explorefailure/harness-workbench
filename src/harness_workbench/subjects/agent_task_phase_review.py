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

from agent_task_schema import (
    CAMPAIGN_SCHEMA,
    PHASE_CANDIDATE_SCHEMA,
    SUBJECTS,
    bytes_sha256,
    canonical_sha256,
    validate_run,
)
from agent_task_validate import (
    compare_exact_five_matrix,
    scan_credentials,
    validate_retained_run,
)


PHASES = ("write-smoke", "repair-matrix")
STEP_MODULE_INPUTS = (
    "agent_task_archives.py",
    "agent_task_authorization.py",
    "agent_task_broker.py",
    "agent_task_control.py",
    "agent_task_coordinator.py",
    "agent_task_fake_provider.py",
    "agent_task_phase_review.py",
    "agent_task_process.py",
    "agent_task_providers.py",
    "agent_task_routes.py",
    "agent_task_runtime.py",
    "agent_task_schema.py",
    "agent_task_services.py",
    "agent_task_specs.py",
    "agent_task_store.py",
    "agent_task_validate.py",
)
STATIC_INPUTS = (
    "agent_task_step.py",
    *STEP_MODULE_INPUTS,
    "task.json",
    "workspace.zip",
    "fake-provider-plan.json",
    "execution-plan.json",
)


EXPECTED_REVIEW_FILES = {
    "comparison.json",
    "credential-scan.json",
    "offline-review.json",
    "permit-usage.json",
    "phase-checkpoint.json",
    "phase-candidate.json",
}
EXPECTED_MATRIX_REVIEW_FILES = {
    "cleanup-receipts.json",
    "comparison.json",
    "credential-scan.json",
    "offline-review.json",
    "permit-usage.json",
    "phase-candidate.json",
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


def _argument(argv: Any, name: str) -> Any:
    if type(argv) is not list:
        return None
    try:
        index = argv.index(name)
        return argv[index + 1]
    except (ValueError, IndexError):
        return None


def _store_evidence(store: Path, errors: list[str]) -> dict[str, str] | None:
    record = store / "record.json"
    integrity = store / "integrity.json"
    if (
        record.is_symlink()
        or not record.is_file()
        or integrity.is_symlink()
        or not integrity.is_file()
    ):
        errors.append(f"{store.name}: record or integrity file is unavailable")
        return None
    try:
        return {
            "run_id": store.name,
            "run_store_tree_sha256": canon.digest_tree(str(store)),
            "record_json_sha256": canon.digest_file(str(record)),
            "integrity_json_sha256": canon.digest_file(str(integrity)),
        }
    except OSError as error:
        errors.append(f"{store.name}: cannot digest store evidence: {error}")
        return None


def _review_pre_call_specs(
    destination: Path, bundle_manifest: dict[str, Any], errors: list[str]
) -> None:
    root = destination / "bundle" / "precall-specs"
    if root.is_symlink() or not root.is_dir():
        errors.append("pre-call spec root is not a real directory")
        return
    if canon.digest_tree(str(root)) != bundle_manifest.get(
        "precall_spec_tree_sha256"
    ):
        errors.append("pre-call spec tree digest disagrees")
    planned = bundle_manifest.get("precall_specs")
    execution_outer = _load_json(
        destination / "bundle" / "execution-plan.json", errors
    )
    execution_plan = (
        execution_outer.get("execution_plan")
        if type(execution_outer) is dict else None
    )
    planned_documents = (
        execution_plan.get("inputs", {}).get("specs")
        if type(execution_plan) is dict else None
    )
    planned_nonces = (
        execution_plan.get("store_nonces")
        if type(execution_plan) is dict else None
    )
    if type(planned) is not dict or set(planned) != set(PHASES):
        errors.append("pre-call spec manifest phases are not exact")
        return
    if (
        type(planned_documents) is not dict
        or set(planned_documents) != set(PHASES)
        or type(planned_nonces) is not dict
        or set(planned_nonces) != set(PHASES)
    ):
        errors.append("pre-call execution-plan spec phases are not exact")
        return
    phase_children = {path.name: path for path in root.iterdir()}
    if set(phase_children) != set(PHASES):
        errors.append("pre-call spec directory phases are not exact")
        return
    observed_rows: dict[str, Any] = {}
    for phase in PHASES:
        phase_root = phase_children[phase]
        if phase_root.is_symlink() or not phase_root.is_dir():
            errors.append(f"pre-call phase root is not a real directory: {phase}")
            continue
        if type(planned[phase]) is not dict:
            errors.append(f"pre-call {phase} manifest subjects are invalid")
            continue
        if (
            type(planned_documents[phase]) is not dict
            or type(planned_nonces[phase]) is not dict
        ):
            errors.append(f"pre-call {phase} execution-plan subjects are invalid")
            continue
        subjects = {path.name: path for path in phase_root.iterdir()}
        if (
            set(subjects) != set(SUBJECTS)
            or set(planned[phase]) != set(SUBJECTS)
            or set(planned_documents[phase]) != set(SUBJECTS)
            or set(planned_nonces[phase]) != set(SUBJECTS)
        ):
            errors.append(f"pre-call {phase} subject set is not exact-five")
            continue
        observed_rows[phase] = {}
        for subject in SUBJECTS:
            own = subjects[subject]
            expected_names = {
                *STATIC_INPUTS, f"{subject}.json", f"{subject}.freeze.lock",
            }
            if (
                own.is_symlink() or not own.is_dir()
                or {path.name for path in own.iterdir()} != expected_names
            ):
                errors.append(f"pre-call spec file set disagrees: {phase}/{subject}")
                continue
            expected_paths = [own / name for name in expected_names]
            if any(path.is_symlink() or not path.is_file() for path in expected_paths):
                errors.append(f"pre-call spec input is not regular: {phase}/{subject}")
                continue
            spec = _load_json(own / f"{subject}.json", errors)
            lock = _load_json(own / f"{subject}.freeze.lock", errors)
            if spec is None or lock is None:
                continue
            features = spec.get("features")
            names = (
                [
                    row.get("name") if type(row) is dict else None
                    for row in features
                ]
                if type(features) is list else []
            )
            draws = 1 if phase == "write-smoke" else 3
            steps = spec.get("steps")
            step = steps[0] if type(steps) is list and len(steps) == 1 else None
            if (
                spec.get("schema") != "hwbspec/v0.1"
                or names != ["freeze", "receipt", "retry", "sample", "timing"]
                or features[2].get("config") != {"max": 2}
                or features[3].get("config") != {"n": draws}
                or type(step) is not dict
                or step.get("id") != f"{subject}-agent-task"
                or step.get("inputs") != list(STATIC_INPUTS)
                or spec.get("step_timeout_ms") is not None
            ):
                errors.append(f"pre-call spec semantics disagree: {phase}/{subject}")
            argv = step.get("argv") if type(step) is dict else None
            if (
                type(argv) is not list
                or len(argv) != 16
                or type(argv[0]) is not str
                or not Path(argv[0]).is_absolute()
                or argv[1] != "agent_task_step.py"
                or _argument(argv, "--phase") != phase
                or _argument(argv, "--subject") != subject
                or _argument(argv, "--store-nonce")
                != planned_nonces[phase][subject]
                or _argument(argv, "--task") != "task.json"
                or _argument(argv, "--workspace-archive") != "workspace.zip"
                or _argument(argv, "--transport-plan")
                != "fake-provider-plan.json"
                or _argument(argv, "--execution-plan") != "execution-plan.json"
            ):
                errors.append(f"pre-call spec invocation disagrees: {phase}/{subject}")
            if canonical_sha256(spec) != planned[phase][subject]:
                errors.append(f"pre-call planned spec digest disagrees: {phase}/{subject}")
            execution_spec = planned_documents[phase][subject]
            if (
                type(execution_spec) is not dict
                or execution_spec.get("document") != spec
                or execution_spec.get("sha256") != canonical_sha256(spec)
            ):
                errors.append(
                    f"pre-call execution-plan spec disagrees: {phase}/{subject}"
                )
            observed_inputs = {
                name: canon.digest_file(str(own / name)) for name in STATIC_INPUTS
            }
            if lock != {"digests": observed_inputs}:
                errors.append(f"pre-call freeze lock disagrees: {phase}/{subject}")
            observed_rows[phase][subject] = {
                "spec_sha256": canonical_sha256(spec),
                "spec_file_sha256": bytes_sha256(
                    (own / f"{subject}.json").read_bytes()
                ),
                "freeze_lock_sha256": bytes_sha256(
                    (own / f"{subject}.freeze.lock").read_bytes()
                ),
                "inputs": observed_inputs,
            }
    if (
        set(observed_rows) == set(PHASES)
        and all(set(observed_rows[phase]) == set(SUBJECTS) for phase in PHASES)
    ):
        observed_assembly = {
            "schema": "agent-task-precall-spec-assembly/v0.1",
            "specs": observed_rows,
            "tree_sha256": canon.digest_tree(str(root)),
        }
        if canonical_sha256(observed_assembly) != bundle_manifest.get(
            "precall_spec_assembly_sha256"
        ):
            errors.append("pre-call spec assembly digest disagrees")


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
            "store_evidence": {},
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
                "execution-plan.json",
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
            execution_plan = _load_json(
                destination / "bundle" / "execution-plan.json", errors
            )
            if (
                execution_plan is not None
                and (
                    type(execution_plan) is not dict
                    or set(execution_plan) != {
                        "execution_plan", "execution_plan_sha256",
                    }
                    or canonical_sha256(execution_plan.get("execution_plan"))
                    != execution_plan.get("execution_plan_sha256")
                    or execution_plan.get("execution_plan_sha256")
                    != bundle_manifest.get("execution_plan_sha256")
                )
            ):
                errors.append("retained execution plan digest disagrees")
        _review_pre_call_specs(destination, bundle_manifest, errors)
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
    stores_by_subject: dict[str, dict[str, str]] = {}
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
            evidence = _store_evidence(store, errors)
            if evidence is not None:
                stores_by_subject[subject] = evidence
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
        "store_evidence": dict(sorted(stores_by_subject.items())),
        "credential_rescan": rescanned,
    }


def review_fake_repair_matrix(
    destination: Path,
    *,
    configured_credentials: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Reconstruct and validate the retained three-draw matrix offline."""
    errors: list[str] = []
    if destination.is_symlink():
        return {
            "schema": "agent-task-fake-matrix-offline-review/v0.1",
            "passed": False,
            "errors": ["live destination is an alias"],
            "subjects": [],
            "stores": 0,
            "store_evidence": {},
            "credential_rescan": {
                "passed": False, "findings": ["alias prevents safe rescan"],
            },
        }
    destination = destination.resolve(strict=True)
    aliases = [path for path in destination.rglob("*") if path.is_symlink()]
    if aliases:
        errors.append("retained evidence contains an alias")
    records = destination / "records" / "repair-matrix"
    review_root = destination / "review" / "repair-matrix"
    if records.is_symlink() or not records.is_dir():
        errors.append("repair-matrix records root is not a real directory")
        stores: list[Path] = []
    else:
        stores = sorted(records.iterdir())
    if len(stores) != len(SUBJECTS):
        errors.append("repair-matrix records root is not exact-five")
    if any(path.is_symlink() or not path.is_dir() for path in stores):
        errors.append("repair-matrix records root contains a partial store")
    if review_root.is_symlink() or not review_root.is_dir():
        errors.append("repair-matrix review root is not a real directory")
        review_names: set[str] = set()
    else:
        review_names = {path.name for path in review_root.iterdir()}
    required_review = {
        "cleanup-receipts.json", "comparison.json", "credential-scan.json",
        "permit-usage.json",
    }
    if (
        not review_names.issubset(EXPECTED_MATRIX_REVIEW_FILES)
        or not required_review.issubset(review_names)
    ):
        errors.append("repair-matrix review file set is incomplete or unexpected")

    comparison = _load_json(review_root / "comparison.json", errors)
    usage = _load_json(review_root / "permit-usage.json", errors)
    cleanup = _load_json(review_root / "cleanup-receipts.json", errors)
    retained_scan = _load_json(review_root / "credential-scan.json", errors)
    bundle_manifest = _load_json(
        destination / "bundle" / "bundle-manifest.json", errors
    )
    execution_outer = _load_json(
        destination / "bundle" / "execution-plan.json", errors
    )
    task = _load_json(destination / "bundle" / "task.json", errors)
    archive_path = destination / "bundle" / "workspace.zip"
    try:
        if archive_path.is_symlink() or not archive_path.is_file():
            raise ValueError("not a regular file")
        workspace_archive = archive_path.read_bytes()
    except (OSError, ValueError) as error:
        errors.append(f"cannot read workspace.zip: {error}")
        workspace_archive = b""
    execution_plan = (
        execution_outer.get("execution_plan")
        if type(execution_outer) is dict else None
    )
    if bundle_manifest is not None:
        files = bundle_manifest.get("files")
        if (
            bundle_manifest.get("schema")
            != "agent-task-live-bundle-manifest/v0.1"
            or type(files) is not dict
            or set(files) != {
                "task.json", "workspace.zip", "fake-provider-plan.json",
                "execution-plan.json",
            }
        ):
            errors.append("retained bundle manifest is invalid")
        else:
            for name, expected in files.items():
                path = destination / "bundle" / name
                if (
                    path.is_symlink() or not path.is_file()
                    or bytes_sha256(path.read_bytes()) != expected
                ):
                    errors.append(f"retained bundle file digest disagrees: {name}")
        _review_pre_call_specs(destination, bundle_manifest, errors)
    if (
        type(execution_plan) is not dict
        or canonical_sha256(execution_plan)
        != execution_outer.get("execution_plan_sha256")
        or execution_outer.get("execution_plan_sha256")
        != (bundle_manifest or {}).get("execution_plan_sha256")
    ):
        errors.append("retained execution plan digest disagrees")

    if comparison is not None:
        subjects = comparison.get("subjects")
        if (
            comparison.get("schema")
            != "cross-harness-agent-task-comparison/v0.1"
            or comparison.get("phase") != "repair-matrix"
            or comparison.get("draws_per_subject") != 3
            or comparison.get("passed") is not True
            or type(subjects) is not dict
            or set(subjects) != set(SUBJECTS)
            or any(
                row.get("passed") is not True
                or type(row.get("draws")) is not list
                or len(row["draws"]) != 3
                or any(draw.get("passed") is not True for draw in row["draws"])
                for row in subjects.values()
                if type(row) is dict
            )
            or any(type(row) is not dict for row in subjects.values())
        ):
            errors.append("retained matrix comparison is not exact-five by three")
    if usage is not None:
        snapshots = usage.get("snapshots")
        if (
            usage.get("schema") != "agent-task-matrix-permit-usage/v0.1"
            or type(snapshots) is not list
            or len(snapshots) != 15
        ):
            errors.append("retained matrix permit usage is not exact-fifteen")
        else:
            names: set[str] = set()
            for row in snapshots:
                if type(row) is not dict or set(row) != {
                    "path", "sha256", "document_sha256",
                }:
                    errors.append("retained matrix permit usage row is malformed")
                    continue
                name = row["path"]
                path = destination / "session" / "permit-usage" / str(name)
                if (
                    type(name) is not str or name != Path(name).name
                    or not name.startswith("permit-") or name in names
                    or path.is_symlink() or not path.is_file()
                ):
                    errors.append("retained matrix permit usage path is invalid")
                    continue
                names.add(name)
                raw = path.read_bytes()
                try:
                    document = json.loads(raw)
                except (ValueError, json.JSONDecodeError):
                    document = None
                if (
                    bytes_sha256(raw) != row["sha256"]
                    or canonical_sha256(document) != row["document_sha256"]
                ):
                    errors.append(f"matrix permit usage digest disagrees: {name}")
    if cleanup is not None:
        receipts = cleanup.get("receipts")
        if (
            cleanup.get("schema") != "agent-task-matrix-cleanup/v0.1"
            or type(receipts) is not list or len(receipts) != 15
            or any(type(row) is not dict for row in receipts)
            or any(
                row.get("phase") != "repair-matrix"
                or row.get("kind") != "clean_self_issued"
                or row.get("group_alive_after_cleanup") is not False
                for row in receipts if type(row) is dict
            )
            or len({row.get("registration_id") for row in receipts}) != 15
        ):
            errors.append("retained matrix cleanup is not exact-fifteen clean")
    if retained_scan is not None and retained_scan.get("passed") is not True:
        errors.append("retained matrix credential scan did not pass")

    environment = _environment()
    observed_subjects: set[str] = set()
    observed_runs: list[dict[str, Any]] = []
    stores_by_subject: dict[str, dict[str, str]] = {}
    for store in stores:
        if store.is_symlink() or not store.is_dir():
            continue
        attempts_path = store / "attempts.jsonl"
        try:
            attempts = [
                json.loads(line)
                for line in attempts_path.read_text(encoding="utf-8").splitlines()
            ]
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"{store.name}: attempts are unreadable: {error}")
            attempts = []
        if len(attempts) != 3 or any(
            row.get("n") != draw
            or row.get("exit") != 0
            or row.get("caused_by") != [
                {"feature": "sample", "i": draw},
                {"feature": "retry", "i": 0},
            ]
            for draw, row in enumerate(attempts)
        ):
            errors.append(f"{store.name}: attempts are not exact three-draw success")
        outputs = [
            store / "steps" / child.name / "attempts" / str(draw) / "stdout.bin"
            for child in (store / "steps").iterdir()
            for draw in range(3)
        ] if (store / "steps").is_dir() else []
        if len(outputs) != 3:
            errors.append(f"{store.name}: sealed matrix outputs are not exact-three")
            episodes: list[dict[str, Any]] = []
        else:
            episodes = []
            for output in outputs:
                try:
                    episode = validate_run(json.loads(output.read_text(encoding="utf-8")))
                    episodes.append(episode)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    errors.append(f"{store.name}: sealed episode is invalid: {error}")
        subjects = {episode.get("subject") for episode in episodes}
        subject = next(iter(subjects)) if len(subjects) == 1 else None
        if subject is None or subject not in SUBJECTS:
            errors.append(f"{store.name}: sealed episode subject is not singular")
        elif subject in observed_subjects:
            errors.append(f"{store.name}: duplicate retained subject {subject}")
        else:
            observed_subjects.add(subject)
            evidence = _store_evidence(store, errors)
            if evidence is not None:
                stores_by_subject[subject] = evidence
            if (
                type(execution_plan) is not dict
                or any(
                    episode.get("store_nonce")
                    != execution_plan.get("store_nonces", {}).get(
                        "repair-matrix", {}
                    ).get(subject)
                    for episode in episodes
                )
            ):
                errors.append(f"{store.name}: matrix store nonce disagrees")
        if type(task) is dict and workspace_archive:
            for draw, episode in enumerate(episodes):
                validation = validate_retained_run(
                    episode, task=task, workspace_archive=workspace_archive
                )
                if not validation["passed"]:
                    errors.extend(
                        f"{store.name} draw {draw}: {error}"
                        for error in validation["errors"]
                    )
        observed_runs.extend(episodes)
        completed = run_bounded(
            [
                sys.executable, "-m", "harness_workbench", "--root",
                str(records), "verify", store.name,
            ],
            cwd=destination / "bundle", env=environment, timeout=15,
            stdout_limit=1024 * 1024, stderr_limit=1024 * 1024,
            termination_grace=1.0, forward_signals=False,
        )
        if (
            completed.returncode != 0
            or completed.termination_reason is not None
            or completed.stdout_overflow or completed.stderr_overflow
            or completed.group_alive_after_cleanup
            or b"conforms: yes" not in completed.stdout
        ):
            errors.append(f"{store.name}: hwb verify failed")
    if observed_subjects != set(SUBJECTS):
        errors.append("sealed matrix store subjects are not exact-five")
    if type(task) is dict and workspace_archive:
        replayed = compare_exact_five_matrix(
            observed_runs, task=task, workspace_archive=workspace_archive
        )
        if comparison is not None and replayed != comparison:
            errors.append("retained matrix comparison does not independently replay")
    rescanned = (
        {"passed": False, "findings": ["alias prevents safe rescan"]}
        if aliases else scan_credentials(destination, configured_credentials)
    )
    if not rescanned["passed"]:
        errors.append("offline matrix credential rescan failed")
    return {
        "schema": "agent-task-fake-matrix-offline-review/v0.1",
        "passed": not errors,
        "errors": errors,
        "subjects": sorted(observed_subjects),
        "stores": len(stores),
        "store_evidence": dict(sorted(stores_by_subject.items())),
        "comparison_sha256": (
            canonical_sha256(comparison) if comparison is not None else None
        ),
        "usage_sha256": canonical_sha256(usage) if usage is not None else None,
        "cleanup_sha256": canonical_sha256(cleanup) if cleanup is not None else None,
        "credential_rescan": rescanned,
    }


def _load_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("not a regular file")
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
        if not rows or any(type(row) is not dict for row in rows):
            raise ValueError("not a nonempty object stream")
        return rows
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"cannot read {path.name}: {error}")
        return []


def _closure(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("not a regular file")
        raw = path.read_bytes()
        return {"bytes": len(raw), "sha256": bytes_sha256(raw)}
    except (OSError, ValueError) as error:
        errors.append(f"cannot close {path.name}: {error}")
        return {"bytes": 0, "sha256": None}


def _review_control_plane_closure(
    destination: Path,
    shutdown: Any,
    errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    session = destination / "session"
    journal_path = session / "call-control.jsonl"
    registry_path = session / "process-registry.jsonl"
    stop_record = session / "supervisor-stop.json"
    if stop_record.exists() or stop_record.is_symlink():
        errors.append("supervisor stop record makes the campaign ineligible")
    journal = _load_jsonl(journal_path, errors)
    registry = _load_jsonl(registry_path, errors)
    journal_closure = _closure(journal_path, errors)
    registry_closure = _closure(registry_path, errors)
    expected_shutdown = {
        "broker": {
            "schema": "agent-task-process-registry/v0.1",
            "event": "broker_stopped",
            "kind": "clean_self_issued",
        },
        "call_control": {"kind": "clean_self_issued"},
    }
    if shutdown != expected_shutdown:
        errors.append("control-plane shutdown receipts are not exact and clean")
    if any(
        row.get("kind") == "abnormal_supervisor_witnessed"
        or row.get("event") == "control_plane_termination"
        for row in registry
    ):
        errors.append("process registry contains abnormal supervisor witnessing")
    if any(row.get("event") == "hard_stop" for row in journal):
        errors.append("call-control journal contains a hard stop")
    terminal = journal[-1] if journal else {}
    if terminal != {
        "schema": "agent-task-call-control/v0.1",
        "event": "service_stopped",
        "kind": "clean_self_issued",
        "state": "ready",
        "allocated_calls": 20,
    }:
        errors.append("call-control journal has no exact clean terminal closure")
    allocated = [row for row in journal if row.get("event") == "permit_allocated"]
    released = [row for row in journal if row.get("event") == "provider_released"]
    completed = [row for row in journal if row.get("event") == "permit_completed"]
    if (
        [row.get("call_id") for row in allocated] != list(range(1, 21))
        or [row.get("call_id") for row in released] != list(range(1, 21))
        or [row.get("call_id") for row in completed] != list(range(1, 21))
        or any(
            row.get("result") != "success"
            or row.get("cleanup_proved") is not True
            or row.get("next_state") != "ready"
            for row in completed
        )
    ):
        errors.append("call-control journal is not twenty closed permit lifecycles")
    registered = [row for row in registry if row.get("event") == "registered"]
    cleanup = [row for row in registry if row.get("event") == "cleanup"]
    registered_ids = [row.get("registration_id") for row in registered]
    cleanup_ids = [row.get("registration_id") for row in cleanup]
    if (
        len(registered_ids) != 20
        or len(set(registered_ids)) != 20
        or len(cleanup_ids) != 20
        or set(cleanup_ids) != set(registered_ids)
        or any(
            type(row.get("pid")) is not int
            or row.get("pgid") != row.get("pid")
            or row.get("phase") not in PHASES
            or type(row.get("platform_start_identity")) is not str
            or type(row.get("launcher_executable_identity")) is not str
            or type(row.get("executable_identity")) is not str
            for row in registered
        )
        or any(
            row.get("kind") != "clean_self_issued"
            or row.get("returncode") != 0
            or row.get("termination_reason") is not None
            or row.get("group_alive_after_cleanup") is not False
            for row in cleanup
        )
    ):
        errors.append("process registry is not twenty clean registered closures")
    expected_registry_tail = [
        expected_shutdown["broker"],
        {
            "schema": "agent-task-process-registry/v0.1",
            "event": "control_plane_shutdown",
            "kind": "clean_self_issued",
            "control_plane_child": "broker",
            "returncode": 0,
        },
        {
            "schema": "agent-task-process-registry/v0.1",
            "event": "control_plane_shutdown",
            "kind": "clean_self_issued",
            "control_plane_child": "call_control",
            "returncode": 0,
        },
    ]
    if registry[-3:] != expected_registry_tail:
        errors.append("process registry has no exact clean control-plane tail")
    return journal_closure, registry_closure


def _expected_phase_candidate(
    destination: Path,
    *,
    phase: str,
    execution_plan: dict[str, Any],
    bundle_manifest: dict[str, Any],
    phase_review: dict[str, Any],
    journal_closure: dict[str, Any],
    registry_closure: dict[str, Any],
    shutdown: dict[str, Any],
    smoke_checkpoint: dict[str, Any],
) -> dict[str, Any]:
    review_root = destination / "review" / phase
    cleanup_path = (
        review_root / "cleanup-receipts.json"
        if phase == "repair-matrix"
        else review_root / "phase-checkpoint.json"
    )
    return {
        "schema": PHASE_CANDIDATE_SCHEMA,
        "phase": phase,
        "eligible": True,
        "execution_plan_sha256": canonical_sha256(execution_plan),
        "bundle_manifest_file_sha256": bytes_sha256(
            (destination / "bundle" / "bundle-manifest.json").read_bytes()
        ),
        "task_sha256": execution_plan["inputs"]["task_sha256"],
        "workspace_archive_sha256": execution_plan["inputs"][
            "workspace_archive_sha256"
        ],
        "apparatus_map_sha256": bundle_manifest["apparatus_map_sha256"],
        "validator_program_sha256": bundle_manifest["validator_program_sha256"],
        "comparator_program_sha256": bundle_manifest[
            "comparator_program_sha256"
        ],
        "provider_pins_sha256": canonical_sha256(
            execution_plan["provider_pins"]
        ),
        "stores": phase_review["store_evidence"],
        "comparison_report_sha256": canonical_sha256(json.loads(
            (review_root / "comparison.json").read_text(encoding="utf-8")
        )),
        "usage_evidence_sha256": canonical_sha256(json.loads(
            (review_root / "permit-usage.json").read_text(encoding="utf-8")
        )),
        "credential_scan_file_sha256": bytes_sha256(
            (review_root / "credential-scan.json").read_bytes()
        ),
        "cleanup_evidence_file_sha256": bytes_sha256(cleanup_path.read_bytes()),
        "offline_review_file_sha256": bytes_sha256(
            (review_root / "offline-review.json").read_bytes()
        ),
        "smoke_checkpoint_sha256": canonical_sha256(smoke_checkpoint),
        "journal_closure": journal_closure,
        "registry_closure": registry_closure,
        "control_plane_shutdown": shutdown,
        "calls": {
            "nominal": 5 if phase == "write-smoke" else 15,
            "maximum": 13 if phase == "write-smoke" else 30,
        },
    }


def review_fake_campaign(
    destination: Path,
    *,
    candidate_documents: dict[str, dict[str, Any]] | None = None,
    campaign_document: dict[str, Any] | None = None,
    configured_credentials: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Independently reconstruct final candidates and the clean campaign."""
    errors: list[str] = []
    if destination.is_symlink():
        return {
            "schema": "agent-task-fake-campaign-offline-review/v0.1",
            "passed": False,
            "errors": ["live destination is an alias"],
        }
    destination = destination.resolve(strict=True)
    if candidate_documents is None:
        candidate_documents = {
            phase: _load_json(
                destination / "review" / phase / "phase-candidate.json", errors
            )
            for phase in PHASES
        }
    if campaign_document is None:
        campaign_document = _load_json(
            destination / "review" / "campaign.json", errors
        )
    if (
        type(candidate_documents) is not dict
        or set(candidate_documents) != set(PHASES)
        or any(type(value) is not dict for value in candidate_documents.values())
        or type(campaign_document) is not dict
    ):
        errors.append("final candidate document set is incomplete")
        return {
            "schema": "agent-task-fake-campaign-offline-review/v0.1",
            "passed": False,
            "errors": errors,
        }
    shutdown = campaign_document.get("control_plane_shutdown")
    journal_closure, registry_closure = _review_control_plane_closure(
        destination, shutdown, errors
    )
    smoke_review = review_fake_smoke_checkpoint(
        destination, configured_credentials=configured_credentials
    )
    matrix_review = review_fake_repair_matrix(
        destination, configured_credentials=configured_credentials
    )
    if not smoke_review["passed"]:
        errors.extend(f"smoke review: {error}" for error in smoke_review["errors"])
    if not matrix_review["passed"]:
        errors.extend(f"matrix review: {error}" for error in matrix_review["errors"])
    execution_outer = _load_json(
        destination / "bundle" / "execution-plan.json", errors
    )
    bundle_manifest = _load_json(
        destination / "bundle" / "bundle-manifest.json", errors
    )
    smoke_checkpoint = _load_json(
        destination / "review" / "write-smoke" / "phase-checkpoint.json", errors
    )
    if (
        execution_outer is None
        or type(execution_outer.get("execution_plan")) is not dict
        or bundle_manifest is None
        or smoke_checkpoint is None
    ):
        errors.append("campaign source evidence is incomplete")
    else:
        try:
            execution_plan = execution_outer["execution_plan"]
            expected_candidates = {
                phase: _expected_phase_candidate(
                    destination, phase=phase, execution_plan=execution_plan,
                    bundle_manifest=bundle_manifest,
                    phase_review=(
                        smoke_review if phase == "write-smoke" else matrix_review
                    ),
                    journal_closure=journal_closure,
                    registry_closure=registry_closure,
                    shutdown=shutdown,
                    smoke_checkpoint=smoke_checkpoint,
                )
                for phase in PHASES
            }
            for phase in PHASES:
                if candidate_documents[phase] != expected_candidates[phase]:
                    errors.append(f"{phase} phase candidate does not reconstruct")
            expected_campaign = {
                "schema": CAMPAIGN_SCHEMA,
                "eligible": True,
                "execution_plan_sha256": canonical_sha256(execution_plan),
                "phase_candidates": {
                    phase: {
                        "path": f"{phase}/phase-candidate.json",
                        "sha256": canonical_sha256(candidate_documents[phase]),
                    }
                    for phase in PHASES
                },
                "smoke_checkpoint_sha256": canonical_sha256(smoke_checkpoint),
                "journal_closure": journal_closure,
                "registry_closure": registry_closure,
                "control_plane_shutdown": shutdown,
            }
            if campaign_document != expected_campaign:
                errors.append("campaign manifest does not reconstruct")
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"campaign reconstruction failed closed: {error}")
    return {
        "schema": "agent-task-fake-campaign-offline-review/v0.1",
        "passed": not errors,
        "errors": errors,
        "journal_closure": journal_closure,
        "registry_closure": registry_closure,
    }
