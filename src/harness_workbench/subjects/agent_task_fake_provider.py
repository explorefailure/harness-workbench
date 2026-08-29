#!/usr/bin/env python3
"""Deterministic no-network provider used by all five offline routes."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path, PurePosixPath

from agent_task_schema import SUBJECTS, require_relative_path


EVENTS = {
    "claude": ("system.init", "assistant.tool_use", "user.tool_result", "result"),
    "codex": ("thread.started", "item.started", "item.completed", "turn.completed"),
    "deepseek": ("run.start", "tool.call", "tool.result", "turn.end"),
    "hermes": ("hook.pre_tool", "hook.post_tool", "process.exit"),
    "pi": ("session.start", "tool_execution_start", "tool_execution_end", "agent_settled"),
}


def _target(root: Path, value: str) -> Path:
    require_relative_path(value, "fake provider operation path")
    path = root.joinpath(*PurePosixPath(value).parts)
    resolved_root = root.resolve()
    resolved_parent = path.parent.resolve()
    try:
        resolved_parent.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"fake provider path escapes workspace: {value}") from error
    return path


def apply_plan(root: Path, plan: dict) -> None:
    for row in plan["operations"]:
        target = _target(root, row["path"])
        if row["op"] == "delete":
            target.rmdir() if row["kind"] == "directory" else target.unlink()
            continue
        if row["kind"] == "directory":
            if row["op"] == "create":
                target.mkdir(parents=True, exist_ok=False)
            os.chmod(target, row["mode"])
            continue
        data = base64.b64decode(row["content_base64"], validate=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if row["op"] == "create":
            flags |= os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags, row["mode"])
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(target, row["mode"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", choices=SUBJECTS, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    apply_plan(args.workspace.resolve(strict=True), plan)
    for index, event in enumerate(EVENTS[args.subject]):
        print(json.dumps({
            "schema": "agent-task-fake-provider-event/v0.1",
            "subject": args.subject,
            "sequence": index,
            "event": event,
            "call_id": "offline-call-0",
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
