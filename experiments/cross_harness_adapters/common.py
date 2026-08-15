"""Subject-neutral capture helpers for the cross-harness experiment."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
from typing import Any


EXPECTED_CONTENT = b"cross-harness control\n"


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_bytes(raw: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": base64.b64encode(raw).decode("ascii"),
    }
    try:
        result["text"] = raw.decode("utf-8", errors="strict")
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
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return subprocess.CompletedProcess(argv, 124, stdout, stderr)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


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
