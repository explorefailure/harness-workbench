"""Subject-neutral capture helpers for the cross-harness experiment."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Any


EXPECTED_CONTENT = b"cross-harness control\n"
DEFAULT_STDOUT_LIMIT = 1_048_576
DEFAULT_STDERR_LIMIT = 524_288
DEFAULT_EVIDENCE_LIMIT = 524_288


@dataclass(frozen=True)
class ProcessResult:
    args: list[str]
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_source_bytes: int
    stderr_source_bytes: int
    termination_reason: str | None
    stdout_overflow: bool
    stderr_overflow: bool


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def credential_values(environment: dict[str, str]) -> tuple[str, ...]:
    markers = ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")
    values = {
        value
        for name, value in environment.items()
        if any(marker in name.upper() for marker in markers)
        and isinstance(value, str)
        and len(value) >= 8
    }
    return tuple(sorted(values, key=len, reverse=True))


def redact_bytes(raw: bytes, values: tuple[str, ...]) -> tuple[bytes, int]:
    redacted = raw
    count = 0
    for value in values:
        variants = {
            value.encode("utf-8"),
            json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"),
        }
        for variant in sorted(variants, key=len, reverse=True):
            if not variant:
                continue
            occurrences = redacted.count(variant)
            if occurrences:
                redacted = redacted.replace(variant, b"[REDACTED]")
                count += occurrences
    return redacted, count


def capture_bytes(
    raw: bytes,
    *,
    redactions: tuple[str, ...] = (),
    source_bytes: int | None = None,
) -> dict[str, Any]:
    stored, redaction_count = redact_bytes(raw, redactions)
    result: dict[str, Any] = {
        "bytes": len(stored),
        "source_bytes": len(raw) if source_bytes is None else source_bytes,
        "sha256": hashlib.sha256(stored).hexdigest(),
        "base64": base64.b64encode(stored).decode("ascii"),
        "redaction_count": redaction_count,
    }
    try:
        result["text"] = stored.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        result["text"] = None
    return result


def manifest(root: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(root).as_posix()
            stat = path.stat()
            entries.append({
                "path": relative,
                "mode": stat.st_mode & 0o777,
                "size": stat.st_size,
                "sha256": file_digest(path),
            })
    return entries


def normalized_path(raw: Any, workspace: Path) -> str | None:
    if not isinstance(raw, str):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = workspace / path
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return "<outside-workspace>"


def parse_jsonl(raw: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    events = []
    errors = []
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        return [], [f"stdout is not UTF-8: {error}"]
    for number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append(f"line {number} is not JSON: {error.msg}")
            continue
        if not isinstance(event, dict):
            errors.append(f"line {number} is not a JSON object")
            continue
        events.append(event)
    return events, errors


def run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 120,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    termination_grace: float = 5,
) -> ProcessResult:
    if (
        timeout <= 0
        or stdout_limit <= 0
        or stderr_limit <= 0
        or termination_grace <= 0
    ):
        raise ValueError("process timeout and capture limits must be positive")
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    overflow = {"stdout": False, "stderr": False}
    source_bytes = {"stdout": 0, "stderr": 0}
    deadline = time.monotonic() + timeout
    termination_reason: str | None = None
    term_sent_at: float | None = None

    def signal_group(sig: int) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def terminate(reason: str) -> None:
        nonlocal termination_reason, term_sent_at
        if termination_reason is not None:
            return
        termination_reason = reason
        term_sent_at = time.monotonic()
        if process.poll() is None:
            signal_group(signal.SIGTERM)

    while selector.get_map():
        now = time.monotonic()
        if termination_reason is None and now >= deadline:
            terminate("timeout")
        if (
            termination_reason is not None
            and term_sent_at is not None
            and now - term_sent_at >= termination_grace
        ):
            if process.poll() is None:
                signal_group(signal.SIGKILL)
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
            break
        events = selector.select(timeout=0.05)
        for key, _ in events:
            stream = key.data
            try:
                chunk = os.read(key.fileobj.fileno(), 65_536)
            except BlockingIOError:
                continue
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            source_bytes[stream] += len(chunk)
            remaining = limits[stream] - len(buffers[stream])
            if remaining > 0:
                buffers[stream].extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow[stream] = True
                terminate(f"{stream}_limit")
    selector.close()
    process.stdout.close()
    process.stderr.close()
    process.wait()
    if termination_reason == "timeout":
        returncode = 124
    elif termination_reason in {"stdout_limit", "stderr_limit"}:
        returncode = 125
    else:
        returncode = process.returncode
    return ProcessResult(
        args=argv,
        returncode=returncode,
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
        stdout_source_bytes=source_bytes["stdout"],
        stderr_source_bytes=source_bytes["stderr"],
        termination_reason=termination_reason,
        stdout_overflow=overflow["stdout"],
        stderr_overflow=overflow["stderr"],
    )


def outcome(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    before_map = {entry["path"]: entry for entry in before}
    after_map = {entry["path"]: entry for entry in after}
    errors = []
    if set(before_map) != {"hook.py", "task.md"}:
        errors.append("workspace fixture is not exact")
    if set(after_map) != {"hook.py", "task.md", "shared.txt"}:
        errors.append("workspace effects are not exact")
    for path in ("hook.py", "task.md"):
        if before_map.get(path) != after_map.get(path):
            errors.append(f"fixture input changed: {path}")
    expected_sha = hashlib.sha256(EXPECTED_CONTENT).hexdigest()
    if after_map.get("shared.txt", {}).get("sha256") != expected_sha:
        errors.append("shared.txt does not contain the exact expected bytes")
    return {
        "passed": not errors,
        "errors": errors,
        "declared_effect": "shared.txt",
        "effect_sha256": after_map.get("shared.txt", {}).get("sha256"),
        "expected_sha256": expected_sha,
    }


def repair_outcome(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    initial_test: ProcessResult,
    final_test: ProcessResult,
    tool_executions: list[dict[str, Any]],
) -> dict[str, Any]:
    before_map = {entry["path"]: entry for entry in before}
    after_map = {entry["path"]: entry for entry in after}
    expected_paths = {"hook.py", "repair_task.md", "slugger.py", "test_slugger.py"}
    errors = []
    if set(before_map) != expected_paths or set(after_map) != expected_paths:
        errors.append("repair workspace file set is not exact")
    for path in ("hook.py", "repair_task.md", "test_slugger.py"):
        if before_map.get(path) != after_map.get(path):
            errors.append(f"repair invariant changed: {path}")
    if before_map.get("slugger.py") == after_map.get("slugger.py"):
        errors.append("slugger.py did not change")
    if initial_test.returncode != 1 or initial_test.termination_reason is not None:
        errors.append("external initial test was not red")
    if final_test.returncode != 0 or final_test.termination_reason is not None:
        errors.append("external final test was not green")

    failed_command = None
    mutation = None
    passing_command = None
    for index, execution in enumerate(tool_executions):
        kind = execution.get("effect_kind")
        reported_error = execution.get("reported_error")
        operation = execution.get("operation")
        operation_exit_code = execution.get("operation_exit_code")
        command_failed = reported_error is True or (
            isinstance(operation_exit_code, int) and operation_exit_code != 0
        )
        command_passed = (
            operation_exit_code == 0
            or (operation_exit_code is None and reported_error is False)
        )
        if (
            failed_command is None
            and kind == "command"
            and operation == "python_unittest_v"
            and command_failed
        ):
            failed_command = index
        elif (
            failed_command is not None
            and mutation is None
            and kind == "write"
            and reported_error is False
        ):
            mutation = index
        elif (
            mutation is not None
            and kind == "command"
            and operation == "python_unittest_v"
            and command_passed
        ):
            passing_command = index
            break
    if failed_command is None or mutation is None or passing_command is None:
        errors.append("subject evidence lacks red-command -> write -> green-command")

    return {
        "passed": not errors,
        "errors": errors,
        "declared_effect": "slugger.py repair",
        "effect_sha256": after_map.get("slugger.py", {}).get("sha256"),
        "external_tests": {
            "initial_returncode": initial_test.returncode,
            "final_returncode": final_test.returncode,
        },
        "subject_sequence": {
            "failed_command_index": failed_command,
            "mutation_index": mutation,
            "passing_command_index": passing_command,
        },
    }
