#!/usr/bin/env python3
"""Strict fake-route lifecycle projection used by producer and replay validator."""
from __future__ import annotations

import json
from typing import Any

from agent_task_fake_provider import EVENTS
from agent_task_schema import ContractError, SUBJECTS


def normalize_fake_route(subject: str, raw: bytes) -> dict[str, Any]:
    if subject not in SUBJECTS:
        raise ContractError(f"unknown fake provider route: {subject}")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractError(f"{subject} fake route is not UTF-8") from error
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(f"{subject} fake event {index} is invalid JSON") from error
        if type(row) is not dict:
            raise ContractError(f"{subject} fake event {index} is not an object")
        expected = {
            "schema": "agent-task-fake-provider-event/v0.1",
            "subject": subject,
            "sequence": index,
            "event": EVENTS[subject][index] if index < len(EVENTS[subject]) else None,
            "call_id": "offline-call-0",
        }
        if row != expected:
            raise ContractError(f"{subject} fake lifecycle diverges at event {index}")
        rows.append(row)
    if len(rows) != len(EVENTS[subject]):
        raise ContractError(f"{subject} fake lifecycle is incomplete")
    return {
        "acquisition": "offline_fake_route",
        "events": [row["event"] for row in rows],
        "terminal": rows[-1]["event"],
        "tool_attempts": 1,
    }
