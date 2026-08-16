"""Four subject adapters projected into one evidence envelope."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.request import urlopen

from common import (
    DEFAULT_EVIDENCE_LIMIT,
    DEFAULT_STDERR_LIMIT,
    DEFAULT_STDOUT_LIMIT,
    canonical_digest,
    capture_bytes,
    credential_values,
    file_digest,
    manifest,
    normalized_path,
    outcome,
    parse_jsonl,
    redact_bytes,
    repair_outcome,
    run_bounded,
)


HERE = Path(__file__).resolve().parent
WRITE_PROMPT = (
    "Create shared.txt in the current workspace. Its complete contents must be "
    "exactly the 22 ASCII bytes represented by the JSON string "
    '"cross-harness control\\n": 21 visible characters followed by one LF byte. '
    "The backslash-n denotes that single LF byte, not two literal characters. "
    "Use an available file tool, verify the total is 22 bytes, then reply done."
)
REPAIR_PROMPT = (
    "Open repair_task.md in the current workspace and follow every numbered "
    "requirement. Work only inside the current workspace. You must run the "
    "specified test command once before editing; its initial failure is expected, "
    "so proceed directly to editing slugger.py rather than retrying it unchanged. "
    "Run the same command after the edit and finish with done."
)
WRITE_INPUTS = (
    "run_subject.sh",
    "runner.py",
    "adapters.py",
    "common.py",
    "pin.json",
    "task.md",
    "hook.py",
    "hermes_config.yaml",
    "dsh_patch.yml",
)
REPAIR_INPUTS = (
    "run_subject.sh",
    "runner.py",
    "adapters.py",
    "common.py",
    "pin.json",
    "repair_task.md",
    "repair_fixture/slugger.py",
    "repair_fixture/test_slugger.py",
    "hook.py",
    "hermes_config.yaml",
    "dsh_patch.yml",
)
WORKLOADS = {
    "write": {"prompt": WRITE_PROMPT, "inputs": WRITE_INPUTS},
    "repair": {"prompt": REPAIR_PROMPT, "inputs": REPAIR_INPUTS},
}
# Compatibility aliases used by the first experiment's tests and notes.
PROMPT = WRITE_PROMPT
INPUTS = WRITE_INPUTS


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
        "native_persisted_event_log": subject == "deepseek",
        "native_terminal_event": subject in {"claude", "codex", "deepseek"},
        "correlated_tool_calls": True,
        "tool_result_status": True,
        "model_identity": (
            "local_content_digest"
            if subject in {"deepseek", "hermes"}
            else "hosted_model_label"
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
    elif subject == "deepseek":
        executable = _executable("dsh")
        expected = pins["deepseek_harness"]
        version = _command_text([str(executable), "--version"]).split()[0]
        digest_key = "executable_sha256"
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
            {"provider": expected["provider"]}
            if subject == "deepseek"
            else {}
        ),
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


def _fixture(workspace: Path, workload: str) -> None:
    shutil.copy2(HERE / "hook.py", workspace / "hook.py")
    if workload == "write":
        shutil.copy2(HERE / "task.md", workspace / "task.md")
    elif workload == "repair":
        shutil.copy2(HERE / "repair_task.md", workspace / "repair_task.md")
        shutil.copy2(HERE / "repair_fixture" / "slugger.py", workspace / "slugger.py")
        shutil.copy2(
            HERE / "repair_fixture" / "test_slugger.py",
            workspace / "test_slugger.py",
        )
    else:
        raise AdapterError(f"unknown workload: {workload}")


def _claude_command(identity: dict[str, Any], workload: str) -> list[str]:
    prompt = WORKLOADS[workload]["prompt"]
    tools = "Write" if workload == "write" else "Read,Edit,Bash"
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
        "--tools", tools,
        "--allowedTools", tools,
        "--permission-mode", "dontAsk",
        "--model", identity["model"],
        "--max-budget-usd", "0.05",
        prompt,
    ]


def _codex_command(
    identity: dict[str, Any], workspace: Path, workload: str
) -> list[str]:
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
        WORKLOADS[workload]["prompt"],
    ]


def _hermes_command(identity: dict[str, Any], workload: str) -> list[str]:
    toolsets = "file" if workload == "write" else "file,terminal"
    return [
        str(_executable("hermes")),
        "chat",
        "--query", WORKLOADS[workload]["prompt"],
        "--quiet",
        "--provider", "custom",
        "--model", identity["model"],
        "--toolsets", toolsets,
        "--ignore-rules",
        "--accept-hooks",
        "--yolo",
        "--max-turns", "6",
        "--source", "tool",
    ]


def _deepseek_command(root: Path, workload: str) -> list[str]:
    patch = root / "dsh_patch.yml"
    shutil.copy2(HERE / "dsh_patch.yml", patch)
    return [
        str(_executable("dsh")),
        "--profile", "headless",
        "--patch", str(patch),
        WORKLOADS[workload]["prompt"],
    ]


def _argument_projection(
    tool_name: Any, arguments: Any, workspace: Path
) -> tuple[dict[str, Any], str, bool]:
    name = str(tool_name or "").lower()
    values = arguments if isinstance(arguments, dict) else {}
    if name in {"bash", "terminal", "command_execution"}:
        command = str(values.get("command", ""))
        projection = {
            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest()
        }
        if "python3.11 -m unittest -v" in command:
            projection["operation"] = "python_unittest_v"
        return projection, "command", False

    raw_path = values.get("file_path", values.get("path"))
    path = normalized_path(raw_path, workspace)
    outside = path == "<outside-workspace>"
    projection: dict[str, Any] = {"path": path}
    for key in ("content", "old_string", "new_string", "patch"):
        if key in values:
            projection[f"{key}_sha256"] = hashlib.sha256(
                str(values[key]).encode("utf-8")
            ).hexdigest()
    for key in ("offset", "limit", "replace_all", "mode"):
        if key in values:
            projection[key] = values[key]
    if name in {"read", "read_file"}:
        effect_kind = "read"
    elif name in {"write", "write_file", "edit", "patch", "file_change"}:
        effect_kind = "write"
    else:
        effect_kind = "other"
    return projection, effect_kind, outside


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
        normalized, effect_kind, outside = _argument_projection(
            call.get("name"), arguments, workspace
        )
        if outside:
            errors.append(f"Claude proposed an operation outside workspace: {call_id}")
        result = results.get(call_id)
        if result is None:
            errors.append(f"Claude tool call has no result: {call_id}")
        executions.append({
            "call_id": call_id,
            "tool_name": str(call.get("name", "")).lower(),
            "effect_kind": effect_kind,
            "operation": normalized.get("operation"),
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
            effect_kind = "write"
            if any(change.get("path") == "<outside-workspace>" for change in changes):
                errors.append(f"Codex reported a change outside workspace: {item_id}")
        elif item_type == "command_execution":
            arguments, effect_kind, _ = _argument_projection(
                item_type, {"command": item.get("command")}, workspace
            )
        else:
            continue
        executions.append({
            "call_id": item_id,
            "tool_name": item_type,
            "effect_kind": effect_kind,
            "operation": arguments.get("operation"),
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
        arguments, effect_kind, outside = _argument_projection(
            event.get("tool_name"), tool_input, workspace
        )
        projected.append({
            "event": event.get("hook_event_name"),
            "tool_name": event.get("tool_name"),
            "call_id": extra.get("tool_call_id"),
            "arguments_sha256": canonical_digest(arguments),
            "effect_kind": effect_kind,
            "operation": arguments.get("operation"),
            "outside_workspace": outside,
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
        if pre["outside_workspace"] or post["outside_workspace"]:
            errors.append(
                f"Hermes proposed an operation outside the disposable workspace: {call_id}"
            )
        if pre["tool_name"] != post["tool_name"]:
            errors.append(f"Hermes hook tool names disagree: {call_id}")
        if pre["arguments_sha256"] != post["arguments_sha256"]:
            errors.append(f"Hermes hook arguments disagree: {call_id}")
        executions.append({
            "call_id": call_id,
            "tool_name": pre["tool_name"],
            "effect_kind": pre["effect_kind"],
            "operation": pre.get("operation"),
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


def _normalize_deepseek(
    raw: bytes,
    workspace: Path,
    returncode: int,
    expected_provider: str,
    expected_model: str,
) -> tuple[dict[str, Any], list[str]]:
    records, errors = parse_jsonl(raw)
    headers = [record for record in records if record.get("type") == "session"]
    events = [record for record in records if record.get("type") != "session"]
    types = [event.get("type") for event in events]
    if len(headers) != 1 or records[:1] != headers[:1]:
        errors.append("DeepSeek log does not start with exactly one session header")
    elif headers[0].get("version") != 0:
        errors.append("DeepSeek session header has an unexpected format version")
    elif normalized_path(headers[0].get("cwd"), workspace) != ".":
        errors.append("DeepSeek session header has the wrong workspace")

    for index, event in enumerate(events):
        if event.get("seq") != index:
            errors.append(f"DeepSeek event sequence is not contiguous at index {index}")
            break
    turn_starts = [index for index, kind in enumerate(types) if kind == "turn/start"]
    turn_ends = [index for index, kind in enumerate(types) if kind == "turn/end"]
    turn_window_valid = (
        len(turn_starts) == 1
        and len(turn_ends) == 1
        and turn_starts[0] < turn_ends[0]
        and turn_ends[0] == len(types) - 1
    )
    if not turn_window_valid:
        errors.append("DeepSeek log does not contain one complete ordered turn")

    contexts = [event for event in events if event.get("type") == "request/context"]
    if not contexts:
        errors.append("DeepSeek log has no provider/model context")
    for event in contexts:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if (
            data.get("provider") != expected_provider
            or data.get("model") != expected_model
        ):
            errors.append("DeepSeek provider/model context disagrees with the pin")

    calls: dict[str, tuple[int, dict[str, Any]]] = {}
    results: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, event in enumerate(events):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event.get("type") == "tool/call":
            call_id = data.get("callId")
            if not isinstance(call_id, str) or not call_id:
                errors.append("DeepSeek tool call has no id")
            elif call_id in calls:
                errors.append(f"duplicate DeepSeek tool call: {call_id}")
            else:
                calls[call_id] = (index, data)
                if turn_window_valid and not (
                    turn_starts[0] < index < turn_ends[0]
                ):
                    errors.append(f"DeepSeek tool call is outside its turn: {call_id}")
        elif event.get("type") == "tool/result":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            content = message.get("content") if isinstance(message.get("content"), list) else []
            tool_results = [
                item for item in content
                if isinstance(item, dict) and item.get("type") == "tool-result"
            ]
            call_ids = {
                item.get("toolCallId") for item in tool_results
                if isinstance(item.get("toolCallId"), str) and item.get("toolCallId")
            }
            if len(tool_results) != 1 or len(call_ids) != 1:
                errors.append("DeepSeek tool result does not identify exactly one call")
                continue
            call_id = next(iter(call_ids))
            if call_id in results:
                errors.append(f"duplicate DeepSeek tool result: {call_id}")
            else:
                results[call_id] = (index, tool_results[0])
                if not isinstance(tool_results[0].get("isError"), bool):
                    errors.append(f"DeepSeek tool result has no boolean status: {call_id}")
                if turn_window_valid and not (
                    turn_starts[0] < index < turn_ends[0]
                ):
                    errors.append(f"DeepSeek tool result is outside its turn: {call_id}")

    for call_id in results:
        if call_id not in calls:
            errors.append(f"DeepSeek tool result has no call: {call_id}")
    executions = []
    for call_id, (call_index, call) in calls.items():
        result = results.get(call_id)
        if result is None:
            errors.append(f"DeepSeek tool call has no result: {call_id}")
        elif call_index >= result[0]:
            errors.append(f"DeepSeek tool result precedes its call: {call_id}")
        raw_arguments = call.get("arguments")
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else None
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            errors.append(f"DeepSeek tool call arguments are not an object: {call_id}")
            arguments = {}
        normalized, effect_kind, outside = _argument_projection(
            call.get("name"), arguments, workspace
        )
        if outside:
            errors.append(f"DeepSeek proposed an operation outside workspace: {call_id}")
        operation_exit_code = None
        if result and effect_kind == "command":
            result_content = result[1].get("content")
            blocks = result_content if isinstance(result_content, list) else []
            texts = [
                block.get("text") for block in blocks
                if isinstance(block, dict) and isinstance(block.get("text"), str)
            ]
            if texts:
                match = re.search(r"(?:^|\n)\[exit code: (-?\d+)\]\s*$", texts[-1])
                if match:
                    operation_exit_code = int(match.group(1))
        executions.append({
            "call_id": call_id,
            "tool_name": str(call.get("name", "")).lower(),
            "effect_kind": effect_kind,
            "operation": normalized.get("operation"),
            "arguments_sha256": canonical_digest(normalized),
            "arguments_stage": "subject_event",
            "reported_error": result[1].get("isError") if result else None,
            "operation_exit_code": operation_exit_code,
            "result_stage": "subject_reported",
            "acquisition": "native_persisted_jsonl",
        })

    terminal = events[-1] if events else {}
    terminal_data = (
        terminal.get("data") if isinstance(terminal.get("data"), dict) else {}
    )
    reason = (
        terminal_data.get("reason")
        if isinstance(terminal_data.get("reason"), dict)
        else {}
    )
    terminal_kind = reason.get("kind")
    if returncode != 0 or terminal_kind != "completed":
        errors.append("DeepSeek persisted turn is not a successful terminal")
    return {
        "acquisition": "native_persisted_jsonl_plus_process",
        "completeness": "native_terminal_event",
        "event_types": types,
        "tool_executions": executions,
        "terminal": {"status": terminal_kind, "returncode": returncode},
    }, errors


def _bounded_evidence(path: Path, limit: int) -> tuple[bytes, int, bool]:
    if not path.exists():
        return b"", 0, False
    size = path.stat().st_size
    with path.open("rb") as stream:
        raw = stream.read(limit)
    return raw, size, size > limit


def _deepseek_session_log(dsh_home: Path) -> tuple[Path | None, list[str]]:
    paths = sorted((dsh_home / "sessions").glob("**/session.jsonl"))
    if len(paths) != 1:
        return None, [
            f"DeepSeek produced {len(paths)} raw top-level session candidates; expected one"
        ]
    return paths[0], []


def capture(
    subject: str,
    workload: str = "write",
    *,
    timeout: float = 120,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
) -> dict[str, Any]:
    if workload not in WORKLOADS:
        raise AdapterError(f"unknown workload: {workload}")
    if evidence_limit <= 0:
        raise AdapterError("evidence limit must be positive")
    identity = _verify_identity(subject)
    if subject in {"deepseek", "hermes"}:
        local = _verify_ollama()
        identity["model"] = local["model"]
        identity["model_digest"] = local["model_digest"]

    with tempfile.TemporaryDirectory(prefix=f".hwb-{subject}-", dir=HERE) as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        _fixture(workspace, workload)
        environment = os.environ.copy()
        environment["PWD"] = str(workspace)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        if subject == "deepseek":
            # The generic OpenAI-compatible provider requires a key-shaped value
            # even though the pinned Ollama endpoint does not authenticate it.
            # Add it before deriving the redaction set so it cannot leak into a
            # sealed record if a future dsh version starts echoing provider config.
            environment["HWB_OLLAMA_KEY"] = "ollama-local-placeholder"
        redactions = credential_values(environment)
        sensitive_environment_names = sorted(
            name for name, value in environment.items() if value in redactions
        )
        initial_test = None
        if workload == "repair":
            initial_test = run_bounded(
                ["python3.11", "-m", "unittest", "-v"],
                cwd=workspace,
                env=environment,
                timeout=30,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
        before = manifest(workspace)
        evidence_path: Path | None = None
        evidence_kind = "none"
        if subject == "claude":
            argv = _claude_command(identity, workload)
        elif subject == "codex":
            argv = _codex_command(identity, workspace, workload)
        elif subject == "hermes":
            for name in sensitive_environment_names:
                environment.pop(name, None)
            hermes_home = root / "hermes-home"
            hermes_home.mkdir()
            shutil.copy2(HERE / "hermes_config.yaml", hermes_home / "config.yaml")
            evidence_path = root / "hermes-hooks.jsonl"
            evidence_kind = "shell_hook_jsonl"
            environment["HERMES_HOME"] = str(hermes_home)
            environment["HWB_HERMES_HOOK_EVIDENCE"] = str(evidence_path)
            environment["HWB_HERMES_HOOK_MAX_BYTES"] = str(evidence_limit)
            environment["HWB_REDACT_VALUES_JSON"] = "[]"
            environment["TERMINAL_CWD"] = str(workspace)
            environment["HERMES_WRITE_SAFE_ROOT"] = str(workspace)
            argv = _hermes_command(identity, workload)
        else:
            for name in sensitive_environment_names:
                if name != "HWB_OLLAMA_KEY":
                    environment.pop(name, None)
            dsh_home = root / "dsh-home"
            dsh_home.mkdir()
            environment["HOME"] = str(dsh_home)
            environment["DSH_HOME"] = str(dsh_home)
            environment["DSH_PERMISSION_MODE"] = "workspace-write"
            environment["DSH_TELEMETRY_MODE"] = "DISABLED"
            environment["NO_COLOR"] = "1"
            for name in ("CONFIG", "CACHE", "DATA", "STATE"):
                path = dsh_home / f"xdg-{name.lower()}"
                path.mkdir()
                environment[f"XDG_{name}_HOME"] = str(path)
            evidence_kind = "native_persisted_session_jsonl"
            argv = _deepseek_command(root, workload)
        result = run_bounded(
            argv,
            cwd=workspace,
            env=environment,
            timeout=timeout,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
        )
        after = manifest(workspace)
        evidence_errors: list[str] = []
        if subject == "deepseek":
            evidence_path, evidence_errors = _deepseek_session_log(dsh_home)
        if evidence_path is None:
            evidence_raw, evidence_source_bytes, evidence_overflow = b"", 0, False
        else:
            evidence_raw, evidence_source_bytes, evidence_overflow = _bounded_evidence(
                evidence_path, evidence_limit
            )
        normalized_stdout, _ = redact_bytes(result.stdout, redactions)
        normalized_evidence, _ = redact_bytes(evidence_raw, redactions)
        if subject == "claude":
            lifecycle, errors = _normalize_claude(normalized_stdout, workspace)
        elif subject == "codex":
            lifecycle, errors = _normalize_codex(normalized_stdout, workspace)
        elif subject == "hermes":
            lifecycle, errors = _normalize_hermes(
                normalized_stdout, normalized_evidence, workspace, result.returncode
            )
        else:
            lifecycle, errors = _normalize_deepseek(
                normalized_evidence,
                workspace,
                result.returncode,
                str(identity["provider"]),
                str(identity["model"]),
            )
        errors.extend(evidence_errors)
        if result.stdout_overflow:
            errors.append("stdout capture limit exceeded")
        if result.stderr_overflow:
            errors.append("stderr capture limit exceeded")
        if evidence_overflow:
            errors.append("sidecar evidence capture limit exceeded")
        if result.returncode != 0:
            errors.append(f"{subject} exited with status {result.returncode}")
        adapter_verdict = {"passed": not errors, "errors": errors}
        if workload == "write":
            task_outcome = outcome(before, after)
            oracle_evidence = None
        else:
            final_test = run_bounded(
                ["python3.11", "-m", "unittest", "-v"],
                cwd=workspace,
                env=environment,
                timeout=30,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
            assert initial_test is not None
            task_outcome = repair_outcome(
                before,
                manifest(workspace),
                initial_test=initial_test,
                final_test=final_test,
                tool_executions=lifecycle["tool_executions"],
            )
            oracle_evidence = {
                "initial_test": {
                    "returncode": initial_test.returncode,
                    "stdout": capture_bytes(initial_test.stdout, redactions=redactions),
                    "stderr": capture_bytes(initial_test.stderr, redactions=redactions),
                },
                "final_test": {
                    "returncode": final_test.returncode,
                    "stdout": capture_bytes(final_test.stdout, redactions=redactions),
                    "stderr": capture_bytes(final_test.stderr, redactions=redactions),
                },
            }
            after = manifest(workspace)
        prompt = WORKLOADS[workload]["prompt"]
        inputs = WORKLOADS[workload]["inputs"]
        return {
            "schema": "cross-harness-adapter-run/v0.1",
            "subject": identity,
            "request": {
                "workload": workload,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "input_digests": {
                    name: file_digest(HERE / name) for name in inputs
                },
            },
            "capabilities": _capabilities(subject),
            "invocation": {
                "argv": _normalized_argv(argv, root, workspace),
                "cwd": "<workspace>",
                "timeout_seconds": timeout,
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
                    "deepseek": "temporary DSH_HOME plus experiment patch",
                }[subject],
                "network": "first-party Claude service" if subject == "claude"
                    else "first-party Codex service" if subject == "codex"
                    else "loopback Ollama only",
            },
            "capture": {
                "limits": {
                    "stdout_bytes": stdout_limit,
                    "stderr_bytes": stderr_limit,
                    "hook_evidence_bytes": evidence_limit,
                },
                "stdout": capture_bytes(
                    result.stdout,
                    redactions=redactions,
                    source_bytes=result.stdout_source_bytes,
                ),
                "stderr": capture_bytes(
                    result.stderr,
                    redactions=redactions,
                    source_bytes=result.stderr_source_bytes,
                ),
                "hook_evidence": capture_bytes(
                    evidence_raw,
                    redactions=redactions,
                    source_bytes=evidence_source_bytes,
                ),
                "sidecar_kind": evidence_kind,
                "returncode": result.returncode,
                "termination_reason": result.termination_reason,
                "timed_out": result.termination_reason == "timeout",
                "overflow": {
                    "stdout": result.stdout_overflow,
                    "stderr": result.stderr_overflow,
                    "hook_evidence": evidence_overflow,
                },
                "redacted_environment_names": sensitive_environment_names,
            },
            "lifecycle": lifecycle,
            "workspace": {"before": before, "after": after},
            "verdict": adapter_verdict,
            "outcome": task_outcome,
            "oracle_evidence": oracle_evidence,
        }
