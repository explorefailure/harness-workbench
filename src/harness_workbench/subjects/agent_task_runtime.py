#!/usr/bin/env python3
"""Three-workspace declarative episode runtime for the finite offline path."""
from __future__ import annotations

import base64
import json
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from typing import Any, Callable

from harness_workbench import canon
from harness_workbench.capture import credential_values, minimal_environment, run_bounded

from agent_task_archives import (
    apply_effects_archive,
    build_effects_archive,
    extract_workspace_archive,
    snapshot_tree,
    validate_archive,
)
from agent_task_authorization import AuthorizationExpectation, validate_live_topology
from agent_task_broker import (
    SpawnBroker,
    build_phase_checkpoint,
    durable_prefix,
    validate_prefix,
)
from agent_task_coordinator import AuthorizedAttemptCoordinator, PreparedAttempt
from agent_task_control import CallControl, Permit
from agent_task_services import BrokerClient, CallControlClient
from agent_task_routes import normalize_fake_route
from agent_task_phase_review import (
    review_fake_campaign,
    review_fake_repair_matrix,
    review_fake_smoke_checkpoint,
)
from agent_task_providers import FakeProviderTransport, ProviderTransport
from agent_task_schema import (
    CAMPAIGN_SCHEMA,
    PHASE_CANDIDATE_SCHEMA,
    RUN_SCHEMA,
    SUBJECTS,
    WORKSPACE_SCHEMA,
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    validate_run,
    validate_task,
)
from agent_task_specs import assemble_pre_call_specs
from agent_task_store import materialize_single_draw_store
from agent_task_validate import (
    compare_exact_five,
    compare_exact_five_matrix,
    scan_credentials,
    validate_retained_run,
)


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
    execution_plan_raw = canonical_bytes(coordinator.plan_result) + b"\n"
    planned = coordinator.plan["inputs"]
    expected = {
        "task.json": planned["task_file_sha256"],
        "workspace.zip": planned["workspace_archive_sha256"],
        "fake-provider-plan.json": planned["fake_transport_plan_sha256"],
        "execution-plan.json": bytes_sha256(execution_plan_raw),
    }
    supplied = {
        "task.json": bytes_sha256(task_raw),
        "workspace.zip": bytes_sha256(workspace_archive),
        "fake-provider-plan.json": bytes_sha256(fake_raw),
        "execution-plan.json": bytes_sha256(execution_plan_raw),
    }
    if supplied != expected:
        raise ValueError("fake smoke bundle bytes do not match the execution plan")
    untrusted = (task_raw, workspace_archive, fake_raw, execution_plan_raw)
    for value in credential_values(os.environ):
        raw = value.encode("utf-8", errors="surrogatepass")
        if raw and any(raw in document for document in untrusted):
            raise ValueError("fake smoke bundle contains a configured credential")
    for name, raw in (
        ("task.json", task_raw),
        ("workspace.zip", workspace_archive),
        ("fake-provider-plan.json", fake_raw),
        ("execution-plan.json", execution_plan_raw),
    ):
        _write_bytes_exclusive(bundle / name, raw)
    spec_assembly = assemble_pre_call_specs(bundle, coordinator.plan)
    bundle_manifest = {
        "schema": "agent-task-live-bundle-manifest/v0.1",
        "execution_plan_sha256": coordinator.plan_result["execution_plan_sha256"],
        "files": expected,
        "apparatus_map_sha256": planned["apparatus_map_sha256"],
        "validator_program_sha256": planned["validator_program_sha256"],
        "comparator_program_sha256": planned["comparator_program_sha256"],
        "precall_specs": {
            phase: {
                subject: row["sha256"]
                for subject, row in sorted(subjects.items())
            }
            for phase, subjects in sorted(planned["specs"].items())
        },
        "precall_spec_tree_sha256": spec_assembly["tree_sha256"],
        "precall_spec_assembly_sha256": canonical_sha256(spec_assembly),
    }
    manifest_path = bundle / "bundle-manifest.json"
    _write_json_exclusive(manifest_path, bundle_manifest)
    return bundle / "fake-provider-plan.json", bytes_sha256(manifest_path.read_bytes())


class _AuthorizationBridge:
    """Resolve a fixed draw sequence without exposing the authorization callback."""

    def __init__(
        self,
        *,
        coordinator: AuthorizedAttemptCoordinator,
        subject: str,
        phase: str,
        request_id: str,
        expected_draws: int,
        resolver: Callable[[PreparedAttempt], Path],
    ) -> None:
        if (
            type(coordinator.control) is not CallControlClient
            or type(coordinator.broker) is not BrokerClient
            or coordinator.control.authkey != coordinator.broker.authkey
        ):
            raise ValueError("preassembled step requires the authenticated services")
        if expected_draws not in {1, 3}:
            raise ValueError("authorization bridge draw count is unsupported")
        self.coordinator = coordinator
        self.subject = subject
        self.phase = phase
        self.request_id = request_id
        self.expected_draws = expected_draws
        self.resolver = resolver
        self.authkey = coordinator.control.authkey
        self.error: Exception | None = None
        self.completed: list[str] = []
        self._active_request_id: str | None = None
        self._active_permit: Permit | None = None
        self._authorization_started = False
        self._root = Path(tempfile.mkdtemp(prefix="hwb-agent-task-authorize-"))
        os.chmod(self._root, 0o700)
        self.socket = self._root / "authorize.sock"
        self._listener = Listener(
            str(self.socket), family="AF_UNIX", authkey=self.authkey
        )
        os.chmod(self.socket, 0o600)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            stop = False
            while not stop and len(self.completed) < self.expected_draws:
                connection = self._listener.accept()
                try:
                    request = connection.recv()
                    response, stop = self._dispatch(request)
                except Exception as error:
                    self.error = error
                    response = {"ok": False}
                    stop = True
                try:
                    connection.send(response)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                finally:
                    connection.close()
        except Exception as error:
            self.error = error
        finally:
            self._listener.close()

    def _dispatch(self, request: Any) -> tuple[dict[str, Any], bool]:
        if request == {"op": "cancel"}:
            return {"ok": False}, True
        if type(request) is not dict or type(request.get("op")) is not str:
            raise ValueError("authorization bridge request is malformed")
        operation = request["op"]
        if operation == "claim":
            if set(request) != {"op"} or self._active_request_id is not None:
                raise ValueError("authorization bridge draw claim is not fresh")
            request_id = f"{self.request_id}-draw-{len(self.completed)}"
            if request_id != Path(request_id).name:
                raise ValueError("authorization bridge request identity is invalid")
            self._active_request_id = request_id
            return {"ok": True, "request_id": request_id}, False
        if operation == "authorize":
            if set(request) != {"op", "permit", "authorization"}:
                raise ValueError("authorization bridge request is malformed")
            if self._active_request_id is None or self._authorization_started:
                raise ValueError("authorization bridge draw has no fresh claim")
            permit = Permit(**request["permit"])
            authorization = AuthorizationExpectation(**request["authorization"])
            expected = AuthorizationExpectation.from_permit(
                permit,
                execution_plan_sha256=self.coordinator.plan_result[
                    "execution_plan_sha256"
                ],
                provider_route_sha256=self.coordinator.plan["provider_pins"]
                ["route_sha256"][self.subject],
                model=self.coordinator.plan["provider_pins"]["routes"]
                [self.subject]["model"],
            )
            if (
                permit.phase != self.phase
                or permit.subject != self.subject
                or permit.request_id != self._active_request_id
                or permit.store_nonce
                != self.coordinator.plan["store_nonces"][self.phase][self.subject]
                or authorization != expected
            ):
                raise ValueError("authorization bridge identity disagrees")
            self._authorization_started = True
            path = self.resolver(
                PreparedAttempt(permit=permit, authorization=authorization)
            )
            if type(path) is not Path:
                path = Path(path)
            self._active_permit = permit
            return {"ok": True, "authorization_path": str(path)}, False
        if operation == "complete":
            if set(request) != {
                "op", "request_id", "call_id", "base_attempt_ordinal",
            }:
                raise ValueError("authorization bridge completion is malformed")
            permit = self._active_permit
            if (
                permit is None
                or request["request_id"] != self._active_request_id
                or request["call_id"] != permit.call_id
                or request["base_attempt_ordinal"] != permit.base_attempt_ordinal
            ):
                raise ValueError("authorization bridge completion identity disagrees")
            self.completed.append(permit.request_id)
            self._active_request_id = None
            self._active_permit = None
            self._authorization_started = False
            return {"ok": True}, len(self.completed) == self.expected_draws
        raise ValueError("authorization bridge operation is unsupported")

    def close(self) -> None:
        if self._thread.is_alive():
            try:
                connection = Client(
                    str(self.socket), family="AF_UNIX", authkey=self.authkey
                )
                try:
                    connection.send({"op": "cancel"})
                    connection.recv()
                finally:
                    connection.close()
            except (EOFError, OSError, ConnectionError):
                pass
        self._thread.join(timeout=2)
        if self._thread.is_alive() and self.error is None:
            self.error = RuntimeError("authorization bridge did not stop")
        shutil.rmtree(self._root, ignore_errors=True)


def _workbench_environment(
    coordinator: AuthorizedAttemptCoordinator,
    bridge: _AuthorizationBridge,
    *,
    request_id: str,
) -> dict[str, str]:
    environment = dict(os.environ)
    if environment.get("PYTHONPATH"):
        environment["PYTHONPATH"] = os.pathsep.join(
            str((Path.cwd() / item).resolve())
            if not Path(item).is_absolute() else item
            for item in environment["PYTHONPATH"].split(os.pathsep)
        )
    environment.update({
        "HWB_AGENT_TASK_AUTHKEY_B64": base64.b64encode(bridge.authkey).decode("ascii"),
        "HWB_AGENT_TASK_AUTHORIZATION_SOCKET": str(bridge.socket),
        "HWB_AGENT_TASK_BROKER_SOCKET": str(coordinator.broker.socket_path),
        "HWB_AGENT_TASK_CALL_SOCKET": str(coordinator.control.socket_path),
        "HWB_AGENT_TASK_DESTINATION": str(coordinator.destination),
        "HWB_AGENT_TASK_REQUEST_ID": request_id,
        "HWB_AGENT_TASK_TRANSPORT": "fake",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return environment


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


def run_preassembled_fake_subject(
    *,
    subject: str,
    phase: str,
    expected_draws: int,
    task: dict[str, Any],
    workspace_archive: bytes,
    request_id: str,
    coordinator: AuthorizedAttemptCoordinator,
    authorization_resolver: Callable[[PreparedAttempt], Path],
) -> dict[str, Any]:
    """Execute one subject store through its preassembled Workbench step."""
    if (phase, expected_draws) not in {
        ("write-smoke", 1), ("repair-matrix", 3),
    }:
        raise ValueError("preassembled Workbench phase/draw count is unsupported")
    destination = coordinator.destination
    records = destination / "records" / phase
    spec_root = destination / "bundle" / "precall-specs" / phase / subject
    spec_path = spec_root / f"{subject}.json"
    if (
        records.is_symlink()
        or not records.is_dir()
        or spec_root.is_symlink()
        or not spec_root.is_dir()
        or spec_path.is_symlink()
        or not spec_path.is_file()
    ):
        coordinator.control.latch_stop("preassembled_workbench_topology_invalid")
        raise ValueError("preassembled Workbench store topology is invalid")
    before = {child.name for child in records.iterdir()}
    if any(child.is_symlink() or not child.is_dir() for child in records.iterdir()):
        coordinator.control.latch_stop("preassembled_workbench_store_partial")
        raise ValueError("preassembled Workbench phase contains a partial store")

    bridge = _AuthorizationBridge(
        coordinator=coordinator,
        subject=subject,
        phase=phase,
        request_id=request_id,
        expected_draws=expected_draws,
        resolver=authorization_resolver,
    )
    environment = _workbench_environment(
        coordinator, bridge, request_id=request_id
    )
    try:
        completed = run_bounded(
            [
                sys.executable, "-m", "harness_workbench",
                "--root", str(records), "run", str(spec_path),
            ],
            cwd=spec_root,
            env=environment,
            timeout=(
                coordinator.plan["timeouts"]["subject_episode_seconds"][subject]
                * expected_draws * 2 + 60
            ),
            stdout_limit=4 * 1024 * 1024,
            stderr_limit=4 * 1024 * 1024,
            termination_grace=1.0,
            forward_signals=False,
        )
    finally:
        bridge.close()
    if bridge.error is not None:
        coordinator.control.latch_stop("preassembled_authorization_bridge_failed")
        raise ValueError("preassembled authorization bridge failed") from bridge.error
    if (
        completed.returncode != 0
        or completed.termination_reason is not None
        or completed.stdout_overflow
        or completed.stderr_overflow
        or completed.group_alive_after_cleanup
    ):
        coordinator.control.latch_stop("preassembled_workbench_run_failed")
        raise ValueError("preassembled Workbench run was not bounded and clean")
    lines = completed.stdout.decode("utf-8", errors="strict").splitlines()
    fields = lines[0].split() if lines else []
    if len(fields) < 5 or fields[-1] != "completed":
        coordinator.control.latch_stop("preassembled_workbench_identity_invalid")
        raise ValueError("preassembled Workbench run did not report completion")
    run_id = fields[0]
    after = {child.name for child in records.iterdir()}
    if run_id != Path(run_id).name or after - before != {run_id} or before - after:
        coordinator.control.latch_stop("preassembled_workbench_store_set_invalid")
        raise ValueError("preassembled Workbench did not create exactly one store")
    run_dir = records / run_id
    verify_environment = dict(os.environ)
    if verify_environment.get("PYTHONPATH"):
        verify_environment["PYTHONPATH"] = os.pathsep.join(
            str((Path.cwd() / item).resolve())
            if not Path(item).is_absolute() else item
            for item in verify_environment["PYTHONPATH"].split(os.pathsep)
        )
    verify = run_bounded(
        [
            sys.executable, "-m", "harness_workbench", "--root",
            str(records), "verify", run_id,
        ],
        cwd=spec_root,
        env=verify_environment,
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
        coordinator.control.latch_stop("preassembled_workbench_verify_failed")
        raise ValueError("preassembled Workbench store failed hwb verify")
    record_path = run_dir / "record.json"
    integrity_path = run_dir / "integrity.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    attempts = [
        json.loads(line)
        for line in (run_dir / "attempts.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    if len(attempts) != expected_draws or any(
        attempt.get("n") != draw
        or attempt.get("caused_by") != [
            {"feature": "sample", "i": draw},
            {"feature": "retry", "i": 0},
        ]
        or attempt.get("exit") != 0
        for draw, attempt in enumerate(attempts)
    ):
        coordinator.control.latch_stop("preassembled_workbench_attempt_invalid")
        raise ValueError("preassembled Workbench attempt is not exact")
    stdout_paths = [
        run_dir / "steps" / f"{subject}-agent-task"
        / "attempts" / str(draw) / "stdout.bin"
        for draw in range(expected_draws)
    ]
    try:
        episodes = [
            json.loads(path.read_text(encoding="utf-8")) for path in stdout_paths
        ]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        coordinator.control.latch_stop("preassembled_workbench_episode_invalid")
        raise ValueError("preassembled Workbench episode output is invalid") from error
    validations = [
        validate_retained_run(
            episode, task=task, workspace_archive=workspace_archive
        )
        for episode in episodes
    ]
    if any(not validation["passed"] for validation in validations):
        coordinator.control.latch_stop(
            "authorized_episode_independent_validation_failed"
        )
        raise ValueError("preassembled episode failed independent validation")
    freeze = record.get("extras", {}).get("freeze", {})
    receipt = record.get("extras", {}).get("receipt", {}).get("bound", {})
    if (
        any(episode["subject"] != subject for episode in episodes)
        or any(
            episode["store_nonce"]
            != coordinator.plan["store_nonces"][phase][subject]
            for episode in episodes
        )
        or [episode["base_attempt"]["ordinal"] for episode in episodes]
        != list(range(expected_draws))
        or record.get("run_id") != run_id
        or freeze.get("baseline") != "compared"
        or freeze.get("drifted") is not False
        or receipt.get("inputs_from") != "freeze"
        or receipt.get("inputs") != freeze.get("digests")
    ):
        coordinator.control.latch_stop("preassembled_workbench_binding_invalid")
        raise ValueError("preassembled Workbench store binding is invalid")
    try:
        validate_live_topology(destination, phase=phase)
    except Exception:
        coordinator.control.latch_stop("authorized_store_topology_invalid")
        raise
    store = {
        "schema": "agent-task-preassembled-store/v0.1",
        "phase": phase,
        "subject": subject,
        "draws": expected_draws,
        "run_id": run_id,
        "run_store_tree_sha256": canon.digest_tree(str(run_dir)),
        "record_json_sha256": canon.digest_file(str(record_path)),
        "integrity_json_sha256": canon.digest_file(str(integrity_path)),
    }
    return {
        "schema": "agent-task-authorized-preassembled-result/v0.1",
        "episodes": episodes,
        "independent_validations": validations,
        "episode_paths": [str(path) for path in stdout_paths],
        "store": store,
    }


def run_preassembled_fake_smoke_episode(
    *,
    subject: str,
    task: dict[str, Any],
    workspace_archive: bytes,
    request_id: str,
    coordinator: AuthorizedAttemptCoordinator,
    authorization_resolver: Callable[[PreparedAttempt], Path],
) -> dict[str, Any]:
    """Compatibility wrapper for the exact one-draw smoke store."""
    result = run_preassembled_fake_subject(
        subject=subject, phase="write-smoke", expected_draws=1,
        task=task, workspace_archive=workspace_archive, request_id=request_id,
        coordinator=coordinator, authorization_resolver=authorization_resolver,
    )
    return {
        "schema": "agent-task-authorized-smoke-result/v0.1",
        "episode": result["episodes"][0],
        "independent_validation": result["independent_validations"][0],
        "episode_path": result["episode_paths"][0],
        "store": result["store"],
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
        _, bundle_manifest_sha256 = _prepare_bound_fake_bundle(
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
        result = run_preassembled_fake_smoke_episode(
            subject=subject,
            task=task,
            workspace_archive=workspace_archive,
            request_id=f"{request_prefix}-{subject}",
            coordinator=coordinator,
            authorization_resolver=authorization_resolver,
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
    cleanup_receipts = coordinator.broker.receipt_snapshot()
    if len(cleanup_receipts) != 5:
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
        cleanup_receipts=cleanup_receipts,
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


def _validate_repair_matrix_boundary(
    coordinator: AuthorizedAttemptCoordinator,
) -> dict[str, Any]:
    """Revalidate smoke, fresh usage, and separate matrix authorization."""
    destination = coordinator.destination
    checkpoint_path = destination / "review" / "write-smoke" / "phase-checkpoint.json"
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("repair matrix has no readable smoke checkpoint") from error
    offline = review_fake_smoke_checkpoint(
        destination, configured_credentials=credential_values(os.environ)
    )
    if (
        not offline["passed"]
        or not checkpoint.get("eligible")
        or not validate_prefix(coordinator.control.journal, checkpoint["journal_prefix"])
        or not validate_prefix(coordinator.broker.registry, checkpoint["registry_prefix"])
    ):
        raise ValueError("repair matrix smoke checkpoint is no longer valid")
    journal = coordinator.control.journal
    raw = journal.read_bytes()
    suffix = raw[checkpoint["journal_prefix"]["bytes"]:]
    try:
        rows = [json.loads(line) for line in suffix.splitlines() if line]
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("repair matrix authorization journal is malformed") from error
    boundary_rows = [row for row in rows if row.get("event") == "usage_boundary"]
    authorization_rows = [row for row in rows if row.get("event") == "phase_authorized"]
    matrix_permits = [
        row for row in rows
        if row.get("event") == "permit_allocated"
        and row.get("phase") == "repair-matrix"
    ]
    if (
        len(boundary_rows) != 1
        or boundary_rows[0].get("label") != "after-smoke-before-matrix"
        or boundary_rows[0].get("passed") is not True
        or len(authorization_rows) != 1
        or authorization_rows[0].get("phase") != "repair-matrix"
        or authorization_rows[0].get("maximum_calls") != 30
        or rows.index(boundary_rows[0]) >= rows.index(authorization_rows[0])
        or matrix_permits
    ):
        raise ValueError("repair matrix lacks a fresh, separate 30-call authorization")
    usage_paths = sorted(
        (destination / "session" / "permit-usage").glob(
            "after-smoke-before-matrix-*.json"
        )
    )
    if len(usage_paths) != 1:
        raise ValueError("repair matrix boundary usage evidence is not exact")
    usage = json.loads(usage_paths[0].read_text(encoding="utf-8"))
    if canonical_sha256(usage) != boundary_rows[0].get("usage_sha256"):
        raise ValueError("repair matrix boundary usage digest disagrees")
    status = coordinator.control.status()
    if status.get("state") != "ready":
        raise ValueError("repair matrix control plane is not ready")
    return {
        "checkpoint_sha256": canonical_sha256(checkpoint),
        "usage_sha256": canonical_sha256(usage),
        "authorized_maximum_calls": 30,
        "allocated_calls_before": status.get("allocated_calls"),
    }


def run_authorized_fake_repair_matrix_phase(
    *,
    task: dict[str, Any],
    workspace_archive: bytes,
    coordinator: AuthorizedAttemptCoordinator,
    authorization_resolver: Callable[[PreparedAttempt], Path],
    fake_transport: FakeProviderTransport,
    request_prefix: str = "fake-matrix",
) -> dict[str, Any]:
    """Run five preassembled stores with exactly three fake draws each."""
    if type(fake_transport) is not FakeProviderTransport:
        raise ValueError("fake repair matrix refuses every non-fake transport")
    if not request_prefix or request_prefix != Path(request_prefix).name:
        raise ValueError("fake matrix request prefix is not a basename")
    destination = coordinator.destination
    phase = "repair-matrix"
    validate_live_topology(destination, phase=phase)
    try:
        boundary = _validate_repair_matrix_boundary(coordinator)
    except Exception:
        coordinator.control.latch_stop("authorized_matrix_boundary_invalid")
        raise
    bundle_manifest_path = destination / "bundle" / "bundle-manifest.json"
    if bundle_manifest_path.is_symlink() or not bundle_manifest_path.is_file():
        coordinator.control.latch_stop("authorized_matrix_bundle_invalid")
        raise ValueError("fake repair matrix bundle manifest is unavailable")
    records = destination / "records" / phase
    review_root = destination / "review" / phase
    if any(records.iterdir()) or any(review_root.iterdir()):
        coordinator.control.latch_stop("authorized_matrix_root_not_empty")
        raise ValueError("fake repair matrix requires empty records and review roots")
    planned_subjects = coordinator.plan["store_nonces"].get(phase)
    if type(planned_subjects) is not dict or set(planned_subjects) != set(SUBJECTS):
        coordinator.control.latch_stop("authorized_matrix_subject_set_invalid")
        raise ValueError("authorized matrix planned subject set is not exact-five")

    usage_root = destination / "session" / "permit-usage"
    usage_before = {path.name for path in usage_root.glob("permit-*.json")}
    cleanup_before = coordinator.broker.receipt_snapshot()
    results = [
        run_preassembled_fake_subject(
            subject=subject, phase=phase, expected_draws=3,
            task=task, workspace_archive=workspace_archive,
            request_id=f"{request_prefix}-{subject}", coordinator=coordinator,
            authorization_resolver=authorization_resolver,
        )
        for subject in SUBJECTS
    ]
    runs = [episode for result in results for episode in result["episodes"]]
    comparison = compare_exact_five_matrix(
        runs, task=task, workspace_archive=workspace_archive
    )
    if not comparison["passed"]:
        coordinator.control.latch_stop("authorized_matrix_comparison_failed")
        raise ValueError("authorized matrix exact-five comparison failed")
    store_rows = {result["store"]["subject"]: result["store"] for result in results}
    expected_run_ids = {row["run_id"] for row in store_rows.values()}
    observed_run_ids = {child.name for child in records.iterdir()}
    if observed_run_ids != expected_run_ids or len(observed_run_ids) != 5:
        coordinator.control.latch_stop("authorized_matrix_store_set_invalid")
        raise ValueError("authorized matrix store set is not exact-five")
    for row in store_rows.values():
        if canon.digest_tree(str(records / row["run_id"])) != row[
            "run_store_tree_sha256"
        ]:
            coordinator.control.latch_stop("authorized_matrix_store_digest_drift")
            raise ValueError("authorized matrix store drifted before comparison")

    usage_after = {path.name for path in usage_root.glob("permit-*.json")}
    matrix_usage_names = sorted(usage_after - usage_before)
    if len(matrix_usage_names) != 15 or usage_before - usage_after:
        coordinator.control.latch_stop("authorized_matrix_usage_set_invalid")
        raise ValueError("authorized matrix permit usage set is not exact-fifteen")
    usage_evidence = {
        "schema": "agent-task-matrix-permit-usage/v0.1",
        "snapshots": [
            {
                "path": name,
                "sha256": bytes_sha256((usage_root / name).read_bytes()),
                "document_sha256": canonical_sha256(json.loads(
                    (usage_root / name).read_text(encoding="utf-8")
                )),
            }
            for name in matrix_usage_names
        ],
    }
    cleanup_after = coordinator.broker.receipt_snapshot()
    matrix_cleanup = cleanup_after[len(cleanup_before):]
    if (
        len(matrix_cleanup) != 15
        or cleanup_after[:len(cleanup_before)] != cleanup_before
    ):
        coordinator.control.latch_stop("authorized_matrix_cleanup_set_invalid")
        raise ValueError("authorized matrix cleanup receipt set is not exact-fifteen")
    credential_scan = scan_credentials(destination, credential_values(os.environ))
    if not credential_scan["passed"]:
        coordinator.control.latch_stop("authorized_matrix_credential_scan_failed")
        raise ValueError("authorized matrix retained a configured credential")
    comparison_path = review_root / "comparison.json"
    usage_path = review_root / "permit-usage.json"
    credential_path = review_root / "credential-scan.json"
    cleanup_path = review_root / "cleanup-receipts.json"
    _write_json_exclusive(comparison_path, comparison)
    _write_json_exclusive(usage_path, usage_evidence)
    _write_json_exclusive(credential_path, credential_scan)
    _write_json_exclusive(cleanup_path, {
        "schema": "agent-task-matrix-cleanup/v0.1",
        "receipts": matrix_cleanup,
    })
    validate_live_topology(destination, phase=phase)
    offline_review = review_fake_repair_matrix(
        destination, configured_credentials=credential_values(os.environ)
    )
    if not offline_review["passed"]:
        coordinator.control.latch_stop("authorized_matrix_offline_review_failed")
        raise ValueError("authorized matrix offline review failed")
    offline_review_path = review_root / "offline-review.json"
    _write_json_exclusive(offline_review_path, offline_review)
    return {
        "schema": "agent-task-authorized-fake-repair-matrix/v0.1",
        "passed": True,
        "provider_calls": len(runs),
        "subjects": sorted(store_rows),
        "stores": dict(sorted(store_rows.items())),
        "comparison_sha256": canonical_sha256(comparison),
        "usage_sha256": canonical_sha256(usage_evidence),
        "credential_scan_sha256": bytes_sha256(credential_path.read_bytes()),
        "cleanup_sha256": bytes_sha256(cleanup_path.read_bytes()),
        "offline_review_sha256": bytes_sha256(offline_review_path.read_bytes()),
        "bundle_manifest_sha256": bytes_sha256(bundle_manifest_path.read_bytes()),
        "boundary": boundary,
    }


def _final_store_evidence(destination: Path, phase: str) -> dict[str, Any]:
    records = destination / "records" / phase
    expected_draws = 1 if phase == "write-smoke" else 3
    stores = list(records.iterdir())
    if len(stores) != len(SUBJECTS):
        raise ValueError(f"{phase} finalization requires exact-five stores")
    result: dict[str, Any] = {}
    for store in stores:
        if store.is_symlink() or not store.is_dir():
            raise ValueError(f"{phase} finalization found a partial store")
        outputs = sorted(store.glob("steps/*/attempts/*/stdout.bin"))
        if len(outputs) != expected_draws:
            raise ValueError(f"{phase} finalization found an incomplete store")
        subjects = {
            validate_run(json.loads(path.read_text(encoding="utf-8")))["subject"]
            for path in outputs
        }
        if len(subjects) != 1:
            raise ValueError(f"{phase} finalization found a mixed-subject store")
        subject = next(iter(subjects))
        if subject not in SUBJECTS or subject in result:
            raise ValueError(f"{phase} finalization subject set is not exact-five")
        record = store / "record.json"
        integrity = store / "integrity.json"
        if (
            record.is_symlink() or not record.is_file()
            or integrity.is_symlink() or not integrity.is_file()
        ):
            raise ValueError(f"{phase} finalization store metadata is incomplete")
        result[subject] = {
            "run_id": store.name,
            "run_store_tree_sha256": canon.digest_tree(str(store)),
            "record_json_sha256": canon.digest_file(str(record)),
            "integrity_json_sha256": canon.digest_file(str(integrity)),
        }
    if set(result) != set(SUBJECTS):
        raise ValueError(f"{phase} finalization subject set is not exact-five")
    return dict(sorted(result.items()))


def _final_phase_candidate(
    destination: Path,
    *,
    phase: str,
    execution_plan: dict[str, Any],
    bundle_manifest: dict[str, Any],
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
        "stores": _final_store_evidence(destination, phase),
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


def finalize_authorized_fake_campaign(
    destination: Path,
    *,
    control_plane_shutdown: dict[str, Any],
) -> dict[str, Any]:
    """Emit final candidates only after independently accepted clean closure."""
    if destination.is_symlink():
        raise ValueError("fake campaign destination must not be an alias")
    destination = destination.resolve(strict=True)
    candidate_paths = {
        phase: destination / "review" / phase / "phase-candidate.json"
        for phase in ("write-smoke", "repair-matrix")
    }
    campaign_path = destination / "review" / "campaign.json"
    if campaign_path.exists() or campaign_path.is_symlink() or any(
        path.exists() or path.is_symlink() for path in candidate_paths.values()
    ):
        raise FileExistsError("fake campaign finalization is immutable and single-use")
    execution_outer = json.loads(
        (destination / "bundle" / "execution-plan.json").read_text(
            encoding="utf-8"
        )
    )
    execution_plan = execution_outer["execution_plan"]
    if canonical_sha256(execution_plan) != execution_outer.get(
        "execution_plan_sha256"
    ):
        raise ValueError("final execution plan digest disagrees")
    bundle_manifest = json.loads(
        (destination / "bundle" / "bundle-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    smoke_checkpoint = json.loads(
        (destination / "review" / "write-smoke" / "phase-checkpoint.json")
        .read_text(encoding="utf-8")
    )
    journal_closure = durable_prefix(
        destination / "session" / "call-control.jsonl"
    )
    registry_closure = durable_prefix(
        destination / "session" / "process-registry.jsonl"
    )
    candidates = {
        phase: _final_phase_candidate(
            destination, phase=phase, execution_plan=execution_plan,
            bundle_manifest=bundle_manifest,
            journal_closure=journal_closure,
            registry_closure=registry_closure,
            shutdown=control_plane_shutdown,
            smoke_checkpoint=smoke_checkpoint,
        )
        for phase in ("write-smoke", "repair-matrix")
    }
    campaign = {
        "schema": CAMPAIGN_SCHEMA,
        "eligible": True,
        "execution_plan_sha256": canonical_sha256(execution_plan),
        "phase_candidates": {
            phase: {
                "path": f"{phase}/phase-candidate.json",
                "sha256": canonical_sha256(candidates[phase]),
            }
            for phase in ("write-smoke", "repair-matrix")
        },
        "smoke_checkpoint_sha256": canonical_sha256(smoke_checkpoint),
        "journal_closure": journal_closure,
        "registry_closure": registry_closure,
        "control_plane_shutdown": control_plane_shutdown,
    }
    independent = review_fake_campaign(
        destination,
        candidate_documents=candidates,
        campaign_document=campaign,
        configured_credentials=credential_values(os.environ),
    )
    if not independent["passed"]:
        raise ValueError(
            "fake campaign finalization failed independent review: "
            + "; ".join(independent["errors"])
        )
    for phase, path in candidate_paths.items():
        _write_json_exclusive(path, candidates[phase])
    _write_json_exclusive(campaign_path, campaign)
    return {
        "schema": "agent-task-authorized-fake-campaign-finalization/v0.1",
        "passed": True,
        "phase_candidate_sha256": {
            phase: canonical_sha256(candidate)
            for phase, candidate in candidates.items()
        },
        "campaign_sha256": canonical_sha256(campaign),
        "independent_review": independent,
    }
