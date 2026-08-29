#!/usr/bin/env python3
"""Five declarative provider routes with an injectable zero-network transport."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any, Protocol

from agent_task_schema import SUBJECTS


HERE = Path(__file__).resolve().parent
FAKE_PROVIDER = HERE / "agent_task_fake_provider.py"


class ProviderTransport(Protocol):
    def command(
        self, *, subject: str, workspace: Path, prompt: str, plan: Path
    ) -> list[str]:
        """Return one provider argv without invoking it."""


@dataclass(frozen=True)
class FakeProviderTransport:
    """Deterministic command transport used by offline and fault tests."""

    python: Path = Path(sys.executable)

    def command(
        self, *, subject: str, workspace: Path, prompt: str, plan: Path
    ) -> list[str]:
        if subject not in SUBJECTS:
            raise ValueError(f"unknown provider route: {subject}")
        return [
            str(self.python), str(FAKE_PROVIDER), "--subject", subject,
            "--workspace", str(workspace), "--plan", str(plan.resolve(strict=True)),
        ]


@dataclass(frozen=True)
class RealProviderPlanTransport:
    """Produce inspectable real-route templates; never release a provider."""

    def command(
        self, *, subject: str, workspace: Path, prompt: str, plan: Path
    ) -> list[str]:
        raise RuntimeError(
            "real provider commands are plan-only until a separate one-attempt "
            "authorization artifact is validated"
        )

    def routes(self, prompt: str) -> dict[str, dict[str, Any]]:
        pins = json.loads((HERE / "pin.json").read_text(encoding="utf-8"))
        selection = json.loads(
            (HERE / "model_selection.json").read_text(encoding="utf-8")
        )
        profile = selection["profiles"][selection["active"]]
        models = {
            "claude": pins["claude_code"]["model"],
            "codex": pins["codex_cli"]["model"],
            **profile["models"],
        }
        templates = {
            "claude": [
                "claude", "-p", "--output-format", "stream-json", "--verbose",
                "--no-session-persistence", "--setting-sources", "",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--disable-slash-commands", "--tools", "Read,Edit,Bash",
                "--allowedTools", "Read,Edit,Bash", "--model", models["claude"],
                "--max-budget-usd", "0.05", "--safe-mode", "--permission-mode",
                "dontAsk", prompt,
            ],
            "codex": [
                "codex", "exec", "--ignore-user-config", "--json", "--ephemeral",
                "--ignore-rules", "--skip-git-repo-check", "--sandbox",
                "workspace-write", "--model", models["codex"], "--cd",
                "{agent_workspace}", prompt,
            ],
            "deepseek": [
                "dsh", "--profile", "headless", "--patch",
                "{retained_declarative_dsh_patch}", prompt,
            ],
            "hermes": [
                "hermes", "chat", "--query", prompt, "--quiet", "--provider",
                "custom", "--model", models["hermes"], "--toolsets",
                "file,terminal", "--ignore-rules", "--accept-hooks", "--yolo",
                "--max-turns", "6", "--source", "tool",
            ],
            "pi": [
                "pi", "--mode", "json", "--print", "--no-session",
                "--no-extensions", "--no-skills", "--no-prompt-templates",
                "--no-context-files", "--no-approve", "--tools", "read,edit,bash",
                "--provider", "workbench-gateway", "--model", models["pi"],
                "@{retained_prompt_file}",
            ],
        }
        return {
            subject: {
                "subject": subject,
                "model": models[subject],
                "argv_template": templates[subject],
                "transport": "real_provider_plan_only",
                "release_enabled": False,
                "separate_authorization_artifact_required": True,
            }
            for subject in SUBJECTS
        }
