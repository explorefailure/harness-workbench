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

from harness_workbench.capture import credential_values, minimal_environment

from agent_task_archives import (
    apply_effects_archive,
    build_effects_archive,
    extract_workspace_archive,
    snapshot_tree,
    validate_archive,
)
from agent_task_broker import SpawnBroker
from agent_task_coordinator import AuthorizedAttemptCoordinator, PreparedAttempt
from agent_task_control import CallControl
from agent_task_services import BrokerClient, CallControlClient
from agent_task_routes import normalize_fake_route
from agent_task_providers import FakeProviderTransport, ProviderTransport
from agent_task_schema import (
    RUN_SCHEMA,
    WORKSPACE_SCHEMA,
    bytes_sha256,
    canonical_sha256,
    validate_task,
)


HERE = Path(__file__).resolve().parent
FAKE_PROVIDER = HERE / "agent_task_fake_provider.py"


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
            "authorization_receipt": provider["authorization_receipt"],
            "capture": capture,
            "cleanup_receipt": provider["cleanup_receipt"],
            "lifecycle": lifecycle,
        },
        "workspace": {
            "root": str(episode_root), "before": before, "after": after,
        },
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
