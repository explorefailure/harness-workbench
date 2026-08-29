#!/usr/bin/env python3
"""Three-workspace declarative episode runtime for the finite offline path."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable

from harness_workbench import canon
from harness_workbench.capture import credential_values, minimal_environment

from agent_task_archives import (
    apply_effects_archive,
    build_effects_archive,
    extract_workspace_archive,
    snapshot_tree,
    validate_archive,
)
from agent_task_authorization import validate_live_topology
from agent_task_broker import SpawnBroker, build_phase_checkpoint, validate_prefix
from agent_task_coordinator import AuthorizedAttemptCoordinator, PreparedAttempt
from agent_task_control import CallControl
from agent_task_services import BrokerClient, CallControlClient
from agent_task_routes import normalize_fake_route
from agent_task_phase_review import review_fake_smoke_checkpoint
from agent_task_providers import FakeProviderTransport, ProviderTransport
from agent_task_schema import (
    RUN_SCHEMA,
    SUBJECTS,
    WORKSPACE_SCHEMA,
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    validate_task,
)
from agent_task_store import materialize_single_draw_store
from agent_task_validate import compare_exact_five, scan_credentials, validate_retained_run


HERE = Path(__file__).resolve().parent
FAKE_PROVIDER = HERE / "agent_task_fake_provider.py"


def _real_directory_child(parent: Path, name: str) -> Path:
    """Return one real directory child, creating it without following aliases."""
    if not name or name != Path(name).name:
        raise ValueError("retained directory name is not a basename")
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("retained directory parent is not a real directory")
    child = parent / name
    if child.is_symlink():
        raise ValueError("retained directory is an alias")
    if child.exists():
        if not child.is_dir():
            raise ValueError("retained directory path is not a directory")
    else:
        child.mkdir(mode=0o700)
    return child


def _write_json_exclusive(path: Path, value: Any) -> None:
    raw = json.dumps(value, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    _write_bytes_exclusive(path, raw)


def _write_bytes_exclusive(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _prepare_bound_fake_bundle(
    *,
    coordinator: AuthorizedAttemptCoordinator,
    task: dict[str, Any],
    workspace_archive: bytes,
    fake_plan_document: dict[str, Any],
) -> tuple[Path, str]:
    destination = coordinator.destination
    bundle = destination / "bundle"
    if bundle.is_symlink() or not bundle.is_dir() or any(bundle.iterdir()):
        raise ValueError("fake smoke bundle root is not fresh and empty")
    task_raw = canonical_bytes(task) + b"\n"
    fake_raw = canonical_bytes(fake_plan_document) + b"\n"
    planned = coordinator.plan["inputs"]
    expected = {
        "task.json": planned["task_file_sha256"],
        "workspace.zip": planned["workspace_archive_sha256"],
        "fake-provider-plan.json": planned["fake_transport_plan_sha256"],
    }
    supplied = {
        "task.json": bytes_sha256(task_raw),
        "workspace.zip": bytes_sha256(workspace_archive),
        "fake-provider-plan.json": bytes_sha256(fake_raw),
    }
    if supplied != expected:
        raise ValueError("fake smoke bundle bytes do not match the execution plan")
    untrusted = (task_raw, workspace_archive, fake_raw)
    for value in credential_values(os.environ):
        raw = value.encode("utf-8", errors="surrogatepass")
        if raw and any(raw in document for document in untrusted):
            raise ValueError("fake smoke bundle contains a configured credential")
    for name, raw in (
        ("task.json", task_raw),
        ("workspace.zip", workspace_archive),
        ("fake-provider-plan.json", fake_raw),
    ):
        _write_bytes_exclusive(bundle / name, raw)
    bundle_manifest = {
        "schema": "agent-task-live-bundle-manifest/v0.1",
        "execution_plan_sha256": coordinator.plan_result["execution_plan_sha256"],
        "files": expected,
        "apparatus_map_sha256": planned["apparatus_map_sha256"],
        "validator_program_sha256": planned["validator_program_sha256"],
        "comparator_program_sha256": planned["comparator_program_sha256"],
        "virtual_specs": {
            phase: {
                subject: row["sha256"]
                for subject, row in sorted(subjects.items())
            }
            for phase, subjects in sorted(planned["specs"].items())
        },
    }
    manifest_path = bundle / "bundle-manifest.json"
    _write_json_exclusive(manifest_path, bundle_manifest)
    return bundle / "fake-provider-plan.json", bytes_sha256(manifest_path.read_bytes())


def _assertions(entries: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[str]:
    current = {row["path"]: row for row in entries}
    errors: list[str] = []
    for wanted in expected:
        observed = current.get(wanted["path"])
        if wanted["kind"] == "absent":
            if observed is not None:
                errors.append(f"expected absent path exists: {wanted['path']}")
            continue
        if observed != wanted:
            errors.append(f"verification mismatch: {wanted['path']}")
    return errors


def run_episode(
    *,
    subject: str,
    task: dict[str, Any],
    workspace_archive: bytes,
    fake_plan: Path,
    store_nonce: str,
    request_id: str,
    control: CallControl | CallControlClient,
    broker: SpawnBroker | BrokerClient,
    phase: str = "offline-conformance",
    transport: ProviderTransport | None = None,
) -> dict[str, Any]:
    validate_task(task)
    archive_sha256 = bytes_sha256(workspace_archive)
    if archive_sha256 != task["workspace_archive_sha256"]:
        raise ValueError("task workspace archive digest does not match supplied bytes")
    validate_archive(
        workspace_archive,
        WORKSPACE_SCHEMA,
        maximum=task["limits"]["archive_bytes"],
    )
    request = {
        "phase": phase,
        "subject": subject,
        "store_nonce": store_nonce,
        "request_id": request_id,
    }
    if isinstance(control, CallControl):
        request["usage_gate"] = lambda: (
            {"schema": "offline-usage/v0.1", "metered": False}, True
        )
    permit = control.request(**request)
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hwb-agent-task-") as raw:
        root = Path(raw)
        precheck = root / "precheck"
        agent = root / "agent"
        postcheck = root / "postcheck"
        initial = extract_workspace_archive(workspace_archive, precheck)
        pre_before, _ = snapshot_tree(precheck)
        errors.extend(_assertions(pre_before, task["verification"]["pre"]))
        pre_after, _ = snapshot_tree(precheck)
        if pre_before != pre_after:
            errors.append("precheck verifier mutated its workspace")

        before = extract_workspace_archive(workspace_archive, agent)
        environment = minimal_environment(
            root / "provider-home",
            overrides={"PYTHONNOUSERSITE": "1"},
        )
        redactions = credential_values(environment)
        control.release(permit)
        provider_transport = transport or FakeProviderTransport(Path(sys.executable))
        capture, cleanup = broker.launch(
            provider_transport.command(
                subject=subject, workspace=agent, prompt=task["prompt"], plan=fake_plan
            ),
            cwd=HERE,
            env=environment,
            phase=phase,
            timeout=task["limits"]["episode_seconds"],
            stdout_limit=task["limits"]["stdout_bytes"],
            stderr_limit=task["limits"]["stderr_bytes"],
            redactions=redactions,
        )
        cleanup_proved = (
            cleanup["kind"] == "clean_self_issued"
            and not cleanup["group_alive_after_cleanup"]
        )
        capture_clean = (
            capture["returncode"] == 0
            and capture["termination_reason"] is None
            and not capture["group_alive_after_cleanup"]
            and not capture["stdout"]["overflow"]
            and not capture["stderr"]["overflow"]
            and capture["stdout"]["redaction_count"] == 0
            and capture["stderr"]["redaction_count"] == 0
        )
        control.complete(
            permit,
            result="success" if capture_clean else "fatal",
            cleanup_proved=cleanup_proved,
        )
        if not capture_clean:
            errors.append("fake provider capture or cleanup is not valid")
            lifecycle = None
        else:
            stdout_raw = base64.b64decode(capture["stdout"]["base64"], validate=True)
            try:
                lifecycle = normalize_fake_route(subject, stdout_raw)
            except ValueError as error:
                lifecycle = None
                errors.append(str(error))

        effects_raw, operations, after = build_effects_archive(
            before, agent, maximum=task["limits"]["effects_bytes"]
        )
        if operations != task["effects_policy"]["operations"]:
            errors.append("observed effects do not equal the declared exact policy")

        reconstructed = extract_workspace_archive(workspace_archive, postcheck)
        if reconstructed != initial:
            errors.append("independent postcheck input extraction disagrees")
        applied = apply_effects_archive(effects_raw, postcheck)
        if applied != after:
            errors.append("effects archive does not reconstruct the agent workspace")
        errors.extend(_assertions(applied, task["verification"]["post"]))
        post_after, _ = snapshot_tree(postcheck)
        if post_after != applied:
            errors.append("postcheck verifier mutated its workspace")

    adapter_valid = capture_clean and lifecycle is not None
    safety_eligible = cleanup_proved and not errors
    task_passed = operations == task["effects_policy"]["operations"] and not _assertions(
        after, task["verification"]["post"]
    )
    return {
        "schema": RUN_SCHEMA,
        "subject": subject,
        "task_sha256": canonical_sha256(task),
        "input_archive_sha256": archive_sha256,
        "store_nonce": store_nonce,
        "base_attempt": {
            "ordinal": permit.base_attempt_ordinal,
            "token": permit.base_attempt_token,
            "call_id": permit.call_id,
        },
        "provider": {
            "invoked": True,
            "route": subject,
            "capture": capture,
            "cleanup_receipt": cleanup,
            "lifecycle": lifecycle,
        },
        "workspace": {"before": before, "after": after},
        "effects_archive": {
            "sha256": bytes_sha256(effects_raw),
            "bytes": len(effects_raw),
            "base64": base64.b64encode(effects_raw).decode("ascii"),
        },
        "verdict": {
            "adapter_valid": adapter_valid,
            "safety_eligible": safety_eligible,
            "task_passed": task_passed,
            "errors": errors,
        },
    }


def run_authorized_episode(
    *,
    subject: str,
    task: dict[str, Any],
    workspace_archive: bytes,
    transport_plan: Path,
    request_id: str,
    phase: str,
    coordinator: AuthorizedAttemptCoordinator,
    authorization_resolver: Callable[[PreparedAttempt], Path],
    transport: ProviderTransport,
) -> dict[str, Any]:
    """Run one retained three-workspace episode after explicit authorization."""
    validate_task(task)
    archive_sha256 = bytes_sha256(workspace_archive)
    if archive_sha256 != task["workspace_archive_sha256"]:
        raise ValueError("task workspace archive digest does not match supplied bytes")
    validate_archive(
        workspace_archive,
        WORKSPACE_SCHEMA,
        maximum=task["limits"]["archive_bytes"],
    )
    process_root = coordinator.destination / "process"
    episode_root = process_root / f"episode-{subject}-{request_id}"
    if (
        not request_id
        or request_id != Path(request_id).name
        or episode_root.exists()
        or episode_root.is_symlink()
    ):
        raise ValueError("authorized episode request identity is not a fresh basename")
    episode_root.mkdir(mode=0o700)
    precheck = episode_root / "precheck"
    agent = episode_root / "agent"
    postcheck = episode_root / "postcheck"
    errors: list[str] = []
    initial = extract_workspace_archive(workspace_archive, precheck)
    pre_before, _ = snapshot_tree(precheck)
    errors.extend(_assertions(pre_before, task["verification"]["pre"]))
    pre_after, _ = snapshot_tree(precheck)
    if pre_before != pre_after:
        errors.append("precheck verifier mutated its workspace")
    if errors:
        coordinator.control.latch_stop("authorized_precheck_failed")
        raise ValueError("authorized episode precheck failed")

    before = extract_workspace_archive(workspace_archive, agent)
    prepared = coordinator.prepare(
        phase=phase, subject=subject, request_id=request_id
    )
    try:
        authorization_path = authorization_resolver(prepared)
    except Exception:
        coordinator.control.latch_stop("authorization_resolution_failed")
        raise
    provider = coordinator.execute(
        prepared,
        authorization_path=authorization_path,
        workspace=agent,
        transport_plan=transport_plan,
        transport=transport,
    )
    capture = provider["capture"]
    if provider["result"] != "success":
        errors.append("authorized provider capture or cleanup is not valid")
        lifecycle = None
    else:
        stdout_raw = base64.b64decode(capture["stdout"]["base64"], validate=True)
        try:
            lifecycle = normalize_fake_route(subject, stdout_raw)
        except ValueError as error:
            lifecycle = None
            errors.append(str(error))

    effects_raw, operations, after = build_effects_archive(
        before, agent, maximum=task["limits"]["effects_bytes"]
    )
    if operations != task["effects_policy"]["operations"]:
        errors.append("observed effects do not equal the declared exact policy")
    reconstructed = extract_workspace_archive(workspace_archive, postcheck)
    if reconstructed != initial:
        errors.append("independent postcheck input extraction disagrees")
    applied = apply_effects_archive(effects_raw, postcheck)
    if applied != after:
        errors.append("effects archive does not reconstruct the agent workspace")
    errors.extend(_assertions(applied, task["verification"]["post"]))
    post_after, _ = snapshot_tree(postcheck)
    if post_after != applied:
        errors.append("postcheck verifier mutated its workspace")

    task_passed = operations == task["effects_policy"]["operations"] and not _assertions(
        after, task["verification"]["post"]
    )
    return {
        "schema": RUN_SCHEMA,
        "subject": subject,
        "task_sha256": canonical_sha256(task),
        "input_archive_sha256": archive_sha256,
        "store_nonce": prepared.permit.store_nonce,
        "base_attempt": {
            "ordinal": prepared.permit.base_attempt_ordinal,
            "token": prepared.permit.base_attempt_token,
            "call_id": prepared.permit.call_id,
        },
        "provider": {
            "invoked": provider["provider_invoked"],
            "route": subject,
            "capture": capture,
            "cleanup_receipt": provider["cleanup_receipt"],
            "lifecycle": lifecycle,
        },
        "workspace": {"before": before, "after": after},
        "effects_archive": {
            "sha256": bytes_sha256(effects_raw),
            "bytes": len(effects_raw),
            "base64": base64.b64encode(effects_raw).decode("ascii"),
        },
        "verdict": {
            "adapter_valid": provider["result"] == "success" and lifecycle is not None,
            "safety_eligible": not errors,
            "task_passed": task_passed,
            "errors": errors,
        },
    }


def run_authorized_smoke_episode(
    *,
    subject: str,
    task: dict[str, Any],
    workspace_archive: bytes,
    transport_plan: Path,
    request_id: str,
    coordinator: AuthorizedAttemptCoordinator,
    authorization_resolver: Callable[[PreparedAttempt], Path],
    transport: ProviderTransport,
) -> dict[str, Any]:
    """Run, independently validate, retain, and verify one smoke store."""
    phase = "write-smoke"
    episode = run_authorized_episode(
        subject=subject,
        task=task,
        workspace_archive=workspace_archive,
        transport_plan=transport_plan,
        request_id=request_id,
        phase=phase,
        coordinator=coordinator,
        authorization_resolver=authorization_resolver,
        transport=transport,
    )
    validation = validate_retained_run(
        episode, task=task, workspace_archive=workspace_archive
    )
    if not validation["passed"]:
        coordinator.control.latch_stop(
            "authorized_episode_independent_validation_failed"
        )
        raise ValueError("authorized episode failed independent validation")

    destination = coordinator.destination
    bundle_root = destination / "bundle"
    try:
        episodes_root = _real_directory_child(bundle_root, "episodes")
        specs_root = _real_directory_child(bundle_root, "specs")
        episode_root = _real_directory_child(episodes_root, phase)
        spec_root = _real_directory_child(specs_root, phase)
    except ValueError:
        coordinator.control.latch_stop("authorized_store_topology_invalid")
        raise
    episode_path = episode_root / f"{subject}.json"
    try:
        raw = json.dumps(episode, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        _write_bytes_exclusive(episode_path, raw)
    except FileExistsError as error:
        coordinator.control.latch_stop("authorized_store_already_exists")
        raise ValueError("authorized subject episode already exists") from error
    try:
        store = materialize_single_draw_store(
            subject=subject,
            phase=phase,
            episode_path=episode_path,
            spec_root=spec_root,
            records=destination / "records" / phase,
            expected_emitter_sha256=coordinator.plan["inputs"]["apparatus"][
                "agent_task_emit.py"
            ],
        )
    except Exception:
        coordinator.control.latch_stop("authorized_store_materialization_failed")
        raise
    try:
        validate_live_topology(destination, phase=phase)
    except Exception:
        coordinator.control.latch_stop("authorized_store_topology_invalid")
        raise
    return {
        "schema": "agent-task-authorized-smoke-result/v0.1",
        "episode": episode,
        "independent_validation": validation,
        "episode_path": str(episode_path),
        "store": store,
    }


def run_authorized_fake_smoke_phase(
    *,
    task: dict[str, Any],
    workspace_archive: bytes,
    fake_plan_document: dict[str, Any],
    coordinator: AuthorizedAttemptCoordinator,
    authorization_resolver: Callable[[PreparedAttempt], Path],
    fake_transport: FakeProviderTransport,
    request_prefix: str = "fake-smoke",
) -> dict[str, Any]:
    """Run the exact-five smoke checkpoint with an injectable fake transport."""
    if type(fake_transport) is not FakeProviderTransport:
        raise ValueError("fake smoke phase refuses every non-fake transport")
    if not request_prefix or request_prefix != Path(request_prefix).name:
        raise ValueError("fake smoke request prefix is not a basename")
    destination = coordinator.destination
    phase = "write-smoke"
    validate_live_topology(destination, phase=phase)
    try:
        transport_plan, bundle_manifest_sha256 = _prepare_bound_fake_bundle(
            coordinator=coordinator,
            task=task,
            workspace_archive=workspace_archive,
            fake_plan_document=fake_plan_document,
        )
    except Exception:
        coordinator.control.latch_stop("authorized_smoke_bundle_invalid")
        raise
    records = destination / "records" / phase
    if any(records.iterdir()):
        raise ValueError("fake smoke phase requires an empty phase root")

    results: list[dict[str, Any]] = []
    planned_subjects = coordinator.plan["store_nonces"].get(phase)
    if type(planned_subjects) is not dict or set(planned_subjects) != set(SUBJECTS):
        coordinator.control.latch_stop("authorized_smoke_subject_set_invalid")
        raise ValueError("authorized smoke planned subject set is not exact-five")
    for subject in SUBJECTS:
        result = run_authorized_smoke_episode(
            subject=subject,
            task=task,
            workspace_archive=workspace_archive,
            transport_plan=transport_plan,
            request_id=f"{request_prefix}-{subject}",
            coordinator=coordinator,
            authorization_resolver=authorization_resolver,
            transport=fake_transport,
        )
        results.append(result)

    runs = [result["episode"] for result in results]
    comparison = compare_exact_five(
        runs, task=task, workspace_archive=workspace_archive
    )
    if not comparison["passed"]:
        coordinator.control.latch_stop("authorized_smoke_comparison_failed")
        raise ValueError("authorized smoke exact-five comparison failed")
    store_rows = {result["store"]["subject"]: result["store"] for result in results}
    expected_run_ids = {row["run_id"] for row in store_rows.values()}
    observed_run_ids = {child.name for child in records.iterdir()}
    if observed_run_ids != expected_run_ids or len(observed_run_ids) != 5:
        coordinator.control.latch_stop("authorized_smoke_store_set_invalid")
        raise ValueError("authorized smoke store set is not exact-five")
    for row in store_rows.values():
        observed = canon.digest_tree(str(records / row["run_id"]))
        if observed != row["run_store_tree_sha256"]:
            coordinator.control.latch_stop("authorized_smoke_store_digest_drift")
            raise ValueError("authorized smoke store drifted before comparison")

    review_root = destination / "review" / phase
    comparison_path = review_root / "comparison.json"
    _write_json_exclusive(comparison_path, comparison)
    usage_paths = sorted((destination / "session" / "permit-usage").glob("permit-*.json"))
    if len(usage_paths) != 5:
        coordinator.control.latch_stop("authorized_smoke_usage_set_invalid")
        raise ValueError("authorized smoke permit usage set is not exact-five")
    usage_evidence = {
        "schema": "agent-task-smoke-permit-usage/v0.1",
        "snapshots": [
            {
                "path": path.name,
                "sha256": bytes_sha256(path.read_bytes()),
                "document_sha256": canonical_sha256(
                    json.loads(path.read_text(encoding="utf-8"))
                ),
            }
            for path in usage_paths
        ],
    }
    usage_path = review_root / "permit-usage.json"
    _write_json_exclusive(usage_path, usage_evidence)
    credential_scan = scan_credentials(destination, credential_values(os.environ))
    credential_path = review_root / "credential-scan.json"
    _write_json_exclusive(credential_path, credential_scan)
    if not credential_scan["passed"]:
        coordinator.control.latch_stop("authorized_smoke_credential_scan_failed")
        raise ValueError("authorized smoke retained a configured credential")
    if len(coordinator.broker.receipts) != 5:
        coordinator.control.latch_stop("authorized_smoke_cleanup_set_invalid")
        raise ValueError("authorized smoke cleanup receipt set is not exact-five")
    checkpoint = build_phase_checkpoint(
        journal=coordinator.control.journal,
        registry=coordinator.broker.registry,
        store_digests={
            subject: row["run_store_tree_sha256"]
            for subject, row in store_rows.items()
        },
        comparison_sha256=canonical_sha256(comparison),
        usage_sha256=canonical_sha256(usage_evidence),
        cleanup_receipts=coordinator.broker.receipts,
    )
    if (
        not checkpoint["eligible"]
        or not validate_prefix(
            coordinator.control.journal, checkpoint["journal_prefix"]
        )
        or not validate_prefix(
            coordinator.broker.registry, checkpoint["registry_prefix"]
        )
    ):
        coordinator.control.latch_stop("authorized_smoke_checkpoint_invalid")
        raise ValueError("authorized smoke phase checkpoint is invalid")
    checkpoint_path = review_root / "phase-checkpoint.json"
    _write_json_exclusive(checkpoint_path, checkpoint)
    validate_live_topology(destination, phase=phase)
    offline_review = review_fake_smoke_checkpoint(
        destination, configured_credentials=credential_values(os.environ)
    )
    if not offline_review["passed"]:
        coordinator.control.latch_stop("authorized_smoke_offline_review_failed")
        raise ValueError("authorized smoke offline review failed")
    offline_review_path = review_root / "offline-review.json"
    _write_json_exclusive(offline_review_path, offline_review)
    return {
        "schema": "agent-task-authorized-fake-smoke-phase/v0.1",
        "passed": True,
        "provider_calls": len(runs),
        "subjects": sorted(store_rows),
        "stores": dict(sorted(store_rows.items())),
        "comparison_sha256": canonical_sha256(comparison),
        "usage_sha256": canonical_sha256(usage_evidence),
        "credential_scan_sha256": bytes_sha256(credential_path.read_bytes()),
        "checkpoint_sha256": canonical_sha256(checkpoint),
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "offline_review_sha256": bytes_sha256(offline_review_path.read_bytes()),
    }
