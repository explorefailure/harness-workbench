#!/usr/bin/env python3
"""Generic Pi JSON-mode adapter for Harness Workbench workloads."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from normalizer import StreamError, normalize_jsonl


CONFIG_SCHEMA = "pi-hwb-adapter-config/v0.1"
RUN_SCHEMA = "pi-hwb-adapter-run/v0.1"
CAPTURE_LIMIT_KEYS = {"stdout_bytes", "stderr_bytes", "evidence_bytes"}


class AdapterError(ValueError):
    """The adapter request cannot be executed without guessing."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_manifest(root: Path) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        manifest.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": len(data),
                "mode": stat.S_IMODE(path.stat().st_mode),
                "sha256": sha256_bytes(data),
            }
        )
    return manifest


def command_output(argv: list[str], *, timeout: float = 5.0) -> str:
    result = subprocess.run(
        argv,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
    )
    return result.stdout.strip()


def package_tree_digest(root: Path) -> tuple[int, str]:
    """Hash Pi-owned installed files; npm-shrinkwrap binds dependencies."""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "node_modules" in relative.parts or not path.is_file() or path.is_symlink():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
        count += 1
    return count, digest.hexdigest()


def verify_pi_install(pi_path: Path, pin: dict[str, Any]) -> dict[str, Any]:
    launcher = pi_path.resolve(strict=True)
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
        "package_json_sha256": sha256_file(package_json_path),
        "npm_shrinkwrap_sha256": sha256_file(shrinkwrap_path),
        "launcher_sha256": sha256_file(launcher),
    }
    file_count, tree_digest = package_tree_digest(package_root)
    identities["package_tree_file_count"] = file_count
    identities["package_tree_sha256"] = tree_digest

    expected = {
        "package_json_sha256": pin["package_json_sha256"],
        "npm_shrinkwrap_sha256": pin["npm_shrinkwrap_sha256"],
        "launcher_sha256": pin["launcher_sha256"],
        "package_tree_sha256": pin["package_tree_sha256"],
    }
    mismatches = [key for key, value in expected.items() if identities[key] != value]
    if mismatches:
        raise AdapterError("installed Pi identity mismatch: " + ", ".join(mismatches))
    return identities


def _regular_relative(base: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise AdapterError(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise AdapterError(f"{label} must stay below the config directory")
    path = base / relative
    if not path.is_file() or path.is_symlink():
        raise AdapterError(f"{label} is not a regular file: {value}")
    return path


def _directory_relative(base: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise AdapterError(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise AdapterError(f"{label} must stay below the config directory")
    path = base / relative
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
    }
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise AdapterError("unknown adapter config field(s): " + ", ".join(unknown))
    for key in ("inputs", "extensions", "pi_arguments"):
        if not isinstance(config.get(key), list) or not all(
            isinstance(item, str) and item for item in config[key]
        ):
            raise AdapterError(f"adapter config {key!r} must be a string list")
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


def minimal_environment(retained_root: Path, overrides: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for name in ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "SYSTEMROOT"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    fake_home = retained_root / "home"
    config_dir = retained_root / "pi-config"
    fake_home.mkdir()
    config_dir.mkdir()
    env.update(
        {
            "HOME": str(fake_home),
            "PI_CODING_AGENT_DIR": str(config_dir),
            "PI_OFFLINE": "1",
        }
    )
    env.update(overrides)
    return env


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(process_group_id: int, signum: int) -> None:
    try:
        os.killpg(process_group_id, signum)
    except ProcessLookupError:
        pass


def _wait_for_group_exit(process_group_id: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _process_group_exists(process_group_id) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not _process_group_exists(process_group_id)


def run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    evidence_root: Path,
    stdout_limit: int = 8 * 1024 * 1024,
    stderr_limit: int = 1024 * 1024,
) -> dict[str, Any]:
    stdout_path = evidence_root / "pi-stdout.jsonl"
    stderr_path = evidence_root / "pi-stderr.bin"
    timed_out = False
    output_limit_exceeded: list[str] = []
    received_signals: list[int] = []
    pre_cleanup_group_alive = False
    post_cleanup_group_alive = False
    process: subprocess.Popen[bytes] | None = None
    previous_handlers: dict[int, Any] = {}

    def forward_signal(signum: int, _frame: Any) -> None:
        received_signals.append(signum)
        if process is not None:
            _signal_group(process.pid, signal.SIGTERM)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, forward_signal)

    try:
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                exceeded = []
                if stdout_path.stat().st_size > stdout_limit:
                    exceeded.append("stdout")
                if stderr_path.stat().st_size > stderr_limit:
                    exceeded.append("stderr")
                if exceeded:
                    output_limit_exceeded.extend(exceeded)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.02)
            if timed_out or output_limit_exceeded:
                _signal_group(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    _signal_group(process.pid, signal.SIGKILL)
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
    finally:
        if process is not None:
            pre_cleanup_group_alive = _process_group_exists(process.pid)
            if pre_cleanup_group_alive:
                _signal_group(process.pid, signal.SIGTERM)
                if not _wait_for_group_exit(process.pid, 1.0):
                    _signal_group(process.pid, signal.SIGKILL)
                    _wait_for_group_exit(process.pid, 1.0)
            post_cleanup_group_alive = _process_group_exists(process.pid)
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    stdout_size = stdout_path.stat().st_size
    stderr_size = stderr_path.stat().st_size
    if stdout_size > stdout_limit:
        output_limit_exceeded.append("stdout")
    if stderr_size > stderr_limit:
        output_limit_exceeded.append("stderr")
    return {
        "returncode": process.returncode if process is not None else None,
        "timed_out": timed_out,
        "output_limit_exceeded": sorted(set(output_limit_exceeded)),
        "received_signals": received_signals,
        "pre_cleanup_group_alive": pre_cleanup_group_alive,
        "post_cleanup_group_alive": post_cleanup_group_alive,
        "stdout_size": stdout_size,
        "stdout_sha256": sha256_file(stdout_path),
        "stdout": stdout_path.read_bytes() if stdout_size <= stdout_limit else None,
        "stderr_size": stderr_size,
        "stderr_sha256": sha256_file(stderr_path),
        "stderr": stderr_path.read_bytes() if stderr_size <= stderr_limit else None,
    }


def _evidence_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise AdapterError("evidence path must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise AdapterError("evidence path must stay below the retained root")
    return root / relative


def capture_evidence_file(
    path: Path, *, required: bool, format_name: str, max_bytes: int = 1024 * 1024
) -> dict[str, Any]:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise AdapterError("evidence max_bytes must be a positive integer")
    errors: list[str] = []
    if not path.exists():
        if required:
            errors.append("required evidence file was not created")
        raw = b""
        exists = False
    elif not path.is_file() or path.is_symlink():
        errors.append("evidence path is not a regular file")
        raw = b""
        exists = True
    else:
        exists = True
        size = path.stat().st_size
        if size > max_bytes:
            errors.append(f"evidence exceeds {max_bytes}-byte capture limit: {size} bytes")
            raw = None
        else:
            raw = path.read_bytes()
    regular = exists and path.is_file() and not path.is_symlink()
    size = path.stat().st_size if regular else 0
    raw_sha256 = sha256_file(path) if regular else sha256_bytes(b"")
    text: str | None = None
    records: list[Any] | None = None
    if exists and not errors and raw is not None:
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            if format_name in {"utf8", "jsonl"}:
                errors.append(f"evidence is not UTF-8: {error}")
        if format_name == "jsonl" and text is not None:
            records = []
            for line_number, line in enumerate(text.splitlines(), 1):
                try:
                    records.append(json.loads(line))
                except ValueError as error:
                    errors.append(f"invalid JSONL at line {line_number}: {error}")
    return {
        "exists": exists,
        "format": format_name,
        "size": size,
        "max_bytes": max_bytes,
        "raw_base64": (
            base64.b64encode(raw).decode("ascii") if raw is not None else None
        ),
        "raw_sha256": raw_sha256,
        "utf8": text,
        "jsonl": records,
        "errors": errors,
    }


def generic_errors(
    process: dict[str, Any],
    summary: dict[str, Any] | None,
    normalization_error: str | None,
    evidence: dict[str, dict[str, Any]],
    extension_errors: list[dict[str, str]],
) -> list[str]:
    errors: list[str] = []
    if process["timed_out"]:
        errors.append("Pi exceeded the adapter timeout")
    if process["output_limit_exceeded"]:
        errors.append(
            "Pi exceeded adapter capture limit(s): "
            + ", ".join(process["output_limit_exceeded"])
        )
    if process["received_signals"]:
        errors.append(f"adapter received signals {process['received_signals']}")
    if process["post_cleanup_group_alive"]:
        errors.append("Pi process group survived bounded cleanup")
    if process["pre_cleanup_group_alive"] and not process["timed_out"]:
        errors.append("Pi left a live process-group member after its parent exited")
    if process["returncode"] != 0:
        errors.append(f"Pi exited with status {process['returncode']}")
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
        str(path): path.relative_to(base).as_posix() for path in extension_paths
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
    missing_inputs = sorted(required_inputs - set(inputs))
    if missing_inputs:
        raise AdapterError(
            "adapter config omitted consumed input(s): " + ", ".join(missing_inputs)
        )
    input_digests = {name: sha256_file(path) for name, path in inputs.items()}
    pin = json.loads(pin_path.read_text(encoding="utf-8"))
    pi_resolved = shutil.which(pi_name)
    if pi_resolved is None:
        raise AdapterError("Pi executable not found")
    pi_path = Path(pi_resolved)
    try:
        pi_version = command_output([str(pi_path), "--version"])
        node_version = command_output(["node", "--version"]).removeprefix("v")
        install_identity = verify_pi_install(pi_path, pin)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
        raise AdapterError(str(error)) from error
    if pi_version != pin["version"]:
        raise AdapterError(
            f"Pi version {pi_version!r} does not match {pin['version']!r}"
        )
    if node_version != pin["node_version"]:
        raise AdapterError(
            f"Node version {node_version!r} does not match {pin['node_version']!r}"
        )

    retained_root = Path(
        tempfile.mkdtemp(prefix="hwb-pi-adapter-", dir=workspace_parent)
    ).resolve()
    workspace = retained_root / "workspace"
    shutil.copytree(fixture_path, workspace)
    shutil.copy2(task_path, workspace / task_path.name)
    before = file_manifest(workspace)

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
        path = _evidence_path(retained_root, raw_spec.get("path"))
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

    pi_argv = [str(pi_path), *config["pi_arguments"]]
    for extension_path in extension_paths:
        pi_argv.extend(["-e", str(extension_path)])
    pi_argv.extend(["--print", "@" + task_path.name])
    process = run_bounded(
        pi_argv,
        cwd=workspace,
        env=minimal_environment(retained_root, overrides),
        timeout=timeout,
        evidence_root=retained_root,
        stdout_limit=config["capture_limits"]["stdout_bytes"],
        stderr_limit=config["capture_limits"]["stderr_bytes"],
    )
    after = file_manifest(workspace)

    stdout_text: str | None = None
    stderr_text: str | None = None
    summary: dict[str, Any] | None = None
    normalization_error: str | None = None
    try:
        if process["stdout"] is None:
            raise StreamError("stdout exceeded its configured capture limit")
        stdout_text = process["stdout"].decode("utf-8", errors="strict")
        summary = normalize_jsonl(stdout_text)
    except (UnicodeDecodeError, StreamError) as error:
        normalization_error = str(error)
    try:
        if process["stderr"] is not None:
            stderr_text = process["stderr"].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass
    extension_errors = parse_extension_errors(stderr_text, extension_paths, base)

    evidence: dict[str, dict[str, Any]] = {}
    for name, spec in evidence_specs.items():
        item = capture_evidence_file(
            spec["path"],
            required=spec["required"],
            format_name=spec["format"],
            max_bytes=spec["max_bytes"],
        )
        evidence[name] = {
            **item,
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
    return {
        "schema": RUN_SCHEMA,
        "verdict": {"passed": not errors, "errors": errors},
        "configuration": {
            "config": config_path.name,
            "config_sha256": sha256_file(config_path),
            "input_digests": input_digests,
            "extension_inputs": [path.relative_to(base).as_posix() for path in extension_paths],
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
        },
        "evidence": evidence,
        "pi": {
            "argv": pi_argv,
            "returncode": process["returncode"],
            "timed_out": process["timed_out"],
            "output_limit_exceeded": process["output_limit_exceeded"],
            "received_signals": process["received_signals"],
            "pre_cleanup_group_alive": process["pre_cleanup_group_alive"],
            "post_cleanup_group_alive": process["post_cleanup_group_alive"],
            "stdout_size": process["stdout_size"],
            "stdout_base64": (
                base64.b64encode(process["stdout"]).decode("ascii")
                if process["stdout"] is not None
                else None
            ),
            "stdout_jsonl": stdout_text,
            "stdout_sha256": process["stdout_sha256"],
            "stderr_size": process["stderr_size"],
            "stderr_base64": (
                base64.b64encode(process["stderr"]).decode("ascii")
                if process["stderr"] is not None
                else None
            ),
            "stderr_utf8": stderr_text,
            "stderr_sha256": process["stderr_sha256"],
            "extension_errors": extension_errors,
            "summary": summary,
            "summary_sha256": sha256_bytes(summary_bytes),
        },
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("config", type=Path)
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
        envelope = capture(
            args.config,
            pi_name=args.pi,
            timeout=args.timeout,
            workspace_parent=args.workspace_parent,
        )
    except (AdapterError, OSError, KeyError, TypeError) as error:
        return failure_envelope(str(error))
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return 0 if envelope["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
