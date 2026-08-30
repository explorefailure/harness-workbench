#!/usr/bin/env python3
"""Fixed Workbench step boundary for one injected, explicitly authorized fake call."""
from __future__ import annotations

import argparse
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys


SUBJECTS = ("claude", "codex", "deepseek", "hermes", "pi")
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
ENVIRONMENT_KEYS = {
    "HWB_AGENT_TASK_AUTHKEY_B64",
    "HWB_AGENT_TASK_AUTHORIZATION_SOCKET",
    "HWB_AGENT_TASK_BROKER_SOCKET",
    "HWB_AGENT_TASK_CALL_SOCKET",
    "HWB_AGENT_TASK_DESTINATION",
    "HWB_AGENT_TASK_REQUEST_ID",
    "HWB_AGENT_TASK_TRANSPORT",
}


def _refusal(*, phase: str, subject: str, reason: str, error_type: str | None = None) -> int:
    document = {
        "schema": "agent-task-precall-step-refusal/v0.1",
        "phase": phase,
        "subject": subject,
        "provider_invoked": False if error_type is None else None,
        "reason": reason,
    }
    if error_type is not None:
        document["error_type"] = error_type
    sys.stderr.write(json.dumps(document, sort_keys=True) + "\n")
    return 64


def _environment() -> dict[str, str] | None:
    values = {name: os.environ.get(name) for name in ENVIRONMENT_KEYS}
    if all(value is None for value in values.values()):
        return None
    if any(type(value) is not str or not value for value in values.values()):
        raise ValueError("authorized fake step environment is incomplete")
    return {name: value for name, value in values.items() if value is not None}


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            value.update(chunk)
    return "sha256:" + value.hexdigest()


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validate_frozen_inputs(
    *, phase: str, subject: str, store_nonce: str, execution_plan_path: Path,
) -> None:
    spec_path = Path(f"{subject}.json")
    lock_path = Path(f"{subject}.freeze.lock")
    if (
        spec_path.is_symlink() or not spec_path.is_file()
        or lock_path.is_symlink() or not lock_path.is_file()
    ):
        raise ValueError("pre-call spec or freeze lock is not regular")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    steps = spec.get("steps")
    step = steps[0] if type(steps) is list and len(steps) == 1 else None
    if type(step) is not dict or step.get("inputs") != list(STATIC_INPUTS):
        raise ValueError("pre-call spec input set is not exact")
    paths = {name: Path(name) for name in STATIC_INPUTS}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise ValueError("pre-call input is not a regular file")
    observed = {name: _digest(path) for name, path in paths.items()}
    if lock != {"digests": observed}:
        raise ValueError("pre-call freeze lock disagrees")
    outer = json.loads(execution_plan_path.read_text(encoding="utf-8"))
    plan = outer.get("execution_plan") if type(outer) is dict else None
    if (
        type(plan) is not dict
        or _canonical_sha256(plan) != outer.get("execution_plan_sha256")
        or plan.get("store_nonces", {}).get(phase, {}).get(subject) != store_nonce
    ):
        raise ValueError("retained execution plan identity disagrees")
    planned_spec = plan.get("inputs", {}).get("specs", {}).get(
        phase, {}
    ).get(subject)
    if (
        type(planned_spec) is not dict
        or planned_spec.get("document") != spec
        or planned_spec.get("sha256") != _canonical_sha256(spec)
    ):
        raise ValueError("pre-call spec disagrees with the execution plan")
    apparatus = plan.get("inputs", {}).get("apparatus", {})
    if any(
        observed[name] != apparatus.get(name)
        for name in ("agent_task_step.py", *STEP_MODULE_INPUTS)
    ):
        raise ValueError("pre-call apparatus copy disagrees with the execution plan")
    planned_inputs = plan.get("inputs", {})
    if (
        observed["task.json"] != planned_inputs.get("task_file_sha256")
        or observed["workspace.zip"]
        != planned_inputs.get("workspace_archive_sha256")
        or observed["fake-provider-plan.json"]
        != planned_inputs.get("fake_transport_plan_sha256")
    ):
        raise ValueError("pre-call task inputs disagree with the execution plan")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("write-smoke", "repair-matrix"), required=True)
    parser.add_argument("--subject", choices=SUBJECTS, required=True)
    parser.add_argument("--store-nonce", required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--workspace-archive", type=Path, required=True)
    parser.add_argument("--transport-plan", type=Path, required=True)
    parser.add_argument("--execution-plan", type=Path, required=True)
    args = parser.parse_args()
    if len(args.store_nonce) < 16:
        raise SystemExit("store nonce is not a stable opaque identity")
    for path in (
        args.task, args.workspace_archive, args.transport_plan, args.execution_plan,
    ):
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"pre-call step input is not a regular file: {path}")
    try:
        _validate_frozen_inputs(
            phase=args.phase, subject=args.subject, store_nonce=args.store_nonce,
            execution_plan_path=args.execution_plan,
        )
    except Exception:
        return _refusal(
            phase=args.phase, subject=args.subject,
            reason="pre-call freeze or execution-plan binding is invalid",
        )
    try:
        environment = _environment()
    except ValueError:
        return _refusal(
            phase=args.phase, subject=args.subject,
            reason="authorized fake step environment is invalid",
        )
    if environment is None:
        return _refusal(
            phase=args.phase, subject=args.subject,
            reason="provider execution requires an injected fake transport context",
        )
    if environment["HWB_AGENT_TASK_TRANSPORT"] != "fake":
        return _refusal(
            phase=args.phase, subject=args.subject,
            reason="preassembled Workbench step refuses every non-fake transport",
        )

    try:
        from multiprocessing.connection import Client

        from agent_task_coordinator import (
            AuthorizedAttemptCoordinator,
            PreparedAttempt,
        )
        from agent_task_providers import FakeProviderTransport
        from agent_task_runtime import run_authorized_episode
        from agent_task_schema import canonical_bytes
        from agent_task_services import BrokerClient, CallControlClient

        authkey = base64.b64decode(
            environment["HWB_AGENT_TASK_AUTHKEY_B64"], validate=True
        )
        if len(authkey) != 32:
            raise ValueError("service authentication key has the wrong length")
        destination = Path(environment["HWB_AGENT_TASK_DESTINATION"])
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("authorized destination is not a real directory")
        request_prefix = environment["HWB_AGENT_TASK_REQUEST_ID"]
        if request_prefix != Path(request_prefix).name:
            raise ValueError("authorized request prefix is not a basename")
        plan_result = json.loads(args.execution_plan.read_text(encoding="utf-8"))
        task = json.loads(args.task.read_text(encoding="utf-8"))
        plan = plan_result.get("execution_plan", {})
        if (
            plan.get("destination", {}).get("resolved") != str(destination)
            or plan.get("store_nonces", {}).get(args.phase, {}).get(args.subject)
            != args.store_nonce
        ):
            raise ValueError("step identity disagrees with the retained execution plan")
        call = CallControlClient(
            Path(environment["HWB_AGENT_TASK_CALL_SOCKET"]), authkey,
            destination / "session" / "call-control.jsonl",
        )
        broker = BrokerClient(
            Path(environment["HWB_AGENT_TASK_BROKER_SOCKET"]), authkey,
            destination / "session" / "process-registry.jsonl",
        )
        coordinator = AuthorizedAttemptCoordinator(
            plan_result=plan_result, task=task, control=call, broker=broker,
        )

        def bridge_request(request: dict[str, object]) -> dict[str, object]:
            connection = Client(
                environment["HWB_AGENT_TASK_AUTHORIZATION_SOCKET"],
                family="AF_UNIX", authkey=authkey,
            )
            try:
                connection.send(request)
                response = connection.recv()
            finally:
                connection.close()
            if type(response) is not dict or response.get("ok") is not True:
                raise RuntimeError("explicit authorization resolver refused the attempt")
            return response

        claim = bridge_request({"op": "claim"})
        request_id = claim.get("request_id")
        if (
            type(request_id) is not str
            or not request_id.startswith(request_prefix + "-draw-")
            or request_id != Path(request_id).name
        ):
            raise RuntimeError("authorization bridge returned an invalid request identity")

        def resolve(prepared: PreparedAttempt) -> Path:
            response = bridge_request({
                "op": "authorize",
                "permit": asdict(prepared.permit),
                "authorization": asdict(prepared.authorization),
            })
            if (
                set(response) != {"ok", "authorization_path"}
                or type(response["authorization_path"]) is not str
            ):
                raise RuntimeError("explicit authorization resolver refused the attempt")
            return Path(response["authorization_path"])

        episode = run_authorized_episode(
            subject=args.subject,
            task=task,
            workspace_archive=args.workspace_archive.read_bytes(),
            transport_plan=args.transport_plan,
            request_id=request_id,
            phase=args.phase,
            coordinator=coordinator,
            authorization_resolver=resolve,
            transport=FakeProviderTransport(Path(sys.executable)),
        )
        attempt = episode["base_attempt"]
        completion = bridge_request({
            "op": "complete",
            "request_id": request_id,
            "call_id": attempt["call_id"],
            "base_attempt_ordinal": attempt["ordinal"],
        })
        if set(completion) != {"ok"}:
            raise RuntimeError("authorization bridge completion was not exact")
        sys.stdout.buffer.write(canonical_bytes(episode) + b"\n")
        return 0
    except Exception as error:
        return _refusal(
            phase=args.phase, subject=args.subject,
            reason="authorized fake Workbench step failed closed",
            error_type=type(error).__name__,
        )


if __name__ == "__main__":
    raise SystemExit(main())
