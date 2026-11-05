#!/usr/bin/env python3
"""Generic Pi JSON-mode adapter for Harness Workbench workloads."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from harness_workbench.capture import (
    Bounded,
    CaptureError,
    capture_bytes,
    capture_file,
    contained_path,
    digest_bytes,
    digest_file,
    manifest,
    minimal_environment,
    run_bounded,
)

from normalizer import StreamError, normalize_jsonl


CONFIG_SCHEMA = "pi-hwb-adapter-config/v0.1"
RUN_SCHEMA = "pi-hwb-adapter-run/v0.1"
CAPTURE_LIMIT_KEYS = {"stdout_bytes", "stderr_bytes", "evidence_bytes"}


class AdapterError(ValueError):
    """The adapter request cannot be executed without guessing."""


def command_output(argv: list[str], *, cwd: Path, timeout: float = 5.0) -> str:
    """Read one short line from a pinned executable, under the same bounds.

    A version probe looks harmless enough to run with plain `subprocess.run`,
    which is how it was written first. But `subprocess.run`'s timeout kills the
    process and not its group, so a launcher that forks leaks exactly the
    orphan the adapter later reports as clean. The purpose is pin verification,
    which stays adapter-local; the mechanism is the primitive's, so it uses it.
    """
    result = run_bounded(
        argv,
        cwd=cwd,
        env=dict(os.environ),
        timeout=timeout,
        stdout_limit=64 * 1024,
        stderr_limit=64 * 1024,
        termination_grace=1.0,
    )
    if result.returncode != 0 or result.termination_reason is not None:
        raise AdapterError(
            f"{argv[0]} probe failed: status {result.returncode}"
            f" ({result.termination_reason or 'no bound fired'})"
        )
    try:
        return result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise AdapterError(f"{argv[0]} probe output is not UTF-8: {error}") from error


def package_tree_digest(root: Path) -> tuple[int, str]:
    """Hash Pi-owned installed files; npm-shrinkwrap binds dependencies."""
    parts: list[bytes] = []
    count = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "node_modules" in relative.parts or not path.is_file() or path.is_symlink():
            continue
        parts.append(relative.as_posix().encode("utf-8"))
        parts.append(b"\0")
        parts.append(bytes.fromhex(digest_file(path)))
        count += 1
    return count, digest_bytes(b"".join(parts))


def verify_pi_install(pi_path: Path, pin: dict[str, Any]) -> dict[str, Any]:
    launcher = pi_path.resolve(strict=True)
    bundled = pin.get("schema") == "pi-hwb-pin/v0.2"
    if bundled:
        if launcher.name != "cli.js" or launcher.parent.name != "bundle" or launcher.parent.parent.name != "dist":
            raise AdapterError(f"Pi bundled launcher has unexpected installed shape: {launcher}")
        package_root = launcher.parents[2]
    else:
        if launcher.name != "cli.js" or launcher.parent.name != "dist":
            raise AdapterError(f"Pi launcher has unexpected installed shape: {launcher}")
        package_root = launcher.parent.parent
    package_json_path = package_root / "package.json"
    shrinkwrap_path = package_root / "npm-shrinkwrap.json"
    package = json.loads(package_json_path.read_text(encoding="utf-8"))
    if package.get("name") != pin["npm_package"]:
        raise AdapterError(f"installed package name is {package.get('name')!r}")
    if package.get("version") != pin["version"]:
        raise AdapterError(f"installed package version is {package.get('version')!r}")

    identities = {
        "package_root": str(package_root),
        "package_name": package["name"],
        "package_version": package["version"],
        "package_json_sha256": digest_file(package_json_path),
        **({"installation_lock_sha256": digest_file(package_root.parents[2] / "package-lock.json")}
           if bundled else {"npm_shrinkwrap_sha256": digest_file(shrinkwrap_path)}),
        "launcher_sha256": digest_file(launcher),
    }
    file_count, tree_digest = package_tree_digest(package_root)
    identities["package_tree_file_count"] = file_count
    identities["package_tree_sha256"] = tree_digest

    expected = {
        "package_json_sha256": pin["package_json_sha256"],
        **({"installation_lock_sha256": pin["installation_lock_sha256"]}
           if bundled else {"npm_shrinkwrap_sha256": pin["npm_shrinkwrap_sha256"]}),
        "launcher_sha256": pin["launcher_sha256"],
        "package_tree_sha256": pin["package_tree_sha256"],
    }
    mismatches = [key for key, value in expected.items() if identities[key] != value]
    if mismatches:
        raise AdapterError("installed Pi identity mismatch: " + ", ".join(mismatches))
    return identities


def _contained(base: Path, value: Any, label: str) -> Path:
    """Containment is the primitive's; what kind of file it must be is ours."""
    try:
        return contained_path(base, value, label=label)
    except CaptureError as error:
        raise AdapterError(str(error)) from error


def _regular_relative(base: Path, value: Any, label: str) -> Path:
    path = _contained(base, value, label)
    if not path.is_file() or path.is_symlink():
        raise AdapterError(f"{label} is not a regular file: {value}")
    return path


def _directory_relative(base: Path, value: Any, label: str) -> Path:
    path = _contained(base, value, label)
    if not path.is_dir() or path.is_symlink():
        raise AdapterError(f"{label} is not a real directory: {value}")
    return path


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AdapterError(f"could not load adapter config: {error}") from error
    if not isinstance(config, dict) or config.get("schema") != CONFIG_SCHEMA:
        raise AdapterError(f"adapter config must use schema {CONFIG_SCHEMA!r}")
    allowed = {
        "schema",
        "pin",
        "fixture",
        "task",
        "inputs",
        "extensions",
        "pi_arguments",
        "network_mode",
        "capture_limits",
        "provider",
        "checks",
        "controls",
    }
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise AdapterError("unknown adapter config field(s): " + ", ".join(unknown))
    if 'controls' in config:
        from harness_workbench._adapter_controls import validate as validate_controls
        try:
            if config['controls'] is None:
                raise ValueError('controls must be an object when present')
            validate_controls(config['controls'], 'pi')
        except ValueError as error:
            raise AdapterError(str(error)) from error
    for key in ("inputs", "extensions", "pi_arguments"):
        if not isinstance(config.get(key), list) or not all(
            isinstance(item, str) and item for item in config[key]
        ):
            raise AdapterError(f"adapter config {key!r} must be a string list")
    if 'provider' in config:
        from provider_config import validate
        try:
            validate(config['provider'])
        except ValueError as error:
            raise AdapterError(str(error)) from error
        if config['extensions']:
            raise AdapterError('explicit provider mode does not load extensions')
        conflicts = {'--provider', '--model', '--api-key', '--models', '--list-models'}
        if any(arg.split('=')[0] in conflicts for arg in config['pi_arguments']):
            raise AdapterError('provider/model/auth selection belongs in the provider field')
    if 'checks' in config:
        from task_checks import validate
        try:validate(config['checks'])
        except ValueError as error:raise AdapterError(str(error)) from error
    mode_values = [
        config["pi_arguments"][index + 1]
        for index, value in enumerate(config["pi_arguments"][:-1])
        if value == "--mode"
    ]
    if mode_values != ["json"]:
        raise AdapterError("adapter config must request exactly one '--mode json'")
    for adapter_owned in ("--print", "-e", "--extension"):
        if adapter_owned in config["pi_arguments"]:
            raise AdapterError(
                f"adapter config must use the dedicated field instead of {adapter_owned!r}"
            )
    if not isinstance(config.get("network_mode"), str) or not config["network_mode"]:
        raise AdapterError("adapter config network_mode must be a non-empty string")
    limits = config.get("capture_limits")
    if not isinstance(limits, dict) or set(limits) != CAPTURE_LIMIT_KEYS:
        raise AdapterError(
            "adapter config capture_limits must contain exactly: "
            + ", ".join(sorted(CAPTURE_LIMIT_KEYS))
        )
    for key, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AdapterError(f"capture limit {key!r} must be a positive integer")
    if config.get("controls", {}).get("values", {}).get("tool_call_extension") is not None:
        _regular_relative(path.parent, "control_runtime.py", "control runtime helper")
        from control_runtime import validate_profile
        try:
            validate_profile(config, path)
        except ValueError as error:
            raise AdapterError(str(error)) from error
    return config


def _unique_relative_inputs(base: Path, values: list[str]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for value in values:
        path = _regular_relative(base, value, f"input {value!r}")
        normalized = path.relative_to(base).as_posix()
        if normalized in paths:
            raise AdapterError(f"duplicate adapter input {normalized!r}")
        paths[normalized] = path
    return paths


def _fixture_files(base: Path, fixture: Path) -> set[str]:
    files: set[str] = set()
    for path in fixture.rglob("*"):
        if path.is_symlink():
            raise AdapterError("fixture may not contain symbolic links")
        if path.is_dir():
            continue
        if not path.is_file():
            raise AdapterError(f"fixture contains unsupported path type: {path}")
        files.add(path.relative_to(base).as_posix())
    return files


def pi_environment(retained_root: Path, overrides: dict[str, str]) -> dict[str, str]:
    """The primitive's allowlist plus the three variables that are Pi's.

    `PI_OFFLINE` and a private `PI_CODING_AGENT_DIR` are exactly the kind of
    per-subject knowledge that must not live in core: another harness has its
    own names and its own offline switch, and a shared default would be wrong
    for every subject but one.
    """
    config_dir = retained_root / "pi-config"
    config_dir.mkdir(exist_ok=True)
    return minimal_environment(
        retained_root,
        {
            "PI_CODING_AGENT_DIR": str(config_dir),
            "PI_OFFLINE": "1",
            **overrides,
        },
    )


def _limits_exceeded(process: Bounded) -> list[str]:
    exceeded = []
    if process.stdout_overflow:
        exceeded.append("stdout")
    if process.stderr_overflow:
        exceeded.append("stderr")
    return exceeded


def generic_errors(
    process: Bounded,
    summary: dict[str, Any] | None,
    normalization_error: str | None,
    evidence: dict[str, dict[str, Any]],
    extension_errors: list[dict[str, str]],
) -> list[str]:
    errors: list[str] = []
    if process.timed_out:
        errors.append("Pi exceeded the adapter timeout")
    if _limits_exceeded(process):
        errors.append(
            "Pi exceeded adapter capture limit(s): "
            + ", ".join(_limits_exceeded(process))
        )
    if process.forwarded_signals:
        errors.append(f"adapter received signals {list(process.forwarded_signals)}")
    if process.group_alive_after_cleanup:
        errors.append("Pi process group survived bounded cleanup")
    if process.group_alive_before_cleanup and not process.timed_out:
        errors.append("Pi left a live process-group member after its parent exited")
    if process.returncode != 0:
        errors.append(f"Pi exited with status {process.returncode}")
    if normalization_error:
        errors.append(f"normalizer: {normalization_error}")
    elif summary is None:
        errors.append("Pi stdout could not be normalized")
    elif not summary["valid"]:
        errors.extend(f"stream: {error}" for error in summary["errors"])
    for item in extension_errors:
        errors.append(
            f"Pi extension {item['extension']} failed: {item['error']}"
        )
    for name, item in evidence.items():
        errors.extend(f"evidence {name}: {error}" for error in item["errors"])
    return errors


def parse_extension_errors(
    stderr_text: str | None,
    extension_paths: list[Path],
    base: Path,
) -> list[dict[str, str]]:
    """Project Pi print-mode extension errors without retaining host paths."""
    if stderr_text is None:
        return []
    known = {
        str(path): (path.relative_to(base).as_posix() if path.is_relative_to(base) else "<owned-control-wrapper>") for path in extension_paths
    }
    records: list[dict[str, str]] = []
    prefix = "Extension error ("
    separator = "): "
    for line in stderr_text.splitlines():
        if not line.startswith(prefix) or separator not in line:
            continue
        raw_path, error = line[len(prefix):].split(separator, 1)
        records.append({
            "schema": "pi-hwb-extension-error/v0.1",
            "extension": known.get(raw_path, "<unbound>"),
            "error": error,
        })
    return records


def check_runtime(config_path: Path, pi_name: str = "pi") -> dict[str, Any]:
    """Check the declared runtime without executing the workload."""
    config_path = config_path.resolve()
    base = config_path.parent
    config = load_config(config_path)
    pin_path = _regular_relative(base, config.get("pin"), "pin")
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    pi_resolved = shutil.which(pi_name)
    if pi_resolved is None:
        raise AdapterError("Pi executable not found")
    pi_path = Path(pi_resolved)
    try:
        # Reject altered Pi code before invoking even its version command.
        install_identity = verify_pi_install(pi_path, pin)
        pi_version = command_output([str(pi_path), "--version"], cwd=base)
        node_version = command_output(["node", "--version"], cwd=base).removeprefix("v")
    except (OSError, ValueError, KeyError) as error:
        raise AdapterError(str(error)) from error
    if pi_version != pin["version"]:
        raise AdapterError(
            f"Pi version {pi_version!r} does not match {pin['version']!r}"
        )
    if node_version != pin["node_version"]:
        raise AdapterError(
            f"Node version {node_version!r} does not match {pin['node_version']!r}"
        )

    return {"pi_path": str(pi_path), "pi_version": pi_version,
            "node_version": node_version, "install_identity": install_identity}


def capture(
    config_path: Path,
    *,
    pi_name: str = "pi",
    timeout: float = 20.0,
    workspace_parent: str | None = None,
    environment: dict[str, str] | None = None,
    additional_extensions: tuple[str, ...] = (),
    additional_inputs: tuple[str, ...] = (),
    evidence_files: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    config_path = config_path.resolve()
    base = config_path.parent
    config = load_config(config_path)
    pin_path = _regular_relative(base, config.get("pin"), "pin")
    fixture_path = _directory_relative(base, config.get("fixture"), "fixture")
    task_path = _regular_relative(base, config.get("task"), "task")
    control = config.get('controls', {}).get('values', {}).get('tool_call_extension')
    if control is not None and (additional_extensions or evidence_files or environment):
        raise AdapterError('tool_call_extension does not accept additional extension/evidence/environment overrides')
    input_values = [*config["inputs"], *additional_inputs]
    inputs = _unique_relative_inputs(base, input_values)
    if config_path.relative_to(base).as_posix() not in inputs:
        raise AdapterError("adapter config must declare itself as an input")
    extensions = [*config["extensions"], *additional_extensions]
    extension_paths = [
        _regular_relative(base, value, f"extension {value!r}") for value in extensions
    ]
    required_inputs = {
        config_path.relative_to(base).as_posix(),
        pin_path.relative_to(base).as_posix(),
        task_path.relative_to(base).as_posix(),
        *(path.relative_to(base).as_posix() for path in extension_paths),
        *_fixture_files(base, fixture_path),
    }
    if 'provider' in config:
        required_inputs.update({'adapter.py', 'normalizer.py', 'provider_config.py'})
    if 'checks' in config:required_inputs.add('task_checks.py')
    missing_inputs = sorted(required_inputs - set(inputs))
    if missing_inputs:
        raise AdapterError(
            "adapter config omitted consumed input(s): " + ", ".join(missing_inputs)
        )
    input_digests = {name: digest_file(path) for name, path in inputs.items()}
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    runtime = check_runtime(config_path, pi_name)
    pi_path = Path(runtime["pi_path"])
    pi_version = runtime["pi_version"]
    node_version = runtime["node_version"]
    install_identity = runtime["install_identity"]

    retained_root = Path(
        tempfile.mkdtemp(prefix="hwb-pi-adapter-", dir=workspace_parent)
    ).resolve()
    workspace = retained_root / "workspace"
    shutil.copytree(fixture_path, workspace)
    shutil.copy2(task_path, workspace / task_path.name)
    before = manifest(workspace)

    control_binding = None
    if control is not None:
        from control_runtime import render
        wrapper, control_config, control_binding = render(config, base, retained_root)
        extension_paths.append(wrapper)
        evidence_files = ({'name':'tool_call_control', 'path':'control.trace.jsonl',
                           'format':'jsonl','required':True,
                           'environment_variable':'HWB_PI_CONTROL_TRACE'},)
    overrides = dict(environment or {})
    if not all(
        isinstance(name, str)
        and name
        and isinstance(value, str)
        for name, value in overrides.items()
    ):
        raise AdapterError("environment overrides must map non-empty names to strings")
    adapter_owned_environment = {"HOME", "PI_CODING_AGENT_DIR", "PI_OFFLINE"}
    reserved_environment = adapter_owned_environment & set(overrides)
    if reserved_environment:
        raise AdapterError(
            "caller may not override adapter-owned environment: "
            + ", ".join(sorted(reserved_environment))
        )
    evidence_specs: dict[str, dict[str, Any]] = {}
    evidence_paths: set[Path] = set()
    reserved_evidence_paths = {
        retained_root / "pi-stdout.jsonl",
        retained_root / "pi-stderr.bin",
    }
    for raw_spec in evidence_files:
        if not isinstance(raw_spec, dict):
            raise AdapterError("evidence specification must be an object")
        allowed_spec = {
            "name",
            "path",
            "format",
            "required",
            "environment_variable",
            "max_bytes",
        }
        unknown_spec = sorted(set(raw_spec) - allowed_spec)
        if unknown_spec:
            raise AdapterError(
                "unknown evidence specification field(s): "
                + ", ".join(unknown_spec)
            )
        name = raw_spec.get("name")
        if not isinstance(name, str) or not name or name in evidence_specs:
            raise AdapterError("evidence names must be unique non-empty strings")
        path = _contained(retained_root, raw_spec.get("path"), "evidence path")
        if path in evidence_paths or path in reserved_evidence_paths:
            raise AdapterError(f"duplicate or reserved evidence path: {path.name}")
        evidence_paths.add(path)
        format_name = raw_spec.get("format", "binary")
        if format_name not in {"binary", "utf8", "jsonl"}:
            raise AdapterError(f"unsupported evidence format {format_name!r}")
        max_bytes = raw_spec.get(
            "max_bytes", config["capture_limits"]["evidence_bytes"]
        )
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or max_bytes > config["capture_limits"]["evidence_bytes"]
        ):
            raise AdapterError(
                "evidence max_bytes must be a positive integer no larger than "
                "capture_limits.evidence_bytes"
            )
        environment_variable = raw_spec.get("environment_variable")
        if environment_variable is not None:
            if not isinstance(environment_variable, str) or not environment_variable:
                raise AdapterError("evidence environment variable must be a string")
            if environment_variable in overrides:
                raise AdapterError(
                    f"evidence environment variable {environment_variable!r} conflicts"
                )
            if environment_variable in adapter_owned_environment:
                raise AdapterError(
                    f"evidence may not replace {environment_variable!r}"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            overrides[environment_variable] = str(path)
        evidence_specs[name] = {
            "path": path,
            "relative_path": path.relative_to(retained_root).as_posix(),
            "required": raw_spec.get("required") is True,
            "format": format_name,
            "max_bytes": max_bytes,
            "environment_variable": environment_variable,
        }

    provider_secrets = []
    provider_env = {}
    provider_name = None
    if 'provider' in config:
        if pi_version != '1.4.0':
            raise AdapterError('explicit provider selection requires the Pi 1.4.0 profile')
        from provider_config import prepare
        try:
            provider_name, provider_env, provider_secrets = prepare(config['provider'], retained_root / 'pi-config')
        except ValueError as error:
            raise AdapterError(str(error)) from error
        if set(provider_env) & set(overrides):
            raise AdapterError('caller environment conflicts with selected provider credential')
    pi_argv = [str(pi_path), *config["pi_arguments"]]
    if provider_name:
        pi_argv.extend(['--provider', provider_name, '--model', config['provider']['model']])
    for extension_path in extension_paths:
        pi_argv.extend(["-e", str(extension_path)])
    pi_argv.extend(["--print", "@" + task_path.name])
    if control is not None:
        overrides['HWB_PI_CONTROL_CONFIG'] = str(control_config)
        provider_binding = digest_file(retained_root / 'pi-config' / 'models.json')
    child_env = pi_environment(retained_root, {**overrides, **provider_env})
    process = run_bounded(
        pi_argv,
        cwd=workspace,
        env=child_env,
        timeout=timeout,
        stdout_limit=config["capture_limits"]["stdout_bytes"],
        stderr_limit=config["capture_limits"]["stderr_bytes"],
    )
    after = manifest(workspace)

    stdout_text: str | None = None
    stderr_text: str | None = None
    summary: dict[str, Any] | None = None
    normalization_error: str | None = None
    # Overflow, not a None stream, is now the signal that stdout is unusable:
    # the primitive truncates at the limit and reports how much it discarded,
    # so a saturated run still yields the prefix that shows what went wrong.
    stdout_capture = capture_bytes(
        process.stdout, source_bytes=process.stdout_source_bytes, redactions=provider_secrets
    )
    stderr_capture = capture_bytes(
        process.stderr, source_bytes=process.stderr_source_bytes, redactions=provider_secrets
    )
    try:
        if process.stdout_overflow:
            raise StreamError("stdout exceeded its configured capture limit")
        if stdout_capture["text"] is None:
            raise StreamError("stdout is not valid UTF-8")
        stdout_text = stdout_capture["text"]
        summary = (normalize_jsonl(stdout_text, system_messages=True)
                   if pin.get("schema") == "pi-hwb-pin/v0.2" and pi_version == "1.4.0"
                   else normalize_jsonl(stdout_text))
    except StreamError as error:
        normalization_error = str(error)
    if not process.stderr_overflow:
        stderr_text = stderr_capture["text"]
    extension_errors = parse_extension_errors(stderr_text, extension_paths, base)

    evidence: dict[str, dict[str, Any]] = {}
    for name, spec in evidence_specs.items():
        item = capture_file(
            spec["path"],
            required=spec["required"],
            format_name=spec["format"],
            max_bytes=spec["max_bytes"],
            redactions=provider_secrets,
        )
        # The adapter owns its wire names; the primitive owns the evidence.
        # `raw_sha256` is null for a file that was never created, where it used
        # to be the digest of empty bytes -- which made "no evidence" and "empty
        # evidence" identical, the exact confusion this schema exists to avoid.
        evidence[name] = {
            "exists": item["exists"],
            "format": item["format"],
            "size": item["size"],
            "max_bytes": item["max_bytes"],
            "raw_base64": item["base64"],
            "raw_sha256": item["file_sha256"],
            "utf8": item["text"],
            "jsonl": item["jsonl"],
            "errors": item["errors"],
            "path": spec["relative_path"],
            "environment_variable": spec["environment_variable"],
        }
    errors = generic_errors(
        process, summary, normalization_error, evidence, extension_errors
    )
    summary_bytes = (
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if summary is not None
        else b""
    )
    task_results = None
    if 'checks' in config:
        from task_checks import run
        task_results = run(config['checks'], workspace, minimal_environment(retained_root / 'check-home', {'PYTHONDONTWRITEBYTECODE':'1'}), provider_secrets)
    final_input_digests = {}
    for name in inputs:
        try:
            final_input_digests[name] = digest_file(_regular_relative(base, name, 'input'))
        except (OSError, ValueError):
            final_input_digests[name] = None
    if final_input_digests != input_digests:
        errors.append('declared source inputs changed during capture')
    control_metadata = {}
    if "controls" in config:
        from harness_workbench._adapter_controls import evidence as control_evidence
        control_metadata["controls"] = control_evidence(config["controls"])
    if control is not None:
        from control_runtime import measure
        measured = measure(control, evidence['tool_call_control'], summary, before, after,
                           manifest(workspace), workspace)
        control_metadata['controls'].update(measured)
        control_metadata['controls']['rendered'] = {
            'state':'rendered', 'profile':'pi-local-tool-call/v1', 'bound_files':control_binding}
        if measured['effect']['errors']:
            errors.extend('control: ' + e for e in measured['effect']['errors'])
        for name, expected in control_binding.items():
            try:
                if digest_file(_regular_relative(retained_root, name, 'control artifact')) != expected:
                    errors.append('rendered control artifact changed: ' + name)
            except (OSError, ValueError):
                errors.append('rendered control artifact missing/unsafe: ' + name)
        try:
            if digest_file(retained_root / 'pi-config' / 'models.json') != provider_binding:
                errors.append('selected provider configuration changed')
        except OSError:
            errors.append('selected provider configuration missing')
        if stdout_text and summary is not None:
            for line in stdout_text.splitlines():
                event = json.loads(line)
                message = event.get('message', {})
                if event.get('type') == 'message_end' and isinstance(message, dict) and message.get('role') == 'assistant':
                    if message.get('model') != config['provider']['model'] or message.get('provider') != provider_name:
                        errors.append('observed assistant provider/model differs from selected configuration')
    return {
        **({'task_checks': task_results} if task_results is not None else {}),
        "schema": RUN_SCHEMA,
        **control_metadata,
        "verdict": {"passed": not errors, "errors": errors},
        "configuration": {
            **({"provider": config["provider"]} if "provider" in config else {}),
            "config": config_path.name,
            "config_sha256": input_digests[config_path.name],
            "input_digests": input_digests,
            "input_digests_after": final_input_digests,
            "extension_inputs": [path.relative_to(base).as_posix() if path.is_relative_to(base) else "<owned-control-wrapper>" for path in extension_paths],
            "environment_names": sorted(overrides),
            "network_mode": config["network_mode"],
            "capture_limits": config["capture_limits"],
        },
        "pin": pin,
        "runtime": {
            "python_executable": sys.executable,
            "python_version": ".".join(str(value) for value in sys.version_info[:3]),
            "pi_executable": str(pi_path),
            "pi_version": pi_version,
            "node_version": node_version,
            "pi_install": install_identity,
        },
        "isolation": {
            "network_mode": config["network_mode"],
            "environment_policy": "minimal allowlist plus caller-named variables",
            "environment_value_policy": "values are supplied to Pi but not recorded",
            "session_policy": "empty private Pi config; session behavior is argv-controlled",
            "process_policy": "bounded POSIX process group; escaped sessions are not observed",
            "sandbox": False,
        },
        "workspace": {
            "retained_root": str(retained_root),
            "before": before,
            "after": after,
            **({"after_checks": manifest(workspace)} if task_results is not None else {}),
        },
        "evidence": evidence,
        "pi": {
            "argv": pi_argv,
            "returncode": process.returncode,
            # The real exit status, never synthesized. `termination_reason`
            # says which bound fired, so a subject that genuinely exits 124 is
            # no longer indistinguishable from one the adapter timed out.
            "termination_reason": process.termination_reason,
            "timed_out": process.timed_out,
            "output_limit_exceeded": _limits_exceeded(process),
            "received_signals": list(process.forwarded_signals),
            "pre_cleanup_group_alive": process.group_alive_before_cleanup,
            "post_cleanup_group_alive": process.group_alive_after_cleanup,
            "stdout_size": process.stdout_source_bytes,
            "stdout_base64": stdout_capture["base64"],
            "stdout_jsonl": stdout_text,
            "stdout_sha256": stdout_capture["sha256"],
            "stderr_size": process.stderr_source_bytes,
            "stderr_base64": stderr_capture["base64"],
            "stderr_utf8": stderr_text,
            "stderr_sha256": stderr_capture["sha256"],
            "extension_errors": extension_errors,
            "summary": summary,
            "summary_sha256": digest_bytes(summary_bytes),
        },
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("config", type=Path)
    result.add_argument("--check-only", action="store_true", help="verify runtime without running the workload")
    result.add_argument("--pi", default="pi")
    result.add_argument("--timeout", type=float, default=20.0)
    result.add_argument("--workspace-parent")
    return result


def failure_envelope(message: str) -> int:
    print(json.dumps({"schema": RUN_SCHEMA, "error": message}, sort_keys=True))
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if sys.version_info < (3, 11):
        return failure_envelope("Python 3.11 or newer is required")
    try:
        if args.check_only:
            print(json.dumps({"schema": "pi-hwb-runtime-check/v0.1", "runtime":
                              check_runtime(args.config, args.pi)}, sort_keys=True))
            return 0
        envelope = capture(
            args.config,
            pi_name=args.pi,
            timeout=args.timeout,
            workspace_parent=args.workspace_parent,
        )
    except (AdapterError, OSError, KeyError, TypeError) as error:
        return failure_envelope(str(error))
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0 if envelope["verdict"]["passed"] and envelope.get("task_checks", {"passed": True})["passed"] and envelope.get("controls", {}).get("effect", {}).get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
