#!/usr/bin/env python3
"""Closed, experiment-local contracts for declarative agent-task evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any


SUBJECTS = ("claude", "codex", "deepseek", "hermes", "pi")
TASK_SCHEMA = "agent-task/v0.1"
WORKSPACE_SCHEMA = "agent-workspace-archive/v0.1"
EFFECTS_SCHEMA = "agent-effects-archive/v0.1"
RUN_SCHEMA = "cross-harness-agent-task-run/v0.1"
COMPARISON_SCHEMA = "cross-harness-agent-task-comparison/v0.1"
CALL_CONTROL_SCHEMA = "agent-task-call-control/v0.1"
PROCESS_REGISTRY_SCHEMA = "agent-task-process-registry/v0.1"
PHASE_CHECKPOINT_SCHEMA = "agent-task-phase-checkpoint/v0.1"
SUPERVISOR_STOP_SCHEMA = "agent-task-supervisor-stop/v0.1"
AUTHORIZATION_SCHEMA = "agent-task-one-attempt-authorization/v0.1"

MAX_PROMPT_BYTES = 128 * 1024
MAX_ARCHIVE_BYTES = 72 * 1024 * 1024
MAX_EFFECTS_BYTES = 1536 * 1024
MAX_FILES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 64 * 1024 * 1024


class ContractError(ValueError):
    """The retained object cannot be interpreted under the frozen contract."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ContractError(f"value is not canonical JSON: {error}") from error


def canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def require_sha256(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ContractError(f"{label} must be a lowercase sha256:<hex> digest")
    return value


def require_keys(
    value: Any, *, required: set[str], optional: set[str] = frozenset(), label: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ContractError(f"{label} must be an object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise ContractError(f"{label} is missing keys: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{label} has unknown keys: {', '.join(unknown)}")
    return value


def require_relative_path(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ContractError(f"{label} must be a nonempty relative UTF-8 path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ContractError(f"{label} is not a canonical contained path: {value!r}")
    if value.startswith("blobs/") or value == "manifest.json":
        raise ContractError(f"{label} uses a reserved archive path: {value!r}")
    return value


def _require_positive_int(value: Any, label: str, maximum: int) -> int:
    if type(value) is not int or value <= 0 or value > maximum:
        raise ContractError(f"{label} must be between 1 and {maximum}")
    return value


def _require_nonnegative_int(value: Any, label: str, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise ContractError(f"{label} must be between 0 and {maximum}")
    return value


def _validate_assertion(value: Any, label: str) -> dict[str, Any]:
    row = require_keys(
        value,
        required={"path", "kind", "mode"},
        optional={"sha256", "size"},
        label=label,
    )
    require_relative_path(row["path"], f"{label}.path")
    if row["kind"] not in {"absent", "directory", "file"}:
        raise ContractError(f"{label}.kind is unsupported")
    if type(row["mode"]) is not int or row["mode"] < 0 or row["mode"] > 0o777:
        raise ContractError(f"{label}.mode must be a portable permission mode")
    if row["kind"] == "file":
        require_sha256(row.get("sha256"), f"{label}.sha256")
        _require_nonnegative_int(row.get("size"), f"{label}.size", MAX_FILE_BYTES)
    elif "sha256" in row or "size" in row:
        raise ContractError(f"{label} non-file assertion carries file fields")
    return row


def _validate_operation(value: Any, label: str) -> dict[str, Any]:
    row = require_keys(
        value,
        required={"op", "path", "kind", "mode"},
        optional={"sha256", "size"},
        label=label,
    )
    if row["op"] not in {"create", "modify", "delete"}:
        raise ContractError(f"{label}.op is unsupported")
    require_relative_path(row["path"], f"{label}.path")
    if row["kind"] not in {"directory", "file"}:
        raise ContractError(f"{label}.kind is unsupported")
    if type(row["mode"]) is not int or row["mode"] < 0 or row["mode"] > 0o777:
        raise ContractError(f"{label}.mode must be a portable permission mode")
    if row["op"] != "delete" and row["kind"] == "file":
        require_sha256(row.get("sha256"), f"{label}.sha256")
        _require_nonnegative_int(row.get("size"), f"{label}.size", MAX_FILE_BYTES)
    elif "sha256" in row or "size" in row:
        raise ContractError(f"{label} carries inapplicable content fields")
    return row


def validate_task(value: Any) -> dict[str, Any]:
    task = require_keys(
        value,
        required={
            "schema",
            "task_id",
            "prompt",
            "workspace_archive_sha256",
            "effects_policy",
            "verification",
            "limits",
        },
        label="task",
    )
    if task["schema"] != TASK_SCHEMA:
        raise ContractError(f"unsupported task schema: {task['schema']!r}")
    if type(task["task_id"]) is not str or not task["task_id"]:
        raise ContractError("task.task_id must be a nonempty string")
    if type(task["prompt"]) is not str:
        raise ContractError("task.prompt must be a string")
    if len(task["prompt"].encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ContractError("task.prompt exceeds the v0.1 bound")
    require_sha256(task["workspace_archive_sha256"], "task.workspace_archive_sha256")
    policy = require_keys(
        task["effects_policy"], required={"operations"}, label="task.effects_policy"
    )
    if type(policy["operations"]) is not list:
        raise ContractError("task.effects_policy.operations must be an array")
    operations = [
        _validate_operation(row, f"task.effects_policy.operations[{index}]")
        for index, row in enumerate(policy["operations"])
    ]
    if [row["path"] for row in operations] != sorted(
        row["path"] for row in operations
    ) or len({row["path"] for row in operations}) != len(operations):
        raise ContractError("task effect operations must have unique sorted paths")
    verification = require_keys(
        task["verification"], required={"pre", "post"}, label="task.verification"
    )
    for phase in ("pre", "post"):
        rows = verification[phase]
        if type(rows) is not list:
            raise ContractError(f"task.verification.{phase} must be an array")
        checked = [
            _validate_assertion(row, f"task.verification.{phase}[{index}]")
            for index, row in enumerate(rows)
        ]
        if [row["path"] for row in checked] != sorted(
            row["path"] for row in checked
        ) or len({row["path"] for row in checked}) != len(checked):
            raise ContractError(
                f"task.verification.{phase} paths must be unique and sorted"
            )
    limits = require_keys(
        task["limits"],
        required={
            "episode_seconds",
            "stdout_bytes",
            "stderr_bytes",
            "archive_bytes",
            "effects_bytes",
            "files",
            "file_bytes",
            "total_file_bytes",
        },
        label="task.limits",
    )
    maxima = {
        "episode_seconds": 900,
        "stdout_bytes": 4 * 1024 * 1024,
        "stderr_bytes": 4 * 1024 * 1024,
        "archive_bytes": MAX_ARCHIVE_BYTES,
        "effects_bytes": MAX_EFFECTS_BYTES,
        "files": MAX_FILES,
        "file_bytes": MAX_FILE_BYTES,
        "total_file_bytes": MAX_TOTAL_FILE_BYTES,
    }
    for name, maximum in maxima.items():
        _require_positive_int(limits[name], f"task.limits.{name}", maximum)
    return task


def validate_archive_manifest(value: Any, expected_schema: str) -> dict[str, Any]:
    archive_doc = require_keys(
        value, required={"schema", "entries"}, label="archive manifest"
    )
    if archive_doc["schema"] != expected_schema:
        raise ContractError(f"unexpected archive schema: {archive_doc['schema']!r}")
    if type(archive_doc["entries"]) is not list:
        raise ContractError("archive entries must be an array")
    seen: set[str] = set()
    for index, row in enumerate(archive_doc["entries"]):
        label = f"archive entries[{index}]"
        if expected_schema == WORKSPACE_SCHEMA:
            row = require_keys(
                row,
                required={"path", "kind", "mode"},
                optional={"sha256", "size"},
                label=label,
            )
            require_relative_path(row["path"], f"{label}.path")
            if row["kind"] not in {"directory", "file"}:
                raise ContractError(f"{label}.kind is unsupported")
            if row["kind"] == "file":
                require_sha256(row.get("sha256"), f"{label}.sha256")
                _require_nonnegative_int(
                    row.get("size"), f"{label}.size", MAX_FILE_BYTES
                )
            elif "sha256" in row or "size" in row:
                raise ContractError(f"{label} directory carries file fields")
        else:
            row = _validate_operation(row, label)
        if row["path"] in seen:
            raise ContractError(f"duplicate archive path: {row['path']}")
        seen.add(row["path"])
    if [row["path"] for row in archive_doc["entries"]] != sorted(seen):
        raise ContractError("archive entries are not sorted by path")
    return archive_doc


def validate_run(value: Any) -> dict[str, Any]:
    run = require_keys(
        value,
        required={
            "schema", "subject", "task_sha256", "input_archive_sha256",
            "store_nonce", "base_attempt", "provider", "workspace",
            "effects_archive", "verdict",
        },
        label="agent-task run",
    )
    if run["schema"] != RUN_SCHEMA:
        raise ContractError(f"unsupported run schema: {run['schema']!r}")
    if run["subject"] not in SUBJECTS:
        raise ContractError("run subject is not one of the exact five")
    require_sha256(run["task_sha256"], "run.task_sha256")
    require_sha256(run["input_archive_sha256"], "run.input_archive_sha256")
    if type(run["store_nonce"]) is not str or len(run["store_nonce"]) < 16:
        raise ContractError("run.store_nonce is not a stable opaque nonce")
    attempt = require_keys(
        run["base_attempt"], required={"ordinal", "token", "call_id"},
        label="run.base_attempt",
    )
    if type(attempt["ordinal"]) is not int or attempt["ordinal"] < 0:
        raise ContractError("run.base_attempt.ordinal must be nonnegative")
    if type(attempt["call_id"]) is not int or attempt["call_id"] <= 0:
        raise ContractError("run.base_attempt.call_id must be positive")
    token_prefix = "agent-attempt-v0.1:"
    if type(attempt["token"]) is not str or not attempt["token"].startswith(
        token_prefix
    ):
        raise ContractError("run.base_attempt.token is invalid")
    require_sha256(
        attempt["token"][len(token_prefix):], "run.base_attempt.token digest"
    )
    provider = require_keys(
        run["provider"],
        required={"invoked", "route", "capture", "cleanup_receipt"},
        optional={"lifecycle"},
        label="run.provider",
    )
    if provider["route"] != run["subject"] or type(provider["invoked"]) is not bool:
        raise ContractError("run.provider route/invocation is inconsistent")
    workspace = require_keys(
        run["workspace"], required={"before", "after"}, label="run.workspace"
    )
    for name in ("before", "after"):
        validate_archive_manifest(
            {"schema": WORKSPACE_SCHEMA, "entries": workspace[name]},
            WORKSPACE_SCHEMA,
        )
    effects = require_keys(
        run["effects_archive"], required={"sha256", "bytes", "base64"},
        label="run.effects_archive",
    )
    require_sha256(effects["sha256"], "run.effects_archive.sha256")
    _require_positive_int(effects["bytes"], "run.effects_archive.bytes", MAX_EFFECTS_BYTES)
    if type(effects["base64"]) is not str:
        raise ContractError("run.effects_archive.base64 must be a string")
    verdict = require_keys(
        run["verdict"],
        required={"adapter_valid", "safety_eligible", "task_passed", "errors"},
        label="run.verdict",
    )
    if any(type(verdict[name]) is not bool for name in (
        "adapter_valid", "safety_eligible", "task_passed"
    )) or type(verdict["errors"]) is not list or not all(
        type(error) is str for error in verdict["errors"]
    ):
        raise ContractError("run.verdict has invalid types")
    return run
