"""Three subject adapters projected into one evidence envelope."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.request import urlopen

from common import (
    canonical_digest,
    capture_bytes,
    file_digest,
    manifest,
    normalized_path,
    outcome,
    parse_jsonl,
    run_bounded,
)


HERE = Path(__file__).resolve().parent
PROMPT = (
    "Create shared.txt in the current workspace. Its complete contents must be "
    "the 21 ASCII bytes in the following fenced block plus one final newline:\n"
    "```text\ncross-harness control\n```\n"
    "Do not include the fence or punctuation. Use an available file tool, verify "
    "the requested bytes, then reply done."
)
INPUTS = (
    "run_subject.sh",
    "runner.py",
    "adapters.py",
    "common.py",
    "pin.json",
    "task.md",
    "hook.py",
    "hermes_config.yaml",
)


class AdapterError(RuntimeError):
    pass


def _pins() -> dict[str, Any]:
    return json.loads((HERE / "pin.json").read_text(encoding="utf-8"))


def _executable(name: str) -> Path:
    found = shutil.which(name)
    if not found:
        raise AdapterError(f"{name} is not installed")
    return Path(found).resolve()


def _command_text(argv: list[str]) -> str:
    result = subprocess.run(
        argv,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AdapterError(f"identity command failed: {argv[0]}")
    return result.stdout.strip()


def _normalized_argv(argv: list[str], root: Path, workspace: Path) -> list[str]:
    replacements = (
        (str(workspace), "<workspace>"),
        (str(root), "<run-root>"),
    )
    normalized = []
    for argument in argv:
        value = argument
        for raw, replacement in replacements:
            value = value.replace(raw, replacement)
        normalized.append(value)
    return normalized


def _capabilities(subject: str) -> dict[str, Any]:
    return {
        "native_event_stream": subject in {"claude", "codex"},
        "hook_event_stream": subject == "hermes",
        "native_terminal_event": subject in {"claude", "codex"},
        "correlated_tool_calls": True,
        "tool_result_status": True,
        "model_identity": (
            "local_content_digest" if subject == "hermes" else "hosted_model_label"
        ),
    }


def _hermes_source_root() -> Path:
    configured = os.environ.get("HERMES_AGENT_ROOT")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".hermes" / "hermes-agent").resolve()
    )


def _verify_identity(subject: str) -> dict[str, Any]:
    pins = _pins()
    if subject == "claude":
        executable = _executable("claude")
        expected = pins["claude_code"]
        version = _command_text([str(executable), "--version"]).split()[0]
        digest_key = "executable_sha256"
    elif subject == "codex":
        executable = _executable("codex")
        expected = pins["codex_cli"]
        version = _command_text([str(executable), "--version"]).split()[-1]
        digest_key = "executable_sha256"
    elif subject == "hermes":
        executable = _executable("hermes")
        expected = pins["hermes_agent"]
        version_text = _command_text([str(executable), "--version"])
        version = version_text.split()[2].removeprefix("v")
        digest_key = "launcher_sha256"
        source_root = _hermes_source_root()
        commit = _command_text(["git", "-C", str(source_root), "rev-parse", "HEAD"])
        if commit != expected["source_commit"]:
            raise AdapterError("Hermes source commit does not match pin.json")
    else:
        raise AdapterError(f"unknown subject: {subject}")
    if version != expected["version"]:
        raise AdapterError(f"{subject} version {version} does not match pin.json")
    digest = file_digest(executable)
    if digest != expected[digest_key]:
        raise AdapterError(f"{subject} executable digest does not match pin.json")
    return {
        "name": subject,
        "version": version,
        "executable_sha256": digest,
        "model": expected.get("model"),
        **(
            {"source_commit": expected["source_commit"]}
            if subject == "hermes"
            else {}
        ),
    }


def _verify_ollama() -> dict[str, str]:
    expected = _pins()["ollama"]
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
        payload = json.load(response)
    models = {item.get("name"): item for item in payload.get("models", [])}
    observed = models.get(expected["model"], {}).get("digest")
    if observed != expected["model_digest"]:
        raise AdapterError("Ollama model digest does not match pin.json")
    return {"model": expected["model"], "model_digest": observed}


def _fixture(workspace: Path) -> None:
    shutil.copy2(HERE / "task.md", workspace / "task.md")
    shutil.copy2(HERE / "hook.py", workspace / "hook.py")


def _claude_command(identity: dict[str, Any]) -> list[str]:
    return [
        str(_executable("claude")),
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--safe-mode",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--tools", "Write",
        "--allowedTools", "Write",
        "--permission-mode", "dontAsk",
        "--model", identity["model"],
        "--max-budget-usd", "0.05",
        PROMPT,
    ]


def _codex_command(identity: dict[str, Any], workspace: Path) -> list[str]:
    return [
        str(_executable("codex")),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "--model", identity["model"],
        "--cd", str(workspace),
        PROMPT,
    ]


def _hermes_command(identity: dict[str, Any]) -> list[str]:
    return [
        str(_executable("hermes")),
        "chat",
        "--query", PROMPT,
        "--quiet",
        "--provider", "custom",
        "--model", identity["model"],
        "--toolsets", "file",
        "--ignore-rules",
        "--accept-hooks",
        "--yolo",
        "--max-turns", "6",
        "--source", "tool",
    ]


def _normalize_claude(raw: bytes, workspace: Path) -> tuple[dict[str, Any], list[str]]:
    events, errors = parse_jsonl(raw)
    types = [event.get("type") for event in events]
    init_events = [
        event
        for event in events
        if event.get("type") == "system" and event.get("subtype") == "init"
    ]
    if types[:1] != ["system"] or len(init_events) != 1 or events[0] not in init_events:
        errors.append("Claude stream does not start with system init")
    if types[-1:] != ["result"] or types.count("result") != 1:
        errors.append("Claude stream does not end with result")
    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    for event in events:
        message = event.get("message", {})
        for content in message.get("content", []) if isinstance(message, dict) else []:
            if not isinstance(content, dict):
                continue
            if content.get("type") == "tool_use":
                call_id = content.get("id")
                if not isinstance(call_id, str) or not call_id:
                    errors.append("Claude tool call has no id")
                elif call_id in calls:
                    errors.append(f"duplicate Claude tool call: {call_id}")
                else:
                    calls[call_id] = content
            elif content.get("type") == "tool_result":
                call_id = content.get("tool_use_id")
                if not isinstance(call_id, str) or not call_id:
                    errors.append("Claude tool result has no call id")
                elif call_id in results:
                    errors.append(f"duplicate Claude tool result: {call_id}")
                else:
                    results[call_id] = {
                        "is_error": bool(content.get("is_error", False)),
                        "content": content.get("content"),
                    }
    for call_id in results:
        if call_id not in calls:
            errors.append(f"Claude tool result has no call: {call_id}")
    executions = []
    for call_id, call in calls.items():
        arguments = call.get("input", {})
        normalized = {
            "path": normalized_path(arguments.get("file_path"), workspace),
            "content_sha256": hashlib.sha256(
                str(arguments.get("content", "")).encode("utf-8")
            ).hexdigest(),
        }
        result = results.get(call_id)
        if result is None:
            errors.append(f"Claude tool call has no result: {call_id}")
        executions.append({
            "call_id": call_id,
            "tool_name": str(call.get("name", "")).lower(),
            "arguments_sha256": canonical_digest(normalized),
            "arguments_stage": "subject_proposal",
            "reported_error": result.get("is_error") if result else None,
            "result_stage": "subject_reported",
            "acquisition": "native_jsonl",
        })
    terminal = events[-1] if events else {}
    if terminal.get("is_error") is not False or terminal.get("subtype") != "success":
        errors.append("Claude terminal result is not successful")
    return {
        "acquisition": "native_jsonl",
        "completeness": "native_terminal_event",
        "event_types": types,
        "tool_executions": executions,
        "terminal": {
            "status": terminal.get("subtype"),
            "is_error": terminal.get("is_error"),
        },
    }, errors


def _normalize_codex(raw: bytes, workspace: Path) -> tuple[dict[str, Any], list[str]]:
    events, errors = parse_jsonl(raw)
    types = [event.get("type") for event in events]
    if (
        types[:2] != ["thread.started", "turn.started"]
        or types.count("thread.started") != 1
        or types.count("turn.started") != 1
    ):
        errors.append("Codex stream does not start with thread and turn")
    if types[-1:] != ["turn.completed"] or types.count("turn.completed") != 1:
        errors.append("Codex stream does not end with turn.completed")
    started = {}
    completed = {}
    for event in events:
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        if event.get("type") == "item.started":
            if item["id"] in started:
                errors.append(f"duplicate Codex item start: {item['id']}")
            else:
                started[item["id"]] = item
        elif event.get("type") == "item.completed":
            if item["id"] in completed:
                errors.append(f"duplicate Codex item completion: {item['id']}")
            else:
                completed[item["id"]] = item
    for item_id in started:
        if item_id not in completed:
            errors.append(f"Codex item did not complete: {item_id}")
    executions = []
    for item_id, item in completed.items():
        item_type = item.get("type")
        if item_type in {"file_change", "command_execution"}:
            start = started.get(item_id)
            if start is None:
                errors.append(f"Codex tool completion has no start: {item_id}")
            elif start.get("type") != item_type:
                errors.append(f"Codex item type changed before completion: {item_id}")
        if item_type == "file_change":
            changes = [{
                "path": normalized_path(change.get("path"), workspace),
                "kind": change.get("kind"),
            } for change in item.get("changes", []) if isinstance(change, dict)]
            arguments = {"changes": changes}
        elif item_type == "command_execution":
            arguments = {"command": item.get("command")}
        else:
            continue
        executions.append({
            "call_id": item_id,
            "tool_name": item_type,
            "arguments_sha256": canonical_digest(arguments),
            "arguments_stage": "subject_event",
            "reported_error": item.get("status") != "completed"
                or (item.get("exit_code") not in (None, 0)),
            "result_stage": "subject_reported",
            "acquisition": "native_jsonl",
        })
    return {
        "acquisition": "native_jsonl",
        "completeness": "native_terminal_event",
        "event_types": types,
        "tool_executions": executions,
        "terminal": {"status": events[-1].get("type") if events else None},
    }, errors


def _normalize_hermes(
    raw: bytes,
    hook_raw: bytes,
    workspace: Path,
    returncode: int,
) -> tuple[dict[str, Any], list[str]]:
    hook_events, errors = parse_jsonl(hook_raw)
    projected = []
    for event in hook_events:
        extra = event.get("extra") if isinstance(event.get("extra"), dict) else {}
        tool_input = event.get("tool_input") or {}
        path = normalized_path(tool_input.get("path"), workspace)
        if path == "<outside-workspace>":
            errors.append("Hermes proposed a write outside the disposable workspace")
        projected.append({
            "event": event.get("hook_event_name"),
            "tool_name": event.get("tool_name"),
            "call_id": extra.get("tool_call_id"),
            "arguments_sha256": canonical_digest({
                "path": path,
                "content_sha256": hashlib.sha256(
                    str(tool_input.get("content", "")).encode(
                        "utf-8"
                    )
                ).hexdigest(),
            }),
            "status": extra.get("status"),
            "acquisition": "shell_hook",
        })
    calls: dict[str, dict[str, dict[str, Any]]] = {}
    for index, item in enumerate(projected):
        call_id = item.get("call_id")
        event_name = item.get("event")
        if not isinstance(call_id, str) or not call_id:
            errors.append("Hermes hook event has no tool call id")
            continue
        if event_name not in {"pre_tool_call", "post_tool_call"}:
            errors.append(f"unexpected Hermes hook event: {event_name}")
            continue
        pair = calls.setdefault(call_id, {})
        if event_name in pair:
            errors.append(f"duplicate Hermes {event_name}: {call_id}")
        pair[event_name] = {**item, "index": index}
    if not calls:
        errors.append("Hermes emitted no write hook evidence")
    try:
        final_text = raw.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        final_text = ""
        errors.append(f"Hermes stdout is not UTF-8: {error}")
    if returncode != 0 or not final_text:
        errors.append("Hermes process boundary is not a successful terminal")
    executions = []
    for call_id, pair in calls.items():
        pre = pair.get("pre_tool_call")
        post = pair.get("post_tool_call")
        if pre is None or post is None:
            errors.append(f"Hermes hook pair is incomplete: {call_id}")
            continue
        if pre["index"] >= post["index"]:
            errors.append(f"Hermes hook pair is out of order: {call_id}")
        if pre["tool_name"] != post["tool_name"]:
            errors.append(f"Hermes hook tool names disagree: {call_id}")
        if pre["arguments_sha256"] != post["arguments_sha256"]:
            errors.append(f"Hermes hook arguments disagree: {call_id}")
        executions.append({
            "call_id": call_id,
            "tool_name": pre["tool_name"],
            "arguments_sha256": pre["arguments_sha256"],
            "arguments_stage": "subject_proposal",
            "reported_error": (
                None if post.get("status") is None else post.get("status") != "ok"
            ),
            "result_stage": "hook_observer",
            "acquisition": "shell_hook",
        })
    return {
        "acquisition": "shell_hook_plus_process",
        "completeness": "process_boundary_only",
        "event_types": [item["event"] for item in projected],
        "tool_executions": executions,
        "terminal": {"status": "process_exit", "returncode": returncode},
    }, errors


def capture(subject: str) -> dict[str, Any]:
    identity = _verify_identity(subject)
    if subject == "hermes":
        local = _verify_ollama()
        identity["model"] = local["model"]
        identity["model_digest"] = local["model_digest"]

    with tempfile.TemporaryDirectory(prefix=f".hwb-{subject}-", dir=HERE) as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        _fixture(workspace)
        before = manifest(workspace)
        environment = os.environ.copy()
        environment["PWD"] = str(workspace)
        hook_path = root / "hermes-hooks.jsonl"
        if subject == "claude":
            argv = _claude_command(identity)
        elif subject == "codex":
            argv = _codex_command(identity, workspace)
        else:
            hermes_home = root / "hermes-home"
            hermes_home.mkdir()
            shutil.copy2(HERE / "hermes_config.yaml", hermes_home / "config.yaml")
            environment["HERMES_HOME"] = str(hermes_home)
            environment["HWB_HERMES_HOOK_EVIDENCE"] = str(hook_path)
            environment["TERMINAL_CWD"] = str(workspace)
            environment["HERMES_WRITE_SAFE_ROOT"] = str(workspace)
            argv = _hermes_command(identity)
        result = run_bounded(argv, cwd=workspace, env=environment)
        after = manifest(workspace)
        hook_raw = hook_path.read_bytes() if hook_path.exists() else b""
        if subject == "claude":
            lifecycle, errors = _normalize_claude(result.stdout, workspace)
        elif subject == "codex":
            lifecycle, errors = _normalize_codex(result.stdout, workspace)
        else:
            lifecycle, errors = _normalize_hermes(
                result.stdout, hook_raw, workspace, result.returncode
            )
        if result.returncode != 0:
            errors.append(f"{subject} exited with status {result.returncode}")
        adapter_verdict = {"passed": not errors, "errors": errors}
        task_outcome = outcome(before, after)
        return {
            "schema": "cross-harness-adapter-run/v0.1",
            "subject": identity,
            "request": {
                "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
                "input_digests": {
                    name: file_digest(HERE / name) for name in INPUTS
                },
            },
            "capabilities": _capabilities(subject),
            "invocation": {
                "argv": _normalized_argv(argv, root, workspace),
                "cwd": "<workspace>",
                "timeout_seconds": 120,
                "credential_source": (
                    "ambient_authenticated_client"
                    if subject in {"claude", "codex"}
                    else "none_loopback_model"
                ),
            },
            "isolation": {
                "disposable_workspace": True,
                "ambient_config": {
                    "claude": "safe-mode plus empty setting sources",
                    "codex": "ignored user config and rules; ephemeral session",
                    "hermes": "temporary HERMES_HOME plus ignored rules",
                }[subject],
                "network": "first-party Claude service" if subject == "claude"
                    else "first-party Codex service" if subject == "codex"
                    else "loopback Ollama only",
            },
            "capture": {
                "stdout": capture_bytes(result.stdout),
                "stderr": capture_bytes(result.stderr),
                "hook_evidence": capture_bytes(hook_raw),
                "returncode": result.returncode,
                "timed_out": result.returncode == 124,
            },
            "lifecycle": lifecycle,
            "workspace": {"before": before, "after": after},
            "verdict": adapter_verdict,
            "outcome": task_outcome,
        }
