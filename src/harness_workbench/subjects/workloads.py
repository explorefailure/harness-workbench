"""Immutable declarations shared by the adapter and its independent verifier.

This module declares facts, never verdicts.  The producer uses each profile to
construct a request and workspace; the comparator separately recomputes the
relationships.  Sharing a ``passed`` helper here would make one defect capable
of generating and accepting the same false claim.
"""
from __future__ import annotations

from types import MappingProxyType


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
    "Run exactly `python3.11 -m unittest -v` as a standalone command both times. "
    "Do not append or chain any other command to either test invocation. Finish "
    "with done."
)

WRITE_INPUTS = (
    "run_subject.sh",
    "runner.py",
    "adapters.py",
    "oracles.py",
    "workloads.py",
    "pin.json",
    "model_selection.json",
    "task.md",
    "hook.py",
    "hermes_config.yaml",
    "dsh_patch.yml",
)
REPAIR_INPUTS = (
    "run_subject.sh",
    "runner.py",
    "adapters.py",
    "oracles.py",
    "workloads.py",
    "pin.json",
    "model_selection.json",
    "repair_task.md",
    "repair_fixture/slugger.py",
    "repair_fixture/test_slugger.py",
    "hook.py",
    "hermes_config.yaml",
    "dsh_patch.yml",
)
GUARD_INPUTS = WRITE_INPUTS + (
    "guard_extension.ts",
    "guard_hook.py",
    "guard_plugin.mjs",
)

WRITE_WORKSPACE = (
    ("hook.py", "hook.py"),
    ("task.md", "task.md"),
)
REPAIR_WORKSPACE = (
    ("hook.py", "hook.py"),
    ("repair_task.md", "repair_task.md"),
    ("repair_fixture/slugger.py", "slugger.py"),
    ("repair_fixture/test_slugger.py", "test_slugger.py"),
)


def _profile(
    *,
    prompt: str,
    inputs: tuple[str, ...],
    workspace: tuple[tuple[str, str], ...],
    effect_path: str,
) -> MappingProxyType:
    return MappingProxyType({
        "prompt": prompt,
        "inputs": inputs,
        "workspace": workspace,
        "effect_path": effect_path,
    })


WORKLOADS = MappingProxyType({
    "write": _profile(
        prompt=WRITE_PROMPT,
        inputs=WRITE_INPUTS,
        workspace=WRITE_WORKSPACE,
        effect_path="shared.txt",
    ),
    "guard": _profile(
        prompt=WRITE_PROMPT,
        inputs=GUARD_INPUTS,
        workspace=WRITE_WORKSPACE,
        effect_path="shared.txt",
    ),
    "repair": _profile(
        prompt=REPAIR_PROMPT,
        inputs=REPAIR_INPUTS,
        workspace=REPAIR_WORKSPACE,
        effect_path="slugger.py",
    ),
})

GUARD_VARIANTS = ("allow", "block")


# These are evidence declarations, not prose assembled after a run.  Keeping
# them beside the workload profiles lets the producer disclose one immutable
# fact and lets the comparator check that exact fact independently.
AMBIENT_CONFIG = MappingProxyType({
    "claude": MappingProxyType({
        "default": "safe-mode plus empty setting sources",
        "guard": "empty setting sources plus one declared settings file; NO "
                 "safe-mode (it disables the hooks under test); CLAUDE.md, "
                 "bundled skills, workflows, org and auto memory disabled by "
                 "environment; permission mode bypassPermissions so the hook "
                 "is the only control in the run",
    }),
    "codex": MappingProxyType({
        "default": "ignored user config and rules; ephemeral session",
        "guard": "per-run CODEX_HOME containing only the copied credential and "
                 "the guard's config.toml; NO --ignore-user-config (the guard "
                 "IS config.toml); ignored rules; ephemeral session; hook "
                 "trust bypassed",
    }),
    "hermes": MappingProxyType({
        "default": "temporary HERMES_HOME plus ignored rules",
        "guard": "temporary HERMES_HOME plus ignored rules; rendered config "
                 "carries the guard's pre_tool_call and on_session_start "
                 "entries beside the recording observers",
    }),
    "deepseek": MappingProxyType({
        "default": "temporary DSH_HOME plus experiment patch",
        "guard": "temporary DSH_HOME plus experiment patch carrying the guard "
                 "plugin row, verified present in the composed profile",
    }),
    "pi": MappingProxyType({
        "default": "temporary HOME and PI_CODING_AGENT_DIR; no ambient "
                   "resources, sessions, skills, or context files",
        "guard": "temporary HOME and PI_CODING_AGENT_DIR; no ambient "
                 "resources, sessions, skills, or context files; "
                 "--no-extensions followed by the one declared guard extension",
    }),
})
