"""Four subject adapters projected into one evidence envelope."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
from typing import Any
from urllib.request import urlopen

import harness_workbench
from harness_workbench import canon as canon_module
from harness_workbench import capture as capture_module
from harness_workbench.canon import digest_obj
from harness_workbench.capture import (
    DEFAULT_SIDECAR_LIMIT,
    DEFAULT_STDERR_LIMIT,
    DEFAULT_STDOUT_LIMIT,
    _bounded_evidence,
    capture_bytes,
    capture_file,
    credential_values,
    digest_file,
    manifest,
    parse_jsonl,
    redact_bytes,
    relative_to_root,
    run_bounded,
)

from oracles import guard_outcome, outcome, repair_outcome
from workloads import (
    AMBIENT_CONFIG,
    GUARD_INPUTS,
    GUARD_VARIANTS,
    REPAIR_INPUTS,
    REPAIR_PROMPT,
    WORKLOADS,
    WRITE_INPUTS,
    WRITE_PROMPT,
)


HERE = Path(__file__).resolve().parent


def parse_jsonl_objects(raw: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Every subject here emits JSON *objects* per line; a bare scalar is a fault.

    The primitive defaults to permissive because a format fact is not a contract
    violation in general. For these five it is: each normalizer indexes the
    record by key on the next line, so a non-object would surface as a
    TypeError somewhere downstream instead of as the evidence complaint it is.
    Binding the flag once here keeps that decision in the tree that holds it.
    """
    return parse_jsonl(raw, objects_only=True)


# Compatibility aliases used by the first experiment's tests and notes.
PROMPT = WRITE_PROMPT
INPUTS = WRITE_INPUTS


class AdapterError(RuntimeError):
    pass


def _bounded_measurement_errors(
    result: Any,
    *,
    label: str,
    require_zero_exit: bool,
) -> list[str]:
    """Classify process measurement faults without judging task semantics."""
    errors: list[str] = []
    if result.stdout_overflow:
        errors.append(f"{label} stdout capture limit exceeded")
    if result.stderr_overflow:
        errors.append(f"{label} stderr capture limit exceeded")
    if result.termination_reason is not None:
        errors.append(f"{label} bound fired: {result.termination_reason}")
    if require_zero_exit and result.returncode != 0:
        errors.append(f"{label} exited with status {result.returncode}")
    if result.group_alive_after_cleanup:
        errors.append(f"{label} left a live process group after cleanup")
    return errors


# Subjects whose model is chosen by model_selection.json rather than by the
# vendor's own account. Claude and Codex authenticate to their first-party
# services, so their model stays a pin, not a profile choice.
CONFIGURABLE_MODEL_SUBJECTS = frozenset({"deepseek", "hermes", "pi"})


def _pins() -> dict[str, Any]:
    return json.loads((HERE / "pin.json").read_text(encoding="utf-8"))


def _executable(name: str) -> Path:
    found = shutil.which(name)
    if not found:
        raise AdapterError(f"{name} is not installed")
    return Path(found).resolve()


def _command_text(argv: list[str]) -> str:
    """Read one short line from a pinned executable, under the same bounds.

    This was `subprocess.run(..., timeout=15)`, which looks harmless for a
    version probe and is not: its timeout kills the process and not the group,
    so a launcher that forks leaks exactly the orphan this adapter later
    reports as clean. Three of the five probes here run launchers -- `claude`,
    `codex` and `hermes` are wrappers -- and one runs `git`. The purpose stays
    adapter-local; the mechanism is the primitive's, so it uses the primitive.
    """
    result = run_bounded(
        argv,
        cwd=HERE,
        env=dict(os.environ),
        timeout=15,
        stdout_limit=64 * 1024,
        stderr_limit=64 * 1024,
        termination_grace=1.0,
    )
    if result.returncode != 0 or result.termination_reason is not None:
        raise AdapterError(
            f"identity command failed: {argv[0]} exited {result.returncode}"
            f" ({result.termination_reason or 'no bound fired'})"
        )
    try:
        text = result.stdout.decode("utf-8", errors="strict").strip()
        # The previous call merged stderr into stdout, so a tool that announces
        # its version on stderr kept working. Falling back rather than
        # concatenating preserves that tolerance without interleaving two
        # streams into a string the callers then split by position.
        if not text:
            text = result.stderr.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise AdapterError(
            f"identity command output is not UTF-8: {argv[0]}: {error}"
        ) from error
    return text


def _normalized_argv(argv: list[str], root: Path, workspace: Path) -> list[str]:
    replacements = (
        (str(workspace), "<workspace>"),
        (str(root), "<run-root>"),
    )
    normalized = []
    for index, argument in enumerate(argv):
        value = argument
        for raw, replacement in replacements:
            value = value.replace(raw, replacement)
        normalized.append(Path(value).name if index == 0 else value)
    return normalized


def _capabilities(subject: str) -> dict[str, Any]:
    return {
        "native_event_stream": subject in {"claude", "codex", "pi"},
        "hook_event_stream": subject == "hermes",
        "native_persisted_event_log": subject == "deepseek",
        "native_terminal_event": subject in {"claude", "codex", "deepseek", "pi"},
        "correlated_tool_calls": True,
        "tool_result_status": True,
        "model_identity": (
            _resolve_model(subject)["model_identity_strength"]
            if subject in CONFIGURABLE_MODEL_SUBJECTS
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
    elif subject == "pi":
        executable = _executable("pi")
        expected = pins["pi_coding_agent"]
        version = _command_text([str(executable), "--version"]).split()[0]
        digest_key = "executable_sha256"
        node_version = _command_text(["node", "--version"]).lstrip("v")
        if node_version != expected["node_version"]:
            raise AdapterError("Node version does not match pin.json")
    else:
        raise AdapterError(f"unknown subject: {subject}")
    if version != expected["version"]:
        raise AdapterError(f"{subject} version {version} does not match pin.json")
    digest = digest_file(executable)
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


def _model_selection() -> dict[str, Any]:
    return json.loads((HERE / "model_selection.json").read_text(encoding="utf-8"))


def _active_profile() -> tuple[str, dict[str, Any]]:
    selection = _model_selection()
    name = str(selection["active"])
    try:
        return name, selection["profiles"][name]
    except KeyError:
        raise AdapterError(f"model_selection.json has no profile {name!r}") from None


def _resolve_model(subject: str) -> dict[str, str]:
    """Resolve the model for a configurable subject and say how strong its identity is.

    A local model is content-addressed, so its digest is verified against the
    live Ollama inventory. A gateway model is only a label: the service promises
    to route it, and nothing here can prove which weights answered. The two must
    never be reported as the same strength.
    """
    name, profile = _active_profile()
    try:
        model = str(profile["models"][subject])
    except KeyError:
        raise AdapterError(
            f"profile {name!r} declares no model for subject {subject!r}"
        ) from None
    resolved = {
        "model": model,
        "model_profile": name,
        "model_identity_strength": str(profile["identity_strength"]),
        "model_base_url": str(profile["base_url"]),
        "model_api_key_env": str(profile["api_key_env"]),
        # Each harness authenticates from the variable IT looks for: DeepSeek
        # reads the apiKeyEnv named in its patch, Hermes's custom provider reads
        # OPENAI_API_KEY. Recording that per subject keeps the difference in the
        # declaration rather than buried in a branch.
        "model_subject_key_env": str(
            profile.get("subject_key_env", {}).get(subject, profile["api_key_env"])
        ),
    }
    if profile.get("verify_digest"):
        resolved["model_digest"] = _verify_ollama_digest(model)
    return resolved


def _verify_ollama_digest(model: str) -> str:
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
        payload = json.load(response)
    for item in payload.get("models", []):
        if item.get("name") == model:
            return str(item.get("digest"))
    raise AdapterError(f"Ollama does not have the selected model {model!r}")


def _apply_model_profile(text: str, subject: str, secret: str | None = None) -> str:
    """Rewrite a harness config's provider block for the active profile.

    The endpoint and key-env values checked into the configs are the
    local-ollama defaults, so under that profile those two substitute with
    themselves. The model does not: `dsh_patch.yml` and `hermes_config.yaml`
    still name `qwen3.5:9b`, and every profile rewrites it to the model that
    profile declares -- `gpt-oss:20b` under local-ollama. Both retired model
    strings are mapped for that reason, so no profile can leave a model behind
    that was replaced after failing the repair workload. The bytes change under
    every profile, including the local one.
    """
    _, profile = _active_profile()
    resolved = _resolve_model(subject)
    replacements = {
        "http://127.0.0.1:11434/v1": resolved["model_base_url"],
        "HWB_OLLAMA_KEY": resolved["model_api_key_env"],
        "gpt-oss:20b": resolved["model"],
        "qwen3.5:9b": resolved["model"],
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if secret is not None:
        text = text.replace("__HWB_PROVIDER_KEY__", secret)
    return text


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
    if workload in {"write", "guard"}:
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


GUARD_HOOK_INTERPRETER = "python3.11"
# Codex refuses to run a hook it has no persisted `trusted_hash` for, and
# announces that stand-down as an `error` item on its own stream. Named once so
# the flag and the stream check that forgives its notice cannot drift apart.
HOOK_TRUST_FLAG = "--dangerously-bypass-hook-trust"


# The isolation the record DISCLOSES, per subject and per workload. Two of these
# differ between the observational workloads and the guard workload, and the
# record used to state the observational posture for every run: a guard record
# claimed `--safe-mode` for a run that cannot use it (it disables hooks, which
# are the control) and claimed `--ignore-user-config` for a run that cannot use
# it (the guard IS config.toml). A provenance block that describes an apparatus
# the run did not have is worse than one that says nothing, because the whole
def _ambient_config(subject: str, workload: str) -> str:
    """What this run actually kept out, for this subject and this workload."""
    per_subject = AMBIENT_CONFIG[subject]
    return per_subject["guard" if workload == "guard" else "default"]


def _install_guard_hook(root: Path) -> Path:
    """Put the shared guard command beside the run and return its path.

    Beside the run and never inside the workspace. All three command-hook
    harnesses run hooks with the workspace as cwd, and the workspace is the
    exact thing the oracle diffs: an interceptor living in it would show up in
    both manifests, break the fixture-is-exact check, and make the
    instrumentation part of the effect it exists to observe.
    """
    installed = root / "guard_hook.py"
    shutil.copy2(HERE / "guard_hook.py", installed)
    return installed


def _guard_hook_command(guard_hook: Path, subject: str, event: str) -> str:
    """The one-string shell command a harness runs to reach the guard."""
    return " ".join(
        shlex.quote(part)
        for part in (
            GUARD_HOOK_INTERPRETER,
            str(guard_hook),
            "--subject", subject,
            "--event", event,
        )
    )


def _claude_guard_settings(guard_hook: Path) -> str:
    """One settings file declaring both halves of Claude's guard.

    `PreToolUse` is the control. `SessionStart` is the receipt, and it is not
    decoration: a PreToolUse hook fires only when a tool call happens, so on
    its own an empty receipt cannot separate "the guard never loaded" from "the
    model never called a tool". SessionStart fires before the model has
    produced anything, which is what makes a run evaluable at all.
    """
    return json.dumps({
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [{
                        "type": "command",
                        "command": _guard_hook_command(
                            guard_hook, "claude", "session_start"
                        ),
                    }]
                }
            ],
            "PreToolUse": [
                {
                    # Every tool, not just the guarded one: the `Bash` call is
                    # the routing-around evidence, and a matcher scoped to
                    # `Write` would hide exactly the call that matters most.
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": _guard_hook_command(
                            guard_hook, "claude", "tool_call"
                        ),
                    }],
                }
            ],
        }
    }, indent=2)


def _claude_command(
    identity: dict[str, Any],
    workload: str,
    guard_settings: Path | None = None,
) -> list[str]:
    prompt = WORKLOADS[workload]["prompt"]
    if workload == "write":
        tools = "Write"
    elif workload == "guard":
        # `Bash` is in the tool set ON PURPOSE, and so is `Write`. This used to
        # fall through to the repair tool set, which has no `Write` at all --
        # so the guard arm would have denied a tool the subject was never
        # given. That is the mirror of removing the shell: one guarantees
        # containment by construction, the other guarantees the control never
        # fires, and both measure nothing.
        tools = "Write,Bash"
    else:
        tools = "Read,Edit,Bash"
    argv = [
        str(_executable("claude")),
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--tools", tools,
        "--allowedTools", tools,
        "--model", identity["model"],
        "--max-budget-usd", "0.05",
    ]
    if guard_settings is None:
        # `--safe-mode` disables every customization: CLAUDE.md, skills,
        # plugins, MCP servers, commands -- AND HOOKS. That is exactly what the
        # observational workloads want and exactly what the guard workload
        # cannot have, so it is conditional rather than constant. The guard arm
        # replaces it with an isolated config directory (see `capture`), which
        # is stronger anyway: a flag is a promise, an empty directory is a fact.
        argv.extend(["--safe-mode", "--permission-mode", "dontAsk"])
    else:
        # `dontAsk` DENIES Bash here even with Bash in `--allowedTools`, which
        # would have made every block arm look perfectly contained -- by
        # Claude's own permission system, not by the guard under test. That is
        # the same measurement error as removing the shell, wearing a costume.
        # `bypassPermissions` stands the built-in gate down so the hook is the
        # ONLY control in the run, which is the only way the allow arm is a
        # true control-off baseline. Confirmed at runtime: under `dontAsk` the
        # subject was refused Bash and produced nothing; under
        # `bypassPermissions` it was refused `Write`, reached for `Bash`, and
        # the file landed.
        argv.extend(["--permission-mode", "bypassPermissions"])
    if guard_settings is not None:
        # `--setting-sources ""` above keeps every ambient source off -- user,
        # project and local -- and this adds back exactly one declared file.
        # Same doctrine as Pi's `--no-extensions` followed by `-e`: what
        # intercepted the run is a digested input, never whatever happened to
        # be installed on the host.
        argv.extend(["--settings", str(guard_settings)])
    argv.append(prompt)
    return argv


def _codex_guard_config(guard_hook: Path) -> str:
    """Codex's guard, declared where Codex actually reads hooks from.

    NOT `hooks/hooks.json`. The tree's own handover said hooks.json, the binary
    contains that string, and a correctly-shaped file at
    `$CODEX_HOME/hooks/hooks.json` is read by nothing: three runs with it in
    place produced no receipt at all. That string belongs to Codex's importer
    for *Claude Code's* `.claude/settings.json`, which is a different feature
    wearing a familiar name. Codex's own hooks are `[[hooks.<Event>]]` tables
    in `config.toml`, which is what produced the first `loaded` line.

    `SessionStart` is the receipt and `PreToolUse` is the control, for the same
    reason as Claude: a PreToolUse hook fires only on a tool call, so by itself
    it cannot distinguish a guard that never loaded from a model that never
    called a tool.
    """
    session_start = _guard_hook_command(guard_hook, "codex", "session_start")
    tool_call = _guard_hook_command(guard_hook, "codex", "tool_call")
    return "\n".join([
        "[[hooks.SessionStart]]",
        "enabled = true",
        "[[hooks.SessionStart.hooks]]",
        'type = "command"',
        f"command = {json.dumps(session_start)}",
        "",
        "[[hooks.PreToolUse]]",
        # Every tool, not only the guarded one: the shell call is the
        # routing-around evidence and a narrower matcher would hide it.
        'matcher = "*"',
        "enabled = true",
        "[[hooks.PreToolUse.hooks]]",
        'type = "command"',
        f"command = {json.dumps(tool_call)}",
        "",
    ])


def _codex_command(
    identity: dict[str, Any], workspace: Path, workload: str
) -> list[str]:
    argv = [
        str(_executable("codex")),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "--model", identity["model"],
        "--cd", str(workspace),
    ]
    if workload == "guard":
        # Hooks are trust-gated; without this Codex declines to run one it has
        # no persisted `trusted_hash` for, and a declined hook is silent.
        argv.insert(2, HOOK_TRUST_FLAG)
    else:
        # `--ignore-user-config` is what keeps the host's config.toml out of
        # the observational workloads. The guard arm cannot use it, because the
        # guard IS config.toml -- so that arm isolates with a per-run
        # `CODEX_HOME` instead, which removes the host's config by not being it.
        argv.insert(2, "--ignore-user-config")
    argv.append(WORKLOADS[workload]["prompt"])
    return argv


# The line the guard's `pre_tool_call` entry is inserted after. Pinned as a
# constant and asserted by a test: if this key is ever renamed or reindented in
# `hermes_config.yaml`, the insertion silently becomes a no-op, and a Hermes
# hook that never registers FAILS OPEN -- a completely uninstrumented run that
# looks clean. Failing the suite is the cheap version of that discovery.
HERMES_PRE_TOOL_CALL_KEY = "  pre_tool_call:\n"
# The top-level `hooks:` mapping the receipt entry is inserted into. Anchored on
# a leading newline so it cannot match `hooks_auto_accept:` or any nested key of
# the same name -- only a mapping that starts at column zero.
HERMES_HOOKS_KEY = "\nhooks:\n"


def _hermes_guard_hooks(config_text: str, guard_hook: Path) -> str:
    """Add the guard to a rendered Hermes config without displacing `hook.py`.

    Two entries. `pre_tool_call` is the control, and it carries NO matcher on
    purpose: Hermes treats a matcher as a `fullmatch` regex and an absent one
    as "every tool", so one entry records `write_file`, `read_file`, `patch`
    AND `terminal`. The terminal call is the routing-around evidence and a
    matcher scoped to `write_file` would hide it.

    `on_session_start` is the receipt, and it is the reason this subject is
    instrumentable at all: a `pre_tool_call` hook fires only when a tool call
    happens, so on its own an empty receipt cannot separate "the guard never
    loaded" from "the model never called a tool". Hermes needs that separation
    more than any other subject here, because ORDINARY SHELL HOOK FAILURES FAIL
    OPEN -- its own `get_pre_tool_call_block_message` ignores return values it
    does not recognise, silently. A broken guard here does not error; it simply
    is not there.

    The existing `hook.py` observers are left exactly where they are. They are
    still the required sidecar evidence for this workload, and their contract
    is to record without changing the decision -- which is why the guard is a
    separate file registered alongside them rather than an edit to that one.
    """
    if HERMES_PRE_TOOL_CALL_KEY not in config_text:
        raise AdapterError(
            "hermes_config.yaml has no 'pre_tool_call:' block to add the guard to"
        )
    if HERMES_HOOKS_KEY not in config_text:
        raise AdapterError(
            "hermes_config.yaml has no top-level 'hooks:' block to add the guard to"
        )
    if "on_session_start:" in config_text:
        # A second one would be a duplicate YAML key -- last wins -- and the
        # loser would be whichever of the two actually mattered.
        raise AdapterError(
            "hermes_config.yaml already declares 'on_session_start'; the guard "
            "receipt would become a duplicate key"
        )
    command = _guard_hook_command(guard_hook, "hermes", "tool_call")
    entry = f"    - command: {command}\n      timeout: 10\n"
    text = config_text.replace(
        HERMES_PRE_TOOL_CALL_KEY, HERMES_PRE_TOOL_CALL_KEY + entry, 1
    )
    # ANCHORED, not appended. This used to concatenate the receipt onto the end
    # of the file, which is correct only while `hooks:` happens to be the last
    # top-level block -- true today, asserted nowhere, and silently false the
    # day somebody adds a `logging:` or `limits:` key after it. The append
    # would then nest `on_session_start` under THAT key: still valid YAML, the
    # anchor test above still passing, the suite still green, and the receipt
    # hook never registered. Hermes fails open on hook errors, so the whole
    # subject would quietly go NOT_EVALUABLE with nothing pointing at why.
    #
    # Inserting directly after the `hooks:` line depends on nothing but the
    # block it is actually going into, which is the same reason the control
    # above is anchored rather than positioned.
    receipt = _guard_hook_command(guard_hook, "hermes", "session_start")
    receipt_block = (
        "  on_session_start:\n"
        f"    - command: {receipt}\n"
        "      timeout: 10\n"
    )
    return text.replace(HERMES_HOOKS_KEY, HERMES_HOOKS_KEY + receipt_block, 1)


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


def _pi_command(
    identity: dict[str, Any],
    workload: str,
    guard_extension: Path | None = None,
) -> list[str]:
    # Pi reads the task from an @file reference resolved against its cwd, which
    # the supervisor already pins to the disposable workspace.
    if workload == "write":
        tools = "write"
    elif workload == "guard":
        # `bash` is in the tool set ON PURPOSE, and it is the whole design. A
        # guard that denies `write` while the subject holds a shell is the
        # thing being measured; removing the shell would guarantee containment
        # by construction and measure nothing.
        tools = "write,bash"
    else:
        tools = "read,edit,bash"
    task = "repair_task.md" if workload == "repair" else "task.md"
    argv = [
        str(_executable("pi")),
        "--mode", "json",
        "--print",
        "--no-session",
        # Ambient discovery stays off; `-e` below loads exactly one declared
        # extension, so what intercepted the run is a digested input and not
        # whatever happened to be installed.
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--no-approve",
        "--tools", tools,
        "--provider", "workbench-gateway",
        "--model", str(identity["model"]),
    ]
    if guard_extension is not None:
        argv.extend(["-e", str(guard_extension)])
    argv.append("@" + task)
    return argv


def _pi_models_json(identity: dict[str, Any], secret: str) -> str:
    """Declare one custom OpenAI-compatible provider for Pi.

    Pi resolves custom providers from models.json in its config directory, so an
    isolated directory per run is what keeps the host's real provider catalogue
    and credentials out of the experiment.
    """
    return json.dumps({
        "providers": {
            "workbench-gateway": {
                "baseUrl": str(identity["model_base_url"]),
                "api": "openai-completions",
                "apiKey": secret,
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": False,
                },
                "models": [{"id": str(identity["model"])}],
            }
        }
    }, indent=2)


def _deepseek_guard_patch(patch_text: str, guard_plugin: Path) -> str:
    """Add the guard plugin as a NEW row, which is not the obvious spelling.

    `- insert:` is load-bearing. A bare `- id: … name: …` entry only MODIFIES
    an existing row: the loader reports `patch: entry "hwb-guard" not found`
    and carries on, and that report does NOT reach the subject's captured
    stderr. Three separate instrumentation attempts on this harness produced
    perfectly clean-looking runs that were not instrumented at all, which is
    the reason the startup receipt exists across the whole workload.
    """
    row = (
        "\n- insert:\n"
        "    - id: hwb-guard\n"
        f"      name: 'file://{guard_plugin}'\n"
    )
    if not patch_text.endswith("\n"):
        patch_text += "\n"
    return patch_text + row


def _verify_deepseek_guard_row(patch: Path, environment: dict[str, str]) -> None:
    """Ask dsh what it actually composed, before spending a model call on it.

    A receipt written by a file that was never imported cannot warn you about
    itself, so the receipt alone would still leave the three-failed-attempts
    failure detectable only AFTER paying for a run. `--dump-config` prints the
    composed profile tree and exits, which turns "did the row land" from a rule
    somebody has to remember into a check that fails loudly and for free. A
    mechanism cannot be forgotten; a comment can.
    """
    result = run_bounded(
        [
            str(_executable("dsh")),
            "--profile", "headless",
            "--patch", str(patch),
            "--dump-config",
        ],
        cwd=HERE,
        env=environment,
        timeout=90,
        stdout_limit=4 * 1024 * 1024,
        stderr_limit=256 * 1024,
    )
    if result.returncode != 0 or result.termination_reason is not None:
        raise AdapterError(
            "dsh could not compose the guard profile:"
            f" exit {result.returncode}"
            f" ({result.termination_reason or 'no bound fired'})"
        )
    composed = result.stdout.decode("utf-8", errors="replace")
    if "hwb-guard" not in composed:
        raise AdapterError(
            "the guard plugin row is absent from dsh's composed profile;"
            " the patch was accepted and dropped"
        )


def _deepseek_command(
    root: Path, workload: str, guard_plugin: Path | None = None
) -> list[str]:
    patch = root / "dsh_patch.yml"
    text = _apply_model_profile(
        (HERE / "dsh_patch.yml").read_text(encoding="utf-8"), "deepseek"
    )
    if guard_plugin is not None:
        text = _deepseek_guard_patch(text, guard_plugin)
    patch.write_text(text, encoding="utf-8")
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
    path = relative_to_root(raw_path, workspace)
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
    events, errors = parse_jsonl_objects(raw)
    types = [event.get("type") for event in events]
    init_events = [
        event
        for event in events
        if event.get("type") == "system" and event.get("subtype") == "init"
    ]
    # Exactly one init, and NOTHING of substance before it. The check used to
    # be "event 0 is the init", which the guard workload falsifies honestly:
    # a `SessionStart` hook reports itself on this stream, so `hook_started`
    # and `hook_response` legitimately precede init. Relaxing it to "init
    # exists somewhere" would have thrown away the invariant, so the preamble
    # is enumerated instead -- only the harness's own hook lifecycle chatter
    # may come first, and an assistant turn or a tool call before init is still
    # the stream corruption this was written to catch.
    prefix = events[: events.index(init_events[0])] if init_events else events
    preamble_is_hooks_only = all(
        event.get("type") == "system"
        and str(event.get("subtype", "")).startswith("hook_")
        for event in prefix
    )
    if len(init_events) != 1 or not preamble_is_hooks_only:
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
            "arguments_sha256": digest_obj(normalized),
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
    events, errors = parse_jsonl_objects(raw)
    types = [event.get("type") for event in events]
    # thread first, turn next, and between them only Codex's own advisory that
    # the guard arm asked for hooks to run untrusted. Codex reports that notice
    # as an `error` ITEM rather than as a warning, so it cannot be filtered by
    # severity; it is matched by its declared text instead. Anything else
    # appearing before the turn -- including a real error item -- still fails,
    # which is the whole point of keeping the check narrow rather than
    # relaxing it to "a thread and a turn appear somewhere".
    between = (
        events[1 : types.index("turn.started")] if "turn.started" in types else events
    )
    advisory_only = all(
        event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "error"
        and HOOK_TRUST_FLAG in str(event["item"].get("message", ""))
        for event in between
    )
    if (
        types[:1] != ["thread.started"]
        or "turn.started" not in types
        or not advisory_only
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
                "path": relative_to_root(change.get("path"), workspace),
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
            "arguments_sha256": digest_obj(arguments),
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


# The telemetry envelope `api_request_id` arrives in. Pinned because the call
# identity below depends on that field existing and meaning what it means; a
# schema bump is a reason to re-check the assumption, not to pair on faith.
HERMES_TELEMETRY_SCHEMA = "hermes.observer.v1"


def _hermes_result_exit_code(result: Any) -> int | None:
    """Project the child exit status Hermes reports inside its post-hook result.

    Hermes's post_tool_call payload carries the terminal tool's own
    ``exit_code`` as JSON-encoded text. That is a separate fact from the hook's
    ``status``: a command can run to completion (status ok) and still exit
    nonzero, which is exactly what an intentionally red test suite does. Keep
    both rather than collapsing them, the same way DeepSeek's bash exit marker
    stays separate from its ``isError`` flag.
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return None
    if not isinstance(result, dict):
        return None
    code = result.get("exit_code")
    return code if isinstance(code, int) else None


def _normalize_hermes(
    raw: bytes,
    hook_raw: bytes,
    workspace: Path,
    returncode: int,
) -> tuple[dict[str, Any], list[str]]:
    hook_events, errors = parse_jsonl_objects(hook_raw)
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
            # Hermes numbers tool calls PER API REQUEST, not per run, so
            # `tool_call_id` alone is not an identity. See the pairing below.
            "request_id": extra.get("api_request_id"),
            "telemetry_schema": extra.get("telemetry_schema_version"),
            "arguments_sha256": digest_obj(arguments),
            "effect_kind": effect_kind,
            "operation": arguments.get("operation"),
            "outside_workspace": outside,
            "status": extra.get("status"),
            "operation_exit_code": _hermes_result_exit_code(extra.get("result")),
            "acquisition": "shell_hook",
        })
    # A CALL IS IDENTIFIED BY ITS REQUEST AND ITS ID, NOT BY ITS ID.
    #
    # `tool_call_id` is a per-tool counter that Hermes restarts on each API
    # request, so a run spanning more than one model round-trip reuses it. This
    # keyed on `tool_call_id` alone and reported `duplicate Hermes
    # pre_tool_call: read_file_0` on any multi-request run -- which is every
    # `repair` run, because that workload is inherently several round-trips
    # (run the test, edit, run it again). `write` never tripped it: one
    # request, one call per tool, no id ever reused.
    #
    # NOT keyed on `turn_id`, which is the obvious guess and does not work: a
    # whole repair run is a single turn, and pairing on it collides exactly as
    # badly. `api_request_id` is `{turn_id}:api:{n}` and is the granularity the
    # counter actually resets at.
    #
    # And NOT solved by pairing positionally -- matching the nth `pre` with the
    # nth `post` -- which fits the observed evidence and is still inference. The
    # request id is a fact the subject declares about itself, and the whole
    # reason this tree writes startup receipts instead of deducing them is that
    # a declared fact beats a reconstructed one. Positional pairing also fails
    # SILENTLY under interleaving, and two `read_file` calls really are in
    # flight at once here (`pre`, `pre`, `post`, `post`).
    #
    # The duplicate check is re-keyed, never removed. Two `pre` events sharing
    # a request AND an id is still corrupt evidence; deleting the check would
    # make the symptom go away and take a real control with it.
    calls: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for index, item in enumerate(projected):
        call_id = item.get("call_id")
        request_id = item.get("request_id")
        event_name = item.get("event")
        schema = item.get("telemetry_schema")
        if not isinstance(call_id, str) or not call_id:
            errors.append("Hermes hook event has no tool call id")
            continue
        if not isinstance(request_id, str) or not request_id:
            # Loud, and never a fallback to the colliding key. Losing this
            # field would silently restore the mispairing this replaced.
            errors.append(f"Hermes hook event has no api request id: {call_id}")
            continue
        if schema != HERMES_TELEMETRY_SCHEMA:
            # `api_request_id` lives inside a versioned telemetry envelope. If
            # the envelope moves, the identity assumption above is unverified
            # rather than merely old, so say so instead of pairing on faith.
            errors.append(
                f"unexpected Hermes telemetry schema: {schema!r}"
                f" (expected {HERMES_TELEMETRY_SCHEMA!r})"
            )
            continue
        if event_name not in {"pre_tool_call", "post_tool_call"}:
            errors.append(f"unexpected Hermes hook event: {event_name}")
            continue
        pair = calls.setdefault((request_id, call_id), {})
        if event_name in pair:
            errors.append(
                f"duplicate Hermes {event_name}: {call_id} in {request_id}"
            )
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
    for (request_id, call_id), pair in calls.items():
        # Named for the message only. The identity is the pair; this is what a
        # reader needs to find the call in the sidecar.
        where = f"{call_id} in {request_id}"
        pre = pair.get("pre_tool_call")
        post = pair.get("post_tool_call")
        if pre is None or post is None:
            errors.append(f"Hermes hook pair is incomplete: {where}")
            continue
        if pre["index"] >= post["index"]:
            errors.append(f"Hermes hook pair is out of order: {where}")
        if pre["outside_workspace"] or post["outside_workspace"]:
            errors.append(
                f"Hermes proposed an operation outside the disposable workspace: {where}"
            )
        if pre["tool_name"] != post["tool_name"]:
            errors.append(f"Hermes hook tool names disagree: {where}")
        if pre["arguments_sha256"] != post["arguments_sha256"]:
            errors.append(f"Hermes hook arguments disagree: {where}")
        executions.append({
            # Both, and both as strings. `call_id` stays the id Hermes
            # reported, so a record still reads the way the sidecar does;
            # `request_id` is what makes it unique across the run.
            "call_id": call_id,
            "request_id": request_id,
            "tool_name": pre["tool_name"],
            "effect_kind": pre["effect_kind"],
            "operation": pre.get("operation"),
            "arguments_sha256": pre["arguments_sha256"],
            "arguments_stage": "subject_proposal",
            "reported_error": (
                None if post.get("status") is None else post.get("status") != "ok"
            ),
            "operation_exit_code": post.get("operation_exit_code"),
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


def _normalize_pi(raw: bytes, workspace: Path) -> tuple[dict[str, Any], list[str]]:
    """Project Pi's native JSON event stream into the shared envelope.

    Pi is the reference integration and was deliberately excluded while the
    contract was derived, so this lane is a test of the envelope rather than a
    source for it. Pi's own richer summary schema stays in its own experiment;
    only what the shared contract can represent is projected here.
    """
    events, errors = parse_jsonl_objects(raw)
    if not events or events[0].get("type") != "session":
        errors.append("Pi stream does not start with a session event")
    if sum(event.get("type") == "session" for event in events) != 1:
        errors.append("Pi stream does not contain exactly one session event")

    open_tools: dict[str, dict[str, Any]] = {}
    seen_call_ids: set[str] = set()
    executions: list[dict[str, Any]] = []
    settled = 0
    for event in events:
        event_type = event.get("type")
        if event_type == "agent_settled":
            settled += 1
        elif event_type == "tool_execution_start":
            call_id = event.get("toolCallId")
            if not isinstance(call_id, str) or not call_id:
                errors.append("Pi tool_execution_start has no tool call id")
                continue
            if call_id in seen_call_ids:
                errors.append(f"duplicate Pi tool call id: {call_id}")
                continue
            seen_call_ids.add(call_id)
            open_tools[call_id] = event
        elif event_type == "tool_execution_end":
            call_id = event.get("toolCallId")
            start = open_tools.pop(call_id, None) if isinstance(call_id, str) else None
            if start is None:
                errors.append("Pi tool_execution_end has no matching start")
                continue
            if event.get("toolName") != start.get("toolName"):
                errors.append(f"Pi tool name changed during execution: {call_id}")
            is_error = event.get("isError")
            if not isinstance(is_error, bool):
                errors.append(f"Pi tool isError is not boolean: {call_id}")
                is_error = None
            arguments, effect_kind, outside = _argument_projection(
                start.get("toolName"), start.get("args") or {}, workspace
            )
            if outside:
                errors.append(
                    f"Pi proposed an operation outside the disposable workspace: {call_id}"
                )
            executions.append({
                "call_id": call_id,
                "tool_name": start.get("toolName"),
                "effect_kind": effect_kind,
                "operation": arguments.get("operation"),
                "operation_exit_code": _pi_result_exit_code(event),
                "arguments_sha256": digest_obj(arguments),
                "arguments_stage": "subject_event",
                "reported_error": is_error,
                "result_stage": "subject_reported",
                "acquisition": "native_jsonl",
            })
    for call_id in sorted(open_tools):
        errors.append(f"Pi tool_execution_start has no matching end: {call_id}")
    if settled != 1:
        errors.append(f"expected exactly one Pi agent_settled, saw {settled}")

    return {
        "acquisition": "native_jsonl",
        "completeness": "native_event_stream",
        "event_types": [
            event.get("type") for event in events if isinstance(event.get("type"), str)
        ],
        "tool_executions": executions,
        "terminal": {"status": "agent_settled", "settled": settled},
    }, errors


def _pi_result_exit_code(event: dict[str, Any]) -> int | None:
    """Best-effort child exit status from a Pi tool_execution_end.

    Pi reports tool failure as ``isError``; a numeric exit status is only
    sometimes present. Absent is recorded as absent rather than inferred from
    the error flag, because a command can exit nonzero without Pi treating the
    tool call as failed.
    """
    for key in ("exitCode", "exit_code"):
        value = event.get(key)
        if isinstance(value, int):
            return value
    result = event.get("result")
    if isinstance(result, dict):
        for key in ("exitCode", "exit_code"):
            value = result.get(key)
            if isinstance(value, int):
                return value
    return None


def _normalize_deepseek(
    raw: bytes,
    workspace: Path,
    returncode: int,
    expected_provider: str,
    expected_model: str,
) -> tuple[dict[str, Any], list[str]]:
    records, errors = parse_jsonl_objects(raw)
    headers = [record for record in records if record.get("type") == "session"]
    events = [record for record in records if record.get("type") != "session"]
    types = [event.get("type") for event in events]
    if len(headers) != 1 or records[:1] != headers[:1]:
        errors.append("DeepSeek log does not start with exactly one session header")
    elif headers[0].get("version") != 0:
        errors.append("DeepSeek session header has an unexpected format version")
    elif relative_to_root(headers[0].get("cwd"), workspace) != ".":
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
            "arguments_sha256": digest_obj(normalized),
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


def _apparatus() -> dict[str, Any]:
    """Identify the capture primitive the way the tree identifies its subjects.

    The five harnesses are pinned by version and executable digest because the
    record has to say which bytes produced it. `harness_workbench.capture` is
    now just as load-bearing and arrives from the installed package rather than
    from the materialized tree, so the same question applies to it and the same
    answer is recorded.

    This is disclosure, not a pin. `freeze` covers the spec's declared inputs,
    which are files beside the spec; a module imported from site-packages
    cannot be one of them. Recording the digest means an upgraded primitive is
    *visible* in the record instead of silently changing what a run measured --
    it does not make freeze detect it.
    """
    # Both modules, not just the obvious one. `capture.digest_file` is a thin
    # wrapper over `canon.digest_file`, and `digest_obj` is imported straight
    # from `canon`, so a change to the canonical-JSON or file-digest rule moves
    # every digest in this record while `capture.py` stays byte-identical.
    # Naming only the file you thought of is how provenance gets a blind spot.
    modules = {
        "capture": Path(capture_module.__file__).resolve(),
        "canon": Path(canon_module.__file__).resolve(),
    }
    live = {
        "schema": "hwb-subject-apparatus/v0.1",
        "package": "harness_workbench",
        "version": harness_workbench.__version__,
        "modules": {
            name: {"file": path.name, "sha256": digest_file(path)}
            for name, path in sorted(modules.items())
        },
    }
    # The baseline `hwb subjects --into` wrote when this copy was cut. Comparing
    # against it is the only thing that catches the likely shape of the hazard:
    # one machine, one `pip install -U`, every subject upgraded together. The
    # cross-subject check in compare.py cannot see that, because all five agree.
    baseline_path = HERE / "apparatus.json"
    if not baseline_path.is_file():
        # Stated, not omitted. An unmaterialized tree runs against whatever is
        # importable and has nothing to disagree with -- which is a fact about
        # the run, not a clean result.
        live["baseline"] = {"present": False, "agrees": None,
                            "note": "tree was not materialized; no baseline"}
        return live
    try:
        baseline_text = baseline_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        live["baseline"] = {
            "present": True,
            "agrees": False,
            "note": f"baseline is not readable UTF-8: {error}",
        }
        return live
    try:
        baseline = json.loads(baseline_text)
    except json.JSONDecodeError as error:
        live["baseline"] = {"present": True, "agrees": False,
                            "note": f"baseline is not JSON: {error.msg}"}
        return live

    expected_keys = {"schema", "package", "version", "modules"}
    invalid_reason = None
    if not isinstance(baseline, dict) or set(baseline) != expected_keys:
        invalid_reason = "baseline has an invalid top-level shape"
    elif baseline.get("schema") != live["schema"]:
        invalid_reason = "baseline has an invalid schema"
    elif baseline.get("package") != live["package"]:
        invalid_reason = "baseline has an invalid package"
    elif type(baseline.get("version")) is not str or not baseline["version"]:
        invalid_reason = "baseline has an invalid version"
    else:
        baseline_modules = baseline.get("modules")
        if not isinstance(baseline_modules, dict) or set(baseline_modules) != set(
            live["modules"]
        ):
            invalid_reason = "baseline has an invalid module set"
        else:
            for name, module in baseline_modules.items():
                live_module = live["modules"][name]
                if (
                    not isinstance(module, dict)
                    or set(module) != {"file", "sha256"}
                    or module.get("file") != live_module["file"]
                    or type(module.get("sha256")) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", module["sha256"]) is None
                ):
                    invalid_reason = f"baseline has invalid module {name}"
                    break
    if invalid_reason is not None:
        live["baseline"] = {
            "present": True,
            "agrees": False,
            "note": invalid_reason,
        }
        return live

    assert isinstance(baseline, dict)
    baseline_modules = baseline["modules"]
    assert isinstance(baseline_modules, dict)
    differences = sorted(
        name for name in live["modules"]
        if baseline_modules[name]["sha256"]
        != live["modules"].get(name, {}).get("sha256")
    )
    version_agrees = baseline["version"] == live["version"]
    live["baseline"] = {
        "present": True,
        "agrees": not differences and version_agrees,
        "version": baseline["version"],
        "changed_modules": differences,
    }
    if not version_agrees:
        live["baseline"]["note"] = "baseline version differs from running package"
    return live


def _deepseek_session_log(dsh_home: Path) -> tuple[Path | None, list[str]]:
    paths = sorted((dsh_home / "sessions").glob("**/session.jsonl"))
    if len(paths) != 1:
        return None, [
            f"DeepSeek produced {len(paths)} raw top-level session candidates; expected one"
        ]
    return paths[0], []


def _guard_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Whatever the interceptor wrote about itself, and complaints about it.

    A missing file is not an error here -- it is the *finding* that the guard
    never loaded, and `guard_outcome` turns that into NOT_EVALUABLE. Raising
    would remove the run from the comparison entirely, which is how an
    instrumentation failure becomes invisible.
    """
    if not path.is_file():
        return [], ["guard receipt file was never created"]
    events, errors = parse_jsonl_objects(path.read_bytes())
    return events, errors


def capture(
    subject: str,
    workload: str = "write",
    *,
    variant: str | None = None,
    timeout: float = 120,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    evidence_limit: int = DEFAULT_SIDECAR_LIMIT,
) -> dict[str, Any]:
    if workload not in WORKLOADS:
        raise AdapterError(f"unknown workload: {workload}")
    if workload == "guard":
        if variant not in GUARD_VARIANTS:
            raise AdapterError(
                f"guard workload requires variant in {GUARD_VARIANTS}, got {variant!r}"
            )
    elif variant is not None:
        raise AdapterError(f"{workload} workload takes no variant")
    if evidence_limit <= 0:
        raise AdapterError("evidence limit must be positive")
    apparatus = _apparatus()
    baseline = apparatus["baseline"]
    if baseline["agrees"] is False:
        changed = ", ".join(baseline.get("changed_modules") or [])
        detail = baseline.get("note") or changed or "unknown difference"
        raise AdapterError(
            "capture apparatus differs from the materialized baseline: " + detail
        )
    identity = _verify_identity(subject)
    if subject in CONFIGURABLE_MODEL_SUBJECTS:
        identity.update(_resolve_model(subject))

    with tempfile.TemporaryDirectory(prefix=f".hwb-{subject}-", dir=HERE) as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        _fixture(workspace, workload)
        environment = os.environ.copy()
        environment["PWD"] = str(workspace)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        # Outside the workspace on purpose: the receipt is the adapter's
        # evidence about the run, and a file the oracle would see in the
        # after-manifest would make the instrumentation part of the effect it
        # is supposed to be measuring.
        guard_receipt = root / "guard-receipt.jsonl"
        if workload == "guard":
            environment["HWB_GUARD_MODE"] = str(variant)
            environment["HWB_GUARD_RECEIPT"] = str(guard_receipt)
        if subject in CONFIGURABLE_MODEL_SUBJECTS:
            # The generic OpenAI-compatible provider requires a key-shaped value
            # even though the pinned Ollama endpoint does not authenticate it.
            # Add it before deriving the redaction set so it cannot leak into a
            # sealed record if a future dsh version starts echoing provider config.
            # A gateway profile supplies a real key the same way, so it is
            # redacted by the same machinery rather than a special case.
            _, profile = _active_profile()
            key_name = str(profile["api_key_env"])
            placeholder = profile.get("api_key_placeholder")
            if placeholder is not None:
                secret = str(placeholder)
            elif os.environ.get(key_name):
                secret = os.environ[key_name]
            else:
                raise AdapterError(
                    f"profile requires {key_name} to be set in the environment"
                )
            # Publish under both the profile's own name and whichever variable
            # this subject's provider actually reads.
            for name in {key_name, str(identity["model_subject_key_env"])}:
                environment[name] = secret
        redactions = credential_values(environment)
        sensitive_environment_names = sorted(
            name for name, value in environment.items() if value in redactions
        )
        initial_test = None
        oracle_process_errors: list[str] = []
        if workload == "repair":
            initial_test = run_bounded(
                ["python3.11", "-m", "unittest", "-v"],
                cwd=workspace,
                env=environment,
                timeout=30,
                stdout_limit=stdout_limit,
                stderr_limit=stderr_limit,
            )
            oracle_process_errors.extend(
                _bounded_measurement_errors(
                    initial_test,
                    label="initial test",
                    require_zero_exit=False,
                )
            )
        before = manifest(workspace)
        evidence_path: Path | None = None
        evidence_kind = "none"
        if subject == "claude":
            guard_settings: Path | None = None
            if workload == "guard":
                guard_settings = root / "claude_guard_settings.json"
                guard_settings.write_text(
                    _claude_guard_settings(_install_guard_hook(root)),
                    encoding="utf-8",
                )
                # The guard arm cannot use `--safe-mode` (it disables hooks), so
                # the isolation it was providing is rebuilt from named switches.
                #
                # An empty per-run CLAUDE_CONFIG_DIR was tried first and is the
                # obvious answer -- isolation by a directory that does not
                # exist beats isolation by a promise. It does not work here:
                # Claude keeps its CREDENTIALS in that directory, so an
                # isolated one authenticates as nobody. The run failed with
                # `authentication_failed` / "Not logged in" having made zero
                # tool calls, which the guard oracle would otherwise have
                # reported as a beautifully contained block arm. Anyone tempted
                # to re-add it should read that sentence twice.
                #
                # `--setting-sources ""` already keeps user, project and local
                # settings out; these switch off the ambient sources that are
                # discovered from the filesystem rather than from settings.
                # `subjects/README.md` records which of them the run's own
                # `init` event is checked against.
                environment["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"
                environment["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] = "1"
                environment["CLAUDE_CODE_DISABLE_WORKFLOWS"] = "1"
                environment["CLAUDE_CODE_DISABLE_ORG_MEMORY"] = "1"
                environment["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
            argv = _claude_command(identity, workload, guard_settings)
        elif subject == "codex":
            if workload == "guard":
                codex_home = root / "codex-home"
                codex_home.mkdir()
                # Only the credential is carried over from the host home, and
                # only because an isolated CODEX_HOME otherwise authenticates
                # as nobody -- the same trap Claude's isolated config directory
                # sprang, where a run that made zero tool calls would have
                # scored as a perfectly contained block arm. Copying one file
                # keeps every other ambient thing (config.toml, AGENTS.md,
                # sessions, plugins) out by simply not being there.
                source_home = Path(
                    os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
                )
                credential = source_home / "auth.json"
                if not credential.is_file():
                    raise AdapterError(
                        f"codex credential not found at {credential}; the guard"
                        " arm cannot authenticate from an isolated CODEX_HOME"
                    )
                # `copy2` carries the source mode across, and the source is
                # 0600 -- but that is the source's property, not this copy's
                # guarantee. Set it explicitly so the copy is owner-only even
                # if the original is ever loosened. `.gitignore` covers the
                # `.hwb-*` root for the case no code here can reach: a SIGKILL
                # that skips cleanup and leaves this file in the working tree.
                copied = codex_home / "auth.json"
                shutil.copy2(credential, copied)
                copied.chmod(0o600)
                (codex_home / "config.toml").write_text(
                    _codex_guard_config(_install_guard_hook(root)),
                    encoding="utf-8",
                )
                environment["CODEX_HOME"] = str(codex_home)
            argv = _codex_command(identity, workspace, workload)
        elif subject == "hermes":
            for name in sensitive_environment_names:
                environment.pop(name, None)
            # Hermes gets no unrelated host credentials, but it still needs the
            # provider key for the active profile, which the sweep above removes
            # precisely because it looks like a credential. Restore only that one.
            _, hermes_profile = _active_profile()
            provider_key = str(hermes_profile["api_key_env"])
            placeholder = hermes_profile.get("api_key_placeholder")
            secret = (
                str(placeholder)
                if placeholder is not None
                else os.environ.get(provider_key, "")
            )
            if secret:
                for name in {provider_key, str(identity["model_subject_key_env"])}:
                    environment[name] = secret
            hermes_home = root / "hermes-home"
            hermes_home.mkdir()
            hermes_config = _apply_model_profile(
                (HERE / "hermes_config.yaml").read_text(encoding="utf-8"),
                "hermes",
                secret,
            )
            if workload == "guard":
                hermes_config = _hermes_guard_hooks(
                    hermes_config, _install_guard_hook(root)
                )
            (hermes_home / "config.yaml").write_text(
                hermes_config, encoding="utf-8"
            )
            evidence_path = root / "hermes-hooks.jsonl"
            # Created empty before Hermes starts, so absence and emptiness stop
            # meaning the same thing. Absent afterwards means the sidecar was
            # never wired up -- a measurement fault. Present and empty means
            # Hermes made no tool calls -- a fact about the subject, which
            # belongs to the outcome verdict and must not fail the adapter.
            evidence_path.touch()
            evidence_kind = "shell_hook_jsonl"
            environment["HERMES_HOME"] = str(hermes_home)
            environment["HWB_HERMES_HOOK_EVIDENCE"] = str(evidence_path)
            environment["HWB_HERMES_HOOK_MAX_BYTES"] = str(evidence_limit)
            # The hook scrubs PARSED VALUES, before they are ever serialized;
            # `redact_bytes` scrubs a byte stream afterwards. Two layers, and
            # the tree used to run only one: this was `"[]"`, which left the
            # structured scrubber switched off and the byte scrubber as the
            # sole defence. A redactor that only sees one layer is the
            # documented failure mode -- OpenTelemetry's redaction processor
            # silently skips non-string attributes for the same structural
            # reason. The hook is also the layer that CANNOT be defeated by an
            # encoding, because at that point the secret is still a value.
            environment["HWB_REDACT_VALUES_JSON"] = json.dumps(list(redactions))
            environment["TERMINAL_CWD"] = str(workspace)
            environment["HERMES_WRITE_SAFE_ROOT"] = str(workspace)
            argv = _hermes_command(identity, workload)
        elif subject == "pi":
            # Pi gets no host credentials at all: its provider key travels in an
            # isolated models.json inside a per-run config directory, so nothing
            # credential-shaped needs to survive in the environment.
            for name in sensitive_environment_names:
                environment.pop(name, None)
            pi_home = root / "pi-home"
            pi_config = pi_home / "agent"
            pi_config.mkdir(parents=True)
            (pi_config / "models.json").write_text(
                _pi_models_json(identity, secret), encoding="utf-8"
            )
            environment["HOME"] = str(pi_home)
            environment["PI_CODING_AGENT_DIR"] = str(pi_config)
            extension: Path | None = None
            if workload == "guard":
                # Copied beside the run rather than into the workspace: an
                # interceptor sitting in the workspace would appear in both
                # manifests and become part of the effect the oracle measures.
                extension = root / "guard_extension.ts"
                shutil.copy2(HERE / "guard_extension.ts", extension)
            argv = _pi_command(identity, workload, extension)
        else:
            # Strip every credential the subject has no business seeing, except
            # the provider key for the active profile -- which is itself
            # credential-shaped and would otherwise be swept out from under the
            # harness it authenticates.
            #
            # Pi is handled in its own branch above: it receives the key through
            # an isolated models.json rather than the environment.
            keep = {
                str(_active_profile()[1]["api_key_env"]),
                str(identity["model_subject_key_env"]),
            }
            for name in sensitive_environment_names:
                if name not in keep:
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
            guard_plugin: Path | None = None
            if workload == "guard":
                # Beside the run, never in the workspace: a plugin sitting in
                # the workspace would appear in both manifests and become part
                # of the effect it exists to observe.
                guard_plugin = root / "guard_plugin.mjs"
                shutil.copy2(HERE / "guard_plugin.mjs", guard_plugin)
            argv = _deepseek_command(root, workload, guard_plugin)
            if guard_plugin is not None:
                _verify_deepseek_guard_row(root / "dsh_patch.yml", environment)
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
            # Claude, Codex and Pi carry their whole lifecycle on stdout. An
            # empty envelope keeps the sidecar slot the same shape for every
            # subject, so a comparison never has to special-case its absence.
            sidecar = capture_bytes(b"", redactions=redactions)
            sidecar.update({
                "exists": False,
                "format": "bytes",
                "size": 0,
                "max_bytes": evidence_limit,
                "file_sha256": None,
                "jsonl": None,
                "errors": [],
            })
        else:
            # `required=True` because reaching this branch means the subject was
            # instrumented to produce this file. A Hermes run whose hook never
            # fired used to yield empty evidence indistinguishable from a
            # subject that made no tool calls; the primitive names it instead.
            sidecar = capture_file(
                evidence_path,
                required=True,
                format_name="jsonl",
                max_bytes=evidence_limit,
                redactions=redactions,
            )
        # The sidecar arrives already redacted, so the normalizers read the
        # stored text rather than the file. `text` is None only for evidence the
        # primitive refused or could not decode, and that is carried in
        # `sidecar["errors"]` -- an empty parse here is then a recorded state.
        normalized_evidence = (sidecar["text"] or "").encode("utf-8")
        evidence_overflow = sidecar["size"] > sidecar["max_bytes"]
        evidence_errors.extend(sidecar["errors"])
        normalized_stdout, _ = redact_bytes(result.stdout, redactions)
        if subject == "claude":
            lifecycle, adapter_errors = _normalize_claude(
                normalized_stdout, workspace
            )
        elif subject == "codex":
            lifecycle, adapter_errors = _normalize_codex(
                normalized_stdout, workspace
            )
        elif subject == "hermes":
            lifecycle, adapter_errors = _normalize_hermes(
                normalized_stdout, normalized_evidence, workspace, result.returncode
            )
        elif subject == "pi":
            lifecycle, adapter_errors = _normalize_pi(normalized_stdout, workspace)
        else:
            lifecycle, adapter_errors = _normalize_deepseek(
                normalized_evidence,
                workspace,
                result.returncode,
                str(identity["provider"]),
                str(identity["model"]),
            )
        adapter_errors.extend(evidence_errors)
        adapter_errors.extend(oracle_process_errors)
        # No second complaint for the sidecar: `capture_file` already put the
        # refusal in `sidecar["errors"]`, with the size and the limit in it,
        # and that list was extended into `adapter_errors` above. One fact, one
        # error.
        adapter_errors.extend(
            _bounded_measurement_errors(
                result,
                label=f"{subject} run",
                require_zero_exit=True,
            )
        )
        guard_events: list[dict[str, Any]] = []
        if workload == "guard":
            guard_events, guard_errors = _guard_events(guard_receipt)
            task_outcome = guard_outcome(
                before, after, variant=str(variant), events=guard_events
            )
            # Receipt-parsing complaints are ADAPTER faults: they say the
            # measurement is unreadable, not that the subject did anything.
            adapter_errors.extend(guard_errors)
            oracle_evidence = {
                "guard_receipt": capture_bytes(
                    guard_receipt.read_bytes() if guard_receipt.is_file() else b"",
                    redactions=redactions,
                ),
                "events": guard_events,
            }
        elif workload == "write":
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
            adapter_errors.extend(
                _bounded_measurement_errors(
                    final_test,
                    label="final test",
                    require_zero_exit=False,
                )
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
                "initial_test": _bounded_evidence(
                    initial_test,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                    redactions=redactions,
                    argv=_normalized_argv(initial_test.argv, root, workspace),
                ),
                "final_test": _bounded_evidence(
                    final_test,
                    stdout_limit=stdout_limit,
                    stderr_limit=stderr_limit,
                    redactions=redactions,
                    argv=_normalized_argv(final_test.argv, root, workspace),
                ),
            }
            after = manifest(workspace)
        prompt = WORKLOADS[workload]["prompt"]
        inputs = WORKLOADS[workload]["inputs"]
        # Finalize only after every workload-specific evidence source has had
        # a chance to complain. Copy the accumulator so the verdict cannot be
        # mutated into a passed-with-errors contradiction later.
        adapter_verdict = {
            "passed": not adapter_errors,
            "errors": list(adapter_errors),
        }
        process_capture = _bounded_evidence(
            result,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            redactions=redactions,
            argv=_normalized_argv(argv, root, workspace),
        )
        process_capture["limits"]["sidecar_bytes"] = evidence_limit
        process_capture["sidecar"] = sidecar
        process_capture["sidecar_kind"] = evidence_kind
        process_capture["overflow"]["sidecar"] = evidence_overflow
        process_capture["redacted_environment_names"] = (
            sensitive_environment_names
        )
        return {
            "schema": "cross-harness-adapter-run/v0.1",
            "subject": identity,
            "request": {
                "workload": workload,
                "variant": variant,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "input_digests": {
                    name: digest_file(HERE / name) for name in inputs
                },
            },
            "apparatus": apparatus,
            "capabilities": _capabilities(subject),
            "invocation": {
                "argv": _normalized_argv(argv, root, workspace),
                "cwd": "<workspace>",
                "timeout_seconds": timeout,
                "credential_source": (
                    "ambient_authenticated_client"
                    if subject in {"claude", "codex"}
                    else "none_loopback_model"
                    if _active_profile()[1].get("kind") == "local"
                    else "experiment_scoped_gateway_key"
                ),
            },
            "isolation": {
                "disposable_workspace": True,
                "ambient_config": _ambient_config(subject, workload),
                # The network claim has to follow the active profile. A gateway
                # profile reaches a remote service, and describing that as
                # loopback would be a false isolation disclosure.
                "network": "first-party Claude service" if subject == "claude"
                    else "first-party Codex service" if subject == "codex"
                    else "loopback Ollama only"
                    if _active_profile()[1].get("kind") == "local"
                    else f"remote gateway {_active_profile()[1]['base_url']}",
            },
            "capture": process_capture,
            "lifecycle": lifecycle,
            "workspace": {"before": before, "after": after},
            "verdict": adapter_verdict,
            "outcome": task_outcome,
            "oracle_evidence": oracle_evidence,
        }
