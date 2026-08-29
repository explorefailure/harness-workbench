#!/usr/bin/env python3
"""One explicitly authorized provider attempt with no automatic retry loop."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness_workbench.capture import credential_values, minimal_environment

from agent_task_authorization import AuthorizationExpectation
from agent_task_control import Permit
from agent_task_providers import ProviderTransport
from agent_task_schema import bytes_sha256, canonical_sha256
from agent_task_services import BrokerClient, CallControlClient


@dataclass(frozen=True)
class PreparedAttempt:
    permit: Permit
    authorization: AuthorizationExpectation


class AttemptCoordinatorError(RuntimeError):
    """The prepared attempt cannot be executed without weakening its binding."""


class AuthorizedAttemptCoordinator:
    """Prepare and execute one attempt; operational failure never loops here."""

    def __init__(
        self,
        *,
        plan_result: dict[str, Any],
        task: dict[str, Any],
        control: CallControlClient,
        broker: BrokerClient,
    ) -> None:
        plan = plan_result.get("execution_plan")
        digest = plan_result.get("execution_plan_sha256")
        if type(plan) is not dict or canonical_sha256(plan) != digest:
            raise AttemptCoordinatorError("coordinator execution plan digest is invalid")
        if plan.get("mode") != "plan_only" or plan.get("release", {}).get(
            "enabled"
        ) is not False:
            raise AttemptCoordinatorError("coordinator requires the frozen zero-call plan")
        if canonical_sha256(task) != plan.get("inputs", {}).get("task_sha256"):
            raise AttemptCoordinatorError("coordinator task does not match the plan")
        self.plan_result = plan_result
        self.plan = plan
        self.task = task
        self.control = control
        self.broker = broker
        self.destination = Path(plan["destination"]["resolved"])

    def prepare(
        self,
        *,
        phase: str,
        subject: str,
        request_id: str,
        retry_of: int | None = None,
    ) -> PreparedAttempt:
        try:
            store_nonce = self.plan["store_nonces"][phase][subject]
            route = self.plan["provider_pins"]["routes"][subject]
            route_sha256 = self.plan["provider_pins"]["route_sha256"][subject]
        except (KeyError, TypeError) as error:
            raise AttemptCoordinatorError("attempt is not present in the exact plan") from error
        if canonical_sha256(route) != route_sha256:
            raise AttemptCoordinatorError("planned provider route digest drifted")
        permit = self.control.request(
            phase=phase,
            subject=subject,
            store_nonce=store_nonce,
            request_id=request_id,
            retry_of=retry_of,
        )
        expectation = AuthorizationExpectation.from_permit(
            permit,
            execution_plan_sha256=self.plan_result["execution_plan_sha256"],
            provider_route_sha256=route_sha256,
            model=route["model"],
        )
        return PreparedAttempt(permit=permit, authorization=expectation)

    def execute(
        self,
        prepared: PreparedAttempt,
        *,
        authorization_path: Path,
        workspace: Path,
        transport_plan: Path,
        transport: ProviderTransport,
    ) -> dict[str, Any]:
        def reject(message: str) -> None:
            self.control.latch_stop("authorized_attempt_pre_release_invalid")
            raise AttemptCoordinatorError(message)

        permit = prepared.permit
        expected = AuthorizationExpectation.from_permit(
            permit,
            execution_plan_sha256=self.plan_result["execution_plan_sha256"],
            provider_route_sha256=self.plan["provider_pins"]["route_sha256"][
                permit.subject
            ],
            model=self.plan["provider_pins"]["routes"][permit.subject]["model"],
        )
        if prepared.authorization != expected:
            reject("prepared authorization binding was altered")
        workspace = workspace.resolve(strict=True)
        process_root = (self.destination / "process").resolve(strict=True)
        if not workspace.is_dir() or not workspace.is_relative_to(process_root):
            reject("agent workspace is outside the planned process root")
        transport_plan = transport_plan.resolve(strict=True)
        bundle_root = (self.destination / "bundle").resolve(strict=True)
        if not transport_plan.is_file() or not transport_plan.is_relative_to(bundle_root):
            reject("transport plan is outside the retained bundle")
        if bytes_sha256(transport_plan.read_bytes()) != self.plan["inputs"].get(
            "fake_transport_plan_sha256"
        ):
            reject("transport plan bytes do not match the plan")

        receipt = self.control.release(
            permit, authorization_path=authorization_path
        )
        try:
            argv = transport.command(
                subject=permit.subject,
                workspace=workspace,
                prompt=self.task["prompt"],
                plan=transport_plan,
            )
        except Exception:
            self.control.complete(permit, result="fatal", cleanup_proved=True)
            raise
        environment = minimal_environment(
            process_root / f"provider-home-{permit.call_id}",
            overrides={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            },
        )
        try:
            capture, cleanup = self.broker.launch(
                argv,
                cwd=workspace,
                env=environment,
                phase=permit.phase,
                timeout=self.plan["timeouts"]["subject_episode_seconds"][permit.subject],
                stdout_limit=self.task["limits"]["stdout_bytes"],
                stderr_limit=self.task["limits"]["stderr_bytes"],
                redactions=credential_values(environment),
            )
        except Exception:
            try:
                self.control.latch_stop("authorized_broker_launch_uncertain")
            except Exception:
                pass
            raise
        cleanup_proved = (
            cleanup.get("kind") == "clean_self_issued"
            and cleanup.get("group_alive_after_cleanup") is False
        )
        clean = (
            cleanup_proved
            and capture.get("returncode") == 0
            and capture.get("termination_reason") is None
            and capture.get("group_alive_after_cleanup") is False
            and capture.get("stdout", {}).get("overflow") is False
            and capture.get("stderr", {}).get("overflow") is False
            and capture.get("stdout", {}).get("redaction_count") == 0
            and capture.get("stderr", {}).get("redaction_count") == 0
        )
        result = "success" if clean else (
            "operational_failure" if cleanup_proved else "fatal"
        )
        self.control.complete(
            permit, result=result, cleanup_proved=cleanup_proved
        )
        return {
            "schema": "agent-task-authorized-attempt/v0.1",
            "call_id": permit.call_id,
            "subject": permit.subject,
            "phase": permit.phase,
            "provider_invoked": True,
            "result": result,
            "authorization_receipt": receipt,
            "capture": capture,
            "cleanup_receipt": cleanup,
            "automatic_retry_requested": False,
        }
