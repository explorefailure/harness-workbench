#!/usr/bin/env python3
"""Single-flight, fsynced call ownership for one finite offline/live campaign."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from agent_task_schema import CALL_CONTROL_SCHEMA, ContractError, canonical_sha256


class ControlError(ContractError):
    """A provider permit cannot be issued without guessing ownership."""


@dataclass(frozen=True)
class Permit:
    campaign_nonce: str
    phase: str
    subject: str
    store_nonce: str
    request_id: str
    base_attempt_ordinal: int
    base_attempt_token: str
    call_id: int
    retry_of: int | None
    lease_deadline: float
    usage_sha256: str


UsageGate = Callable[[], tuple[dict[str, Any], bool]]


class CallControl:
    """Authoritative in-process service with a durable append-only journal.

    The runtime can later put this object behind an authenticated IPC boundary;
    its state transitions and crash boundary are already independent of that
    transport. One request is allocated, fsynced, and only then returned.
    """

    def __init__(
        self,
        journal: Path,
        *,
        campaign_nonce: str,
        maximum_calls: int,
        authorized_phases: set[str],
        phase_maximums: dict[str, int] | None = None,
        lease_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not campaign_nonce or maximum_calls <= 0 or lease_seconds <= 0:
            raise ControlError("call-control identity and bounds must be positive")
        self.journal = journal
        self.campaign_nonce = campaign_nonce
        self.maximum_calls = maximum_calls
        self.authorized_phases = set(authorized_phases)
        self._phase_maximums = {
            phase: (phase_maximums or {}).get(phase, maximum_calls)
            for phase in authorized_phases
        }
        if any(value <= 0 for value in self._phase_maximums.values()):
            raise ControlError("phase call bounds must be positive")
        self._phase_counts = {phase: 0 for phase in authorized_phases}
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.state = "ready"
        self._lock = threading.Lock()
        self._next_call_id = 1
        self._ordinals: dict[str, int] = {}
        self._allocations: dict[str, Permit] = {}
        self._released: set[str] = set()
        self._inflight: Permit | None = None
        self._retry_owner: int | None = None
        self._retry_store: str | None = None
        self._retry_subject: str | None = None
        self._retry_phase: str | None = None
        self._retry_deadline: float | None = None
        self._allocated_calls = 0
        self._closed = False
        journal.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        self._append({
            "schema": CALL_CONTROL_SCHEMA,
            "event": "service_started",
            "campaign_nonce": campaign_nonce,
            "maximum_calls": maximum_calls,
            "authorized_phases": sorted(authorized_phases),
            "phase_maximums": dict(sorted(self._phase_maximums.items())),
        })

    def _append(self, row: dict[str, Any]) -> None:
        raw = json.dumps(
            row, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8") + b"\n"
        descriptor = os.open(self.journal, os.O_WRONLY | os.O_APPEND)
        try:
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def authorize_phase(self, phase: str, *, maximum_calls: int) -> None:
        with self._lock:
            self._require_open()
            if self.state == "hard_stop":
                raise ControlError("hard-stop state cannot authorize a phase")
            if phase in self.authorized_phases or maximum_calls <= 0:
                raise ControlError("phase authorization must be new and positively bounded")
            self.authorized_phases.add(phase)
            self._phase_maximums[phase] = maximum_calls
            self._phase_counts[phase] = 0
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "phase_authorized",
                "phase": phase,
                "maximum_calls": maximum_calls,
            })

    def record_usage_boundary(
        self, *, label: str, usage_sha256: str, passed: bool,
    ) -> None:
        """Journal one service-owned non-permit phase-boundary usage gate."""
        with self._lock:
            self._require_open()
            if self.state != "ready" or self._inflight is not None:
                self._latch("usage_boundary_not_ready")
                raise ControlError("usage boundary requires ready single-flight state")
            if label != "after-smoke-before-matrix" or type(passed) is not bool:
                self._latch("usage_boundary_malformed")
                raise ControlError("usage boundary identity is malformed")
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "usage_boundary",
                "label": label,
                "usage_sha256": usage_sha256,
                "passed": passed,
            })
            if not passed:
                self._latch("usage_boundary_blocked")
                raise ControlError("fresh phase-boundary usage gate blocked the matrix")

    def _latch(self, reason: str) -> None:
        if self.state != "hard_stop":
            self.state = "hard_stop"
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "hard_stop",
                "reason": reason,
            })

    def latch_stop(self, reason: str) -> None:
        with self._lock:
            self._require_open()
            self._latch(reason)

    def _require_open(self) -> None:
        if self._closed:
            raise ControlError("call-control service is closed")

    def _token(self, store_nonce: str, ordinal: int, call_id: int) -> str:
        body = (
            f"{self.campaign_nonce}\0{store_nonce}\0{ordinal}\0{call_id}"
        ).encode("utf-8")
        return "agent-attempt-v0.1:sha256:" + hashlib.sha256(body).hexdigest()

    def request(
        self,
        *,
        phase: str,
        subject: str,
        store_nonce: str,
        request_id: str,
        usage_gate: UsageGate,
        retry_of: int | None = None,
    ) -> Permit:
        with self._lock:
            self._require_open()
            now = self.clock()
            if (
                self.state == "retry_pending"
                and self._retry_deadline is not None
                and now >= self._retry_deadline
            ):
                self._latch("stale_retry_pending_lease")
            if request_id in self._allocations:
                permit = self._allocations[request_id]
                if request_id in self._released:
                    self._latch("request_reply_uncertain_after_release")
                    raise ControlError("allocated request was already released")
                if self._inflight != permit or now >= permit.lease_deadline:
                    self._latch("request_reply_reconciliation_uncertain")
                    raise ControlError("allocation cannot be safely reconciled")
                self._append({
                    "schema": CALL_CONTROL_SCHEMA,
                    "event": "allocation_replied_again",
                    "request_id": request_id,
                    "call_id": permit.call_id,
                })
                return permit
            if self.state == "hard_stop":
                raise ControlError("campaign is hard-stopped")
            if phase not in self.authorized_phases:
                self._latch("phase_not_authorized")
                raise ControlError(f"phase is not authorized: {phase}")
            if self._inflight is not None or self.state == "inflight":
                self._latch("single_flight_collision")
                raise ControlError("a provider permit is already inflight")
            if self.state == "retry_pending":
                if (
                    retry_of != self._retry_owner
                    or store_nonce != self._retry_store
                    or subject != self._retry_subject
                    or phase != self._retry_phase
                ):
                    self._latch("wrong_retry_owner")
                    raise ControlError("retry request does not own its predecessor")
            elif retry_of is not None:
                self._latch("unexpected_retry_predecessor")
                raise ControlError("ordinary request supplied a retry predecessor")
            if self._allocated_calls >= self.maximum_calls:
                self._latch("global_call_budget_exhausted")
                raise ControlError("global call budget is exhausted")
            if self._phase_counts[phase] >= self._phase_maximums[phase]:
                self._latch("phase_call_budget_exhausted")
                raise ControlError("phase call budget is exhausted")
            try:
                usage, passed = usage_gate()
            except Exception as error:
                self._latch("usage_gate_unreadable")
                raise ControlError("fresh permit-time usage is unreadable") from error
            if type(usage) is not dict or type(passed) is not bool:
                self._latch("usage_gate_malformed")
                raise ControlError("fresh permit-time usage gate is malformed")
            usage_sha256 = canonical_sha256(usage)
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "usage_gate",
                "phase": phase,
                "subject": subject,
                "request_id": request_id,
                "usage_sha256": usage_sha256,
                "passed": bool(passed),
            })
            if not passed:
                self._latch("usage_gate_blocked")
                raise ControlError("fresh permit-time usage gate blocked the call")
            ordinal = self._ordinals.get(store_nonce, 0)
            call_id = self._next_call_id
            permit = Permit(
                campaign_nonce=self.campaign_nonce,
                phase=phase,
                subject=subject,
                store_nonce=store_nonce,
                request_id=request_id,
                base_attempt_ordinal=ordinal,
                base_attempt_token=self._token(store_nonce, ordinal, call_id),
                call_id=call_id,
                retry_of=retry_of,
                lease_deadline=now + self.lease_seconds,
                usage_sha256=usage_sha256,
            )
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "permit_allocated",
                **asdict(permit),
            })
            self._ordinals[store_nonce] = ordinal + 1
            self._next_call_id += 1
            self._allocated_calls += 1
            self._phase_counts[phase] += 1
            self._allocations[request_id] = permit
            self._inflight = permit
            self.state = "inflight"
            return permit

    def release(self, permit: Permit) -> None:
        with self._lock:
            self._require_open()
            if self._inflight != permit or self.state != "inflight":
                self._latch("permit_release_identity_mismatch")
                raise ControlError("permit release does not match inflight allocation")
            if permit.request_id in self._released:
                self._latch("duplicate_provider_release")
                raise ControlError("provider permit was already released")
            if self.clock() >= permit.lease_deadline:
                self._latch("permit_lease_expired_before_release")
                raise ControlError("provider permit lease expired")
            self._released.add(permit.request_id)
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "provider_released",
                "request_id": permit.request_id,
                "call_id": permit.call_id,
            })

    def complete(
        self,
        permit: Permit,
        *,
        result: str,
        cleanup_proved: bool,
    ) -> None:
        with self._lock:
            self._require_open()
            if self._inflight != permit or permit.request_id not in self._released:
                self._latch("completion_without_released_permit")
                raise ControlError("completion does not match a released permit")
            if not cleanup_proved:
                self._latch("provider_cleanup_unproved")
            elif result in {"measurement", "success"}:
                self.state = "ready"
                self._retry_owner = None
                self._retry_store = None
                self._retry_subject = None
                self._retry_phase = None
                self._retry_deadline = None
            elif result == "operational_failure":
                if permit.retry_of is None:
                    self.state = "retry_pending"
                    self._retry_owner = permit.call_id
                    self._retry_store = permit.store_nonce
                    self._retry_subject = permit.subject
                    self._retry_phase = permit.phase
                    self._retry_deadline = self.clock() + self.lease_seconds
                else:
                    self._latch("retry_operational_failure")
            elif result == "fatal":
                self._latch("fatal_provider_result")
            else:
                self._latch("unknown_provider_result")
                raise ControlError(f"unknown provider completion: {result}")
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "permit_completed",
                "call_id": permit.call_id,
                "request_id": permit.request_id,
                "result": result,
                "cleanup_proved": cleanup_proved,
                "next_state": self.state,
            })
            self._inflight = None

    def expire(self) -> None:
        with self._lock:
            self._require_open()
            if self._inflight is not None and self.clock() >= self._inflight.lease_deadline:
                self._latch("stale_inflight_lease")
            elif (
                self.state == "retry_pending"
                and self._retry_deadline is not None
                and self.clock() >= self._retry_deadline
            ):
                self._latch("stale_retry_pending_lease")

    def refusal(self, *, subject: str, store_nonce: str) -> dict[str, Any]:
        with self._lock:
            self._require_open()
            return {
                "schema": "cross-harness-agent-task-refusal/v0.1",
                "subject": subject,
                "store_nonce": store_nonce,
                "provider_invoked": False,
                "state": self.state,
            }

    @property
    def allocated_calls(self) -> int:
        return self._allocated_calls

    def close(self) -> None:
        with self._lock:
            self._require_open()
            if self._inflight is not None or self.state == "retry_pending":
                self._latch("service_closed_with_unfinished_state")
            self._append({
                "schema": CALL_CONTROL_SCHEMA,
                "event": "service_stopped",
                "kind": "clean_self_issued",
                "state": self.state,
                "allocated_calls": self._allocated_calls,
            })
            self._closed = True
