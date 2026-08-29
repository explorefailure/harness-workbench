#!/usr/bin/env python3
"""Digest-bound, durable, single-use authorization for one provider release."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
from typing import Any

from agent_task_control import Permit
from agent_task_schema import (
    AUTHORIZATION_SCHEMA,
    ContractError,
    SUBJECTS,
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    require_keys,
    require_sha256,
)


MAX_ARTIFACT_BYTES = 64 * 1024
MAX_AUTHORIZATION_LIFETIME_SECONDS = 10 * 60
MAX_CLOCK_SKEW_SECONDS = 5
MAX_BOUND_FILE_BYTES = 8 * 1024 * 1024
RELEASE_CONFIGURATION_SCHEMA = "agent-task-release-configuration/v0.1"
HERE = Path(__file__).resolve().parent


class AuthorizationError(ValueError):
    """A release authorization is invalid, mismatched, expired, or consumed."""


@dataclass(frozen=True)
class AuthorizationExpectation:
    execution_plan_sha256: str
    provider_route_sha256: str
    usage_snapshot_sha256: str
    campaign_nonce: str
    phase: str
    subject: str
    model: str
    store_nonce: str
    request_id: str
    base_attempt_ordinal: int
    base_attempt_token: str
    call_id: int

    @classmethod
    def from_permit(
        cls,
        permit: Permit,
        *,
        execution_plan_sha256: str,
        provider_route_sha256: str,
        model: str,
    ) -> "AuthorizationExpectation":
        return cls(
            execution_plan_sha256=execution_plan_sha256,
            provider_route_sha256=provider_route_sha256,
            usage_snapshot_sha256=permit.usage_sha256,
            campaign_nonce=permit.campaign_nonce,
            phase=permit.phase,
            subject=permit.subject,
            model=model,
            store_nonce=permit.store_nonce,
            request_id=permit.request_id,
            base_attempt_ordinal=permit.base_attempt_ordinal,
            base_attempt_token=permit.base_attempt_token,
            call_id=permit.call_id,
        )


def load_authorization_key(path: Path) -> bytes:
    key = _read_regular_file(
        path, maximum_bytes=32, label="authorization key", owner_only=True
    )
    if len(key) != 32:
        raise AuthorizationError("authorization key must contain exactly 32 bytes")
    return key


def build_authorization(
    expectation: AuthorizationExpectation,
    *,
    authorization_id: str,
    issued_at: dt.datetime,
    expires_at: dt.datetime,
    key: bytes,
) -> dict[str, Any]:
    """Build one artifact for explicit operator review; this performs no I/O."""
    payload = {
        "schema": AUTHORIZATION_SCHEMA,
        "authorization_id": authorization_id,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        **asdict(expectation),
        "maximum_provider_calls": 1,
        "automatic_retry": False,
    }
    signature = "hmac-sha256:" + hmac.new(
        _require_key(key), canonical_bytes(payload), hashlib.sha256
    ).hexdigest()
    return {"payload": payload, "signature": signature}


def artifact_bytes(artifact: dict[str, Any]) -> bytes:
    return canonical_bytes(artifact) + b"\n"


def _require_key(key: bytes) -> bytes:
    if type(key) is not bytes or len(key) != 32:
        raise AuthorizationError("authorization verifier key must be exactly 32 bytes")
    return key


def _read_regular_file(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
    owner_only: bool = False,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AuthorizationError(f"{label} cannot be opened safely") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AuthorizationError(f"{label} must be a regular file")
        if owner_only and metadata.st_mode & 0o077:
            raise AuthorizationError(f"{label} must be owner-only")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise AuthorizationError(f"{label} size is invalid")
        chunks = bytearray()
        while len(chunks) <= maximum_bytes:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if not chunks or len(chunks) > maximum_bytes:
            raise AuthorizationError(f"{label} size is invalid")
        return bytes(chunks)
    finally:
        os.close(descriptor)


def _file_sha256(path: Path) -> str:
    raw = _read_regular_file(
        path, maximum_bytes=MAX_BOUND_FILE_BYTES, label="release-bound file"
    )
    return bytes_sha256(raw)


def validate_bound_files(bound_files: Any) -> None:
    if type(bound_files) is not dict or not bound_files or len(bound_files) > 128:
        raise AuthorizationError("release-bound files must be a finite nonempty map")
    for raw_path, expected in bound_files.items():
        if type(raw_path) is not str or not Path(raw_path).is_absolute():
            raise AuthorizationError("release-bound file paths must be absolute")
        require_sha256(expected, f"release-bound digest for {raw_path}")
        try:
            observed = _file_sha256(Path(raw_path))
        except AuthorizationError:
            raise
        except OSError as error:
            raise AuthorizationError("release-bound file is unreadable") from error
        if observed != expected:
            raise AuthorizationError(f"release-bound file drifted: {raw_path}")


def build_release_configuration(
    plan_result: dict[str, Any],
    *,
    key_file: Path,
    consumed_dir: Path,
    apparatus_root: Path = HERE,
) -> dict[str, Any]:
    """Bind one authenticated service configuration to one exact zero-call plan."""
    outer = require_keys(
        plan_result,
        required={"execution_plan", "execution_plan_sha256"},
        label="live plan result",
    )
    plan = outer["execution_plan"]
    if type(plan) is not dict:
        raise AuthorizationError("live execution plan must be an object")
    apparatus = plan.get("inputs", {}).get("apparatus")
    if type(apparatus) is not dict:
        raise AuthorizationError("live execution plan has no apparatus map")
    root = apparatus_root.resolve(strict=True)
    bound_files: dict[str, str] = {}
    for name, digest in apparatus.items():
        if type(name) is not str or not name or name != Path(name).name:
            raise AuthorizationError("apparatus names must be canonical basenames")
        bound_files[str(root / name)] = digest
    routes = plan.get("provider_pins", {}).get("routes")
    route_sha256 = plan.get("provider_pins", {}).get("route_sha256")
    if type(routes) is not dict or type(route_sha256) is not dict:
        raise AuthorizationError("live execution plan has no provider route bindings")
    release_routes = {
        subject: {
            "provider_route_sha256": route_sha256.get(subject),
            "model": routes.get(subject, {}).get("model"),
        }
        for subject in SUBJECTS
    }
    consumed_parent = consumed_dir.parent.resolve(strict=True)
    resolved_consumed = consumed_parent / consumed_dir.name
    if resolved_consumed.exists() or resolved_consumed.is_symlink():
        raise AuthorizationError(
            "new release configuration requires a fresh consumption directory"
        )
    configuration = {
        "schema": RELEASE_CONFIGURATION_SCHEMA,
        "execution_plan": plan,
        "execution_plan_sha256": outer["execution_plan_sha256"],
        "apparatus_root": str(root),
        "bound_files": bound_files,
        "routes": release_routes,
        "key_file": str(key_file.resolve(strict=True)),
        "consumed_dir": str(resolved_consumed),
    }
    validate_release_configuration(configuration, require_destination_nonexistent=True)
    return configuration


def validate_release_configuration(
    value: Any, *, require_destination_nonexistent: bool
) -> dict[str, Any]:
    try:
        config = require_keys(
            value,
            required={
                "schema", "execution_plan", "execution_plan_sha256",
                "apparatus_root", "bound_files", "routes", "key_file",
                "consumed_dir",
            },
            label="release configuration",
        )
        if config["schema"] != RELEASE_CONFIGURATION_SCHEMA:
            raise AuthorizationError("release configuration schema is unsupported")
        plan = config["execution_plan"]
        if type(plan) is not dict:
            raise AuthorizationError("release configuration plan must be an object")
        require_sha256(
            config["execution_plan_sha256"], "release execution_plan_sha256"
        )
        if canonical_sha256(plan) != config["execution_plan_sha256"]:
            raise AuthorizationError("release execution plan digest does not match")
        if (
            plan.get("schema") != "agent-task-live-execution-plan/v0.1"
            or plan.get("mode") != "plan_only"
            or plan.get("network_calls_authorized") != 0
            or plan.get("paid_provider_calls_authorized") != 0
            or plan.get("release", {}).get("enabled") is not False
        ):
            raise AuthorizationError("release configuration requires an exact zero-call plan")
        if plan.get("usage", {}).get("fresh", {}).get("gate_passed") is not True:
            raise AuthorizationError("release configuration requires fresh passing usage")
        destination = plan.get("destination", {}).get("resolved")
        if type(destination) is not str or not Path(destination).is_absolute():
            raise AuthorizationError("release destination must be absolute")
        if require_destination_nonexistent and (
            Path(destination).exists() or Path(destination).is_symlink()
        ):
            raise AuthorizationError("release destination is no longer nonexistent")
        root = Path(config["apparatus_root"])
        if not root.is_absolute() or root.resolve(strict=True) != root:
            raise AuthorizationError("release apparatus root must be resolved")
        apparatus = plan.get("inputs", {}).get("apparatus")
        if type(apparatus) is not dict or not apparatus:
            raise AuthorizationError("release plan apparatus map is missing")
        if canonical_sha256(apparatus) != plan.get("inputs", {}).get(
            "apparatus_map_sha256"
        ):
            raise AuthorizationError("release plan apparatus map digest does not match")
        expected_bound: dict[str, str] = {}
        for name, digest in apparatus.items():
            if type(name) is not str or not name or name != Path(name).name:
                raise AuthorizationError("release apparatus name is not canonical")
            require_sha256(digest, f"release apparatus digest for {name}")
            expected_bound[str(root / name)] = digest
        if config["bound_files"] != expected_bound:
            raise AuthorizationError("release-bound files do not equal the plan apparatus")
        routes = plan.get("provider_pins", {}).get("routes")
        route_digests = plan.get("provider_pins", {}).get("route_sha256")
        if (
            type(routes) is not dict
            or type(route_digests) is not dict
            or set(routes) != set(SUBJECTS)
            or set(route_digests) != set(SUBJECTS)
            or set(config["routes"]) != set(SUBJECTS)
        ):
            raise AuthorizationError("release provider routes are not exact-five")
        for subject in SUBJECTS:
            route = routes[subject]
            if canonical_sha256(route) != route_digests[subject]:
                raise AuthorizationError(f"release route digest drifted: {subject}")
            expected_route = {
                "provider_route_sha256": route_digests[subject],
                "model": route.get("model") if type(route) is dict else None,
            }
            if (
                type(expected_route["model"]) is not str
                or not expected_route["model"]
                or config["routes"][subject] != expected_route
            ):
                raise AuthorizationError(f"release route binding drifted: {subject}")
        load_authorization_key(Path(config["key_file"]))
        consumed = Path(config["consumed_dir"])
        if not consumed.is_absolute() or not consumed.parent.resolve(strict=True).is_dir():
            raise AuthorizationError("authorization consumption parent is invalid")
        validate_bound_files(config["bound_files"])
    except ContractError as error:
        raise AuthorizationError(str(error)) from error
    return config


def _timestamp(value: Any, label: str) -> dt.datetime:
    if type(value) is not str:
        raise AuthorizationError(f"{label} must be a timezone-aware timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AuthorizationError(f"{label} is not an ISO timestamp") from error
    if parsed.tzinfo is None:
        raise AuthorizationError(f"{label} must be timezone-aware")
    return parsed.astimezone(dt.timezone.utc)


def _validate_payload(
    payload: Any,
    expectation: AuthorizationExpectation,
    *,
    now: dt.datetime,
) -> dict[str, Any]:
    expected_fields = set(asdict(expectation))
    row = require_keys(
        payload,
        required={
            "schema", "authorization_id", "issued_at", "expires_at",
            "maximum_provider_calls", "automatic_retry", *expected_fields,
        },
        label="authorization.payload",
    )
    if row["schema"] != AUTHORIZATION_SCHEMA:
        raise AuthorizationError("authorization schema is unsupported")
    authorization_id = row["authorization_id"]
    if (
        type(authorization_id) is not str
        or len(authorization_id) != 64
        or any(character not in "0123456789abcdef" for character in authorization_id)
    ):
        raise AuthorizationError("authorization_id must be 32 lowercase hex bytes")
    if row["maximum_provider_calls"] != 1 or row["automatic_retry"] is not False:
        raise AuthorizationError("authorization must cover one call with no automatic retry")
    if row["subject"] not in SUBJECTS:
        raise AuthorizationError("authorization subject is unsupported")
    for label in (
        "campaign_nonce", "phase", "model", "store_nonce", "request_id",
        "base_attempt_token",
    ):
        if type(row[label]) is not str or not row[label]:
            raise AuthorizationError(f"authorization {label} must be nonempty")
    if type(row["base_attempt_ordinal"]) is not int or row["base_attempt_ordinal"] < 0:
        raise AuthorizationError("base_attempt_ordinal must be nonnegative")
    if type(row["call_id"]) is not int or row["call_id"] <= 0:
        raise AuthorizationError("call_id must be positive")
    for label in (
        "execution_plan_sha256", "provider_route_sha256", "usage_snapshot_sha256",
    ):
        require_sha256(row[label], f"authorization.{label}")
    issued_at = _timestamp(row["issued_at"], "authorization.issued_at")
    expires_at = _timestamp(row["expires_at"], "authorization.expires_at")
    if expires_at <= issued_at:
        raise AuthorizationError("authorization expiry must follow issuance")
    if (expires_at - issued_at).total_seconds() > MAX_AUTHORIZATION_LIFETIME_SECONDS:
        raise AuthorizationError("authorization lifetime exceeds ten minutes")
    if issued_at > now + dt.timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise AuthorizationError("authorization issuance is in the future")
    if now >= expires_at:
        raise AuthorizationError("authorization has expired")
    for label, expected in asdict(expectation).items():
        if row[label] != expected:
            raise AuthorizationError(f"authorization {label} does not match the permit")
    return row


class OneAttemptAuthorizer:
    """Validate and durably consume authorizations across process restarts."""

    def __init__(self, consumed_dir: Path, *, key: bytes) -> None:
        self.consumed_dir = consumed_dir
        self.key = _require_key(key)
        try:
            consumed_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        except FileExistsError:
            metadata = consumed_dir.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or consumed_dir.is_symlink()
                or metadata.st_mode & 0o077
            ):
                raise AuthorizationError(
                    "authorization consumption directory must be owner-only"
                )

    def consume(
        self,
        artifact_path: Path,
        expectation: AuthorizationExpectation,
        *,
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        current = now or dt.datetime.now(dt.timezone.utc)
        if current.tzinfo is None:
            raise AuthorizationError("authorization validation time must be timezone-aware")
        raw = _read_regular_file(
            artifact_path,
            maximum_bytes=MAX_ARTIFACT_BYTES,
            label="authorization artifact",
        )
        try:
            artifact = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuthorizationError("authorization artifact is not JSON") from error
        if raw != artifact_bytes(artifact):
            raise AuthorizationError("authorization artifact is not canonical JSON")
        try:
            outer = require_keys(
                artifact,
                required={"payload", "signature"},
                label="authorization",
            )
        except ContractError as error:
            raise AuthorizationError(str(error)) from error
        signature = outer["signature"]
        if (
            type(signature) is not str
            or len(signature) != 76
            or not signature.startswith("hmac-sha256:")
            or any(character not in "0123456789abcdef" for character in signature[12:])
        ):
            raise AuthorizationError("authorization signature is malformed")
        expected_signature = "hmac-sha256:" + hmac.new(
            self.key, canonical_bytes(outer["payload"]), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise AuthorizationError("authorization signature is invalid")
        try:
            payload = _validate_payload(outer["payload"], expectation, now=current)
        except ContractError as error:
            raise AuthorizationError(str(error)) from error
        receipt = {
            "schema": "agent-task-authorization-consumption/v0.1",
            "authorization_id": payload["authorization_id"],
            "authorization_artifact_sha256": bytes_sha256(raw),
            "execution_plan_sha256": payload["execution_plan_sha256"],
            "provider_route_sha256": payload["provider_route_sha256"],
            "usage_snapshot_sha256": payload["usage_snapshot_sha256"],
            "campaign_nonce": payload["campaign_nonce"],
            "phase": payload["phase"],
            "subject": payload["subject"],
            "store_nonce": payload["store_nonce"],
            "request_id": payload["request_id"],
            "base_attempt_ordinal": payload["base_attempt_ordinal"],
            "base_attempt_token": payload["base_attempt_token"],
            "call_id": payload["call_id"],
            "consumed_at": current.astimezone(dt.timezone.utc).isoformat(),
            "maximum_provider_calls": 1,
            "automatic_retry": False,
        }
        marker_name = hashlib.sha256(payload["authorization_id"].encode("ascii")).hexdigest()
        marker = self.consumed_dir / f"{marker_name}.json"
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise AuthorizationError("authorization was already consumed") from error
        try:
            encoded = canonical_bytes(receipt) + b"\n"
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(self.consumed_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return receipt
