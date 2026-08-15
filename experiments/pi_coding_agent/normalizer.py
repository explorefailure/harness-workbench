"""Strict, additive normalization for Pi's JSON event stream."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any


SCHEMA = "pi-hwb-summary/v0.1"
SESSION_VERSION = 3
KNOWN_EVENT_TYPES = {
    "session",
    "agent_start",
    "agent_end",
    "agent_settled",
    "turn_start",
    "turn_end",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "queue_update",
    "compaction_start",
    "compaction_end",
    "auto_compaction_start",
    "auto_compaction_end",
    "auto_retry_start",
    "auto_retry_end",
    "extension_error",
}
AUXILIARY_PAIRS = {
    "compaction_start": "compaction_end",
    "auto_compaction_start": "auto_compaction_end",
    "auto_retry_start": "auto_retry_end",
}
AUXILIARY_ENDS = {end: start for start, end in AUXILIARY_PAIRS.items()}


class StreamError(ValueError):
    """The captured stdout is not a valid Pi JSON event stream."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number {value} is not JSON")


def canonical_digest(value: Any) -> str:
    body = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def parse_jsonl(raw: str) -> list[dict[str, Any]]:
    if not raw:
        raise StreamError("Pi stdout was empty")

    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise StreamError(f"blank JSONL record at line {line_number}")
        try:
            event = json.loads(line, parse_constant=_reject_constant)
        except ValueError as error:
            raise StreamError(f"invalid JSON at line {line_number}: {error}") from error
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise StreamError(
                f"line {line_number} must be an object with a string 'type'"
            )
        events.append(event)
    return events


def _message_identity(message: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(message, dict) or not isinstance(message.get("role"), str):
        return None, None, None
    role = message["role"]
    call_id = message.get("toolCallId") if role == "toolResult" else None
    tool_name = message.get("toolName") if role == "toolResult" else None
    return role, call_id, tool_name


def _target_path(tool_name: str, arguments: dict[str, Any]) -> str | None:
    if tool_name in {"write", "edit", "read"}:
        path = arguments.get("path")
        return path if isinstance(path, str) else None
    return None


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    if not events:
        errors.append("event stream is empty")
        return _summary(events, errors, [], [], [], [])

    if events[0].get("type") != "session":
        errors.append("first event is not the Pi session header")
    elif events[0].get("version") != SESSION_VERSION:
        errors.append(
            f"Pi session version is {events[0].get('version')!r}; "
            f"expected {SESSION_VERSION}"
        )
    if sum(event.get("type") == "session" for event in events) != 1:
        errors.append("event stream must contain exactly one session header")

    counts = Counter(event["type"] for event in events)
    agent_active = False
    agent_cycles = 0
    agent_ends = 0
    turn_active = False
    message_identity: tuple[str | None, str | None, str | None] | None = None
    open_tools: dict[str, dict[str, Any]] = {}
    seen_tools: set[str] = set()
    open_auxiliary: set[str] = set()
    continuation_ready = False
    settled_count = 0
    assistant_stop_reasons: list[str | None] = []
    assistant_tool_calls: list[dict[str, Any]] = []
    tool_executions: list[dict[str, Any]] = []

    for index, event in enumerate(events):
        event_type = event["type"]
        where = f"event {index + 1} ({event_type})"

        if index and event_type == "session":
            errors.append(f"{where}: session header is only valid first")
        elif event_type == "agent_start":
            if agent_active or turn_active or message_identity or open_tools:
                errors.append(f"{where}: agent_start overlaps active lifecycle state")
            if agent_cycles and not continuation_ready:
                errors.append(
                    f"{where}: additional agent cycle has no completed retry/compaction"
                )
            agent_active = True
            agent_cycles += 1
            continuation_ready = False
        elif event_type == "agent_end":
            if not agent_active:
                errors.append(f"{where}: agent_end has no active agent")
            if turn_active or message_identity or open_tools:
                errors.append(f"{where}: agent_end precedes nested lifecycle closure")
            agent_active = False
            agent_ends += 1
        elif event_type == "agent_settled":
            settled_count += 1
            if agent_active or turn_active or message_identity or open_tools:
                errors.append(f"{where}: agent_settled precedes lifecycle closure")
            if open_auxiliary:
                errors.append(f"{where}: agent_settled precedes retry/compaction closure")
            if index != len(events) - 1:
                errors.append(f"{where}: agent_settled is not the final event")
        elif event_type == "turn_start":
            if not agent_active or turn_active or message_identity or open_tools:
                errors.append(f"{where}: turn_start is outside a clear active agent")
            turn_active = True
        elif event_type == "turn_end":
            if not turn_active:
                errors.append(f"{where}: turn_end has no active turn")
            if message_identity or open_tools:
                errors.append(f"{where}: turn_end precedes nested lifecycle closure")
            turn_active = False
        elif event_type == "message_start":
            identity = _message_identity(event.get("message"))
            if not turn_active or message_identity is not None:
                errors.append(f"{where}: message_start is outside a clear active turn")
            if identity[0] not in {"user", "assistant", "toolResult"}:
                errors.append(f"{where}: message_start has an invalid message role")
            if identity[0] == "toolResult" and (
                not isinstance(identity[1], str) or not isinstance(identity[2], str)
            ):
                errors.append(f"{where}: toolResult identity is incomplete")
            message_identity = identity
        elif event_type == "message_update":
            update = event.get("assistantMessageEvent")
            if message_identity is None or message_identity[0] != "assistant":
                errors.append(f"{where}: message_update has no active assistant message")
            if not isinstance(update, dict) or not isinstance(update.get("type"), str):
                errors.append(f"{where}: assistantMessageEvent is malformed")
        elif event_type == "message_end":
            identity = _message_identity(event.get("message"))
            if message_identity is None:
                errors.append(f"{where}: message_end has no matching message_start")
            elif identity != message_identity:
                errors.append(f"{where}: message identity changed before message_end")
            message = event.get("message")
            if isinstance(message, dict) and identity[0] == "assistant":
                stop_reason = message.get("stopReason")
                if not isinstance(stop_reason, str):
                    errors.append(f"{where}: assistant stopReason is not a string")
                assistant_stop_reasons.append(stop_reason)
                content = message.get("content")
                if not isinstance(content, list):
                    errors.append(f"{where}: assistant content is not a list")
                    content = []
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "toolCall":
                        continue
                    call_id = block.get("id")
                    name = block.get("name")
                    arguments = block.get("arguments")
                    if (
                        not isinstance(call_id, str)
                        or not isinstance(name, str)
                        or not isinstance(arguments, dict)
                    ):
                        errors.append(f"{where}: assistant toolCall is malformed")
                        continue
                    assistant_tool_calls.append(
                        {
                            "tool_call_id": call_id,
                            "tool_name": name,
                            "target_path": _target_path(name, arguments),
                            "arguments_sha256": canonical_digest(arguments),
                        }
                    )
            message_identity = None
        elif event_type == "tool_execution_start":
            call_id = event.get("toolCallId")
            tool_name = event.get("toolName")
            arguments = event.get("args")
            if not turn_active or message_identity is not None:
                errors.append(f"{where}: tool execution starts outside a clear turn")
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(tool_name, str)
                or not tool_name
                or not isinstance(arguments, dict)
            ):
                errors.append(f"{where}: tool_execution_start fields are malformed")
            elif call_id in seen_tools or call_id in open_tools:
                errors.append(f"{where}: duplicate tool execution ID {call_id!r}")
            else:
                open_tools[call_id] = event
                seen_tools.add(call_id)
        elif event_type == "tool_execution_update":
            call_id = event.get("toolCallId")
            if not isinstance(call_id, str) or call_id not in open_tools:
                errors.append(f"{where}: tool update has no matching start")
        elif event_type == "tool_execution_end":
            call_id = event.get("toolCallId")
            start = open_tools.pop(call_id, None) if isinstance(call_id, str) else None
            if start is None:
                errors.append(f"{where}: tool_execution_end has no matching start")
            else:
                tool_name = event.get("toolName")
                arguments = start["args"]
                if tool_name != start.get("toolName"):
                    errors.append(f"{where}: tool name changed during execution")
                if not isinstance(event.get("isError"), bool):
                    errors.append(f"{where}: tool isError is not boolean")
                tool_executions.append(
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "target_path": _target_path(start["toolName"], arguments),
                        "arguments_sha256": canonical_digest(arguments),
                        "arguments_stage": "pre_tool_call_hook",
                        "result_sha256": canonical_digest(event.get("result")),
                        "result_stage": "post_tool_result_hook",
                        "is_error": event.get("isError"),
                    }
                )
        elif event_type in AUXILIARY_PAIRS:
            if event_type in open_auxiliary:
                errors.append(f"{where}: duplicate retry/compaction start")
            open_auxiliary.add(event_type)
        elif event_type in AUXILIARY_ENDS:
            start_type = AUXILIARY_ENDS[event_type]
            if start_type not in open_auxiliary:
                errors.append(f"{where}: retry/compaction end has no matching start")
            else:
                open_auxiliary.remove(start_type)
                continuation_ready = True
        elif event_type == "extension_error":
            errors.append(f"{where}: Pi reported extension_error")

    if agent_cycles < 1 or agent_ends != agent_cycles:
        errors.append(
            f"agent lifecycle is unbalanced: {agent_cycles} start(s), {agent_ends} end(s)"
        )
    if turn_active:
        errors.append("event stream ended with an active turn")
    if message_identity is not None:
        errors.append("event stream ended with an active message")
    for call_id in sorted(open_tools):
        errors.append(f"tool_execution_start has no matching end: {call_id}")
    for start_type in sorted(open_auxiliary):
        errors.append(f"{start_type} has no matching end")
    if settled_count != 1:
        errors.append(f"expected exactly one agent_settled, saw {settled_count}")
    if not assistant_stop_reasons or assistant_stop_reasons[-1] != "stop":
        errors.append("terminal assistant message did not stop normally")
    if assistant_stop_reasons.count("stop") != 1:
        errors.append(
            "expected exactly one normally stopped assistant message, "
            f"saw {assistant_stop_reasons.count('stop')}"
        )

    assistant_by_id = {item["tool_call_id"]: item for item in assistant_tool_calls}
    if len(assistant_by_id) != len(assistant_tool_calls):
        errors.append("assistant emitted duplicate tool-call IDs")
    for execution in tool_executions:
        source = assistant_by_id.get(execution["tool_call_id"])
        if source is None:
            errors.append(
                f"tool execution {execution['tool_call_id']!r} has no assistant tool call"
            )
        elif any(
            source[key] != execution[key]
            for key in ("tool_name", "target_path", "arguments_sha256")
        ):
            errors.append(
                f"tool execution {execution['tool_call_id']!r} disagrees with assistant tool call"
            )

    unknown = sorted(set(counts) - KNOWN_EVENT_TYPES)
    return _summary(
        events,
        errors,
        tool_executions,
        assistant_tool_calls,
        assistant_stop_reasons,
        unknown,
    )


def _summary(
    events: list[dict[str, Any]],
    errors: list[str],
    tool_executions: list[dict[str, Any]],
    assistant_tool_calls: list[dict[str, Any]],
    assistant_stop_reasons: list[str | None],
    unknown: list[str],
) -> dict[str, Any]:
    counts = Counter(event["type"] for event in events)
    header = events[0] if events and events[0].get("type") == "session" else {}
    projection = {
        "schema": "pi-hwb-event-projection/v0.1",
        "event_types": dict(sorted(counts.items())),
        "session_version": header.get("version"),
        "assistant_stop_reasons": assistant_stop_reasons,
        "assistant_tool_calls": assistant_tool_calls,
        "tool_executions": tool_executions,
        "retry_compaction_counts": {
            event_type: counts[event_type]
            for event_type in sorted(AUXILIARY_PAIRS | AUXILIARY_ENDS)
            if counts[event_type]
        },
        "unknown_event_types": unknown,
    }
    return {
        "schema": SCHEMA,
        "valid": not errors,
        "errors": errors,
        "event_count": len(events),
        "projection": projection,
    }


def normalize_jsonl(raw: str) -> dict[str, Any]:
    return summarize(parse_jsonl(raw))
