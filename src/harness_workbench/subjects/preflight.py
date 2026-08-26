#!/usr/bin/env python3
"""Load local adapter prerequisites and run the offline five-subject doctor."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
from typing import Any

import adapters
import doctor


SCHEMA = "cross-harness-adapter-preflight-config/v0.1"
DEFAULT_CONFIG = Path("~/.config/hwb/adapter-preflight.json")
DEFAULT_CREDENTIAL = Path("~/.config/hwb/opencode.key")
MAX_CREDENTIAL_BYTES = 16 * 1024
GATEWAY_SUBJECTS = frozenset(adapters.CONFIGURABLE_MODEL_SUBJECTS)


class PreflightError(ValueError):
    """A local prerequisite was unsafe or unusable."""


def _resolved_path(value: str | Path, *, relative_to: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    return path.resolve()


def _literal_path(value: str | Path, *, relative_to: Path | None = None) -> Path:
    """Make a path absolute without resolving its final symlink."""
    path = Path(value).expanduser()
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    return Path(os.path.abspath(path))


def _load_config(path: Path) -> dict[str, str]:
    path = _resolved_path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise PreflightError(f"preflight config is not readable JSON: {path}") from error
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise PreflightError(f"preflight config schema is not recognized: {path}")
    allowed = {"schema", "credential_file", "hermes_root"}
    if not set(payload).issubset(allowed):
        raise PreflightError(f"preflight config contains unknown fields: {path}")
    config = {}
    for name in ("credential_file", "hermes_root"):
        value = payload.get(name)
        if value is not None:
            if not isinstance(value, str) or not value:
                raise PreflightError(f"preflight config {name} is not a path")
            resolver = (
                _literal_path if name == "credential_file" else _resolved_path
            )
            config[name] = str(resolver(value, relative_to=path.parent))
    return config


def _read_private_credential(path: Path) -> str:
    path = _literal_path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PreflightError(
            f"credential file cannot be opened safely: {path}"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PreflightError(
                f"credential source is not a regular file: {path}"
            )
        if metadata.st_uid != os.getuid():
            raise PreflightError(
                f"credential file is not owned by this user: {path}"
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PreflightError(f"credential file must be owner-only: {path}")
        if metadata.st_size > MAX_CREDENTIAL_BYTES:
            raise PreflightError("credential file exceeds the preflight byte limit")
        chunks = []
        remaining = MAX_CREDENTIAL_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_CREDENTIAL_BYTES:
        raise PreflightError("credential file exceeds the preflight byte limit")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PreflightError("credential file is not UTF-8") from error
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise PreflightError("credential file must contain one non-empty line")
    return value


def prepare_environment(
    subjects: tuple[str, ...],
    *,
    config_path: Path,
    credential_file: Path | None = None,
    hermes_root: Path | None = None,
) -> dict[str, Any]:
    if not subjects or any(subject not in doctor.SUBJECTS for subject in subjects):
        raise PreflightError("preflight requires known subjects")
    config = _load_config(config_path)
    summary: dict[str, Any] = {
        "config": str(_resolved_path(config_path)),
        "credential_source": "not_required",
        "hermes_root": None,
    }
    if "hermes" in subjects:
        selected_root = (
            hermes_root
            or os.environ.get("HERMES_AGENT_ROOT")
            or config.get("hermes_root")
            or Path("~/.hermes/hermes-agent")
        )
        root = _resolved_path(selected_root)
        launcher = root / ".venv" / "bin" / "hermes"
        if (
            not root.is_dir()
            or not launcher.is_file()
            or not os.access(launcher, os.X_OK)
        ):
            raise PreflightError(
                f"Hermes root does not contain an executable pinned environment: {root}"
            )
        os.environ["HERMES_AGENT_ROOT"] = str(root)
        os.environ["PATH"] = f"{launcher.parent}{os.pathsep}{os.environ.get('PATH', '')}"
        summary["hermes_root"] = str(root)
    if GATEWAY_SUBJECTS.intersection(subjects):
        try:
            _, profile = adapters._active_profile()
        except (
            adapters.AdapterError,
            OSError,
            TypeError,
            ValueError,
            KeyError,
        ) as error:
            raise PreflightError(f"active model profile is unusable: {error}") from error
        if profile.get("api_key_placeholder") is None:
            key_name = str(profile["api_key_env"])
            if os.environ.get(key_name):
                summary["credential_source"] = "environment"
            else:
                selected_credential = (
                    credential_file
                    or config.get("credential_file")
                    or DEFAULT_CREDENTIAL
                )
                credential_path = _literal_path(selected_credential)
                os.environ[key_name] = _read_private_credential(credential_path)
                summary["credential_source"] = "owner_only_file"
                summary["credential_file"] = str(credential_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--subject",
        action="append",
        choices=doctor.SUBJECTS,
        help="check one subject (repeatable; default: all five)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("HWB_PREFLIGHT_CONFIG", DEFAULT_CONFIG)),
        help="local path-only config (default: ~/.config/hwb/adapter-preflight.json)",
    )
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--hermes-root", type=Path)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args()
    subjects = tuple(args.subject or doctor.SUBJECTS)
    try:
        local = prepare_environment(
            subjects,
            config_path=args.config,
            credential_file=args.credential_file,
            hermes_root=args.hermes_root,
        )
        result = doctor.report(subjects)
    except (OSError, TypeError, PreflightError) as error:
        parser.error(str(error))
    result["preflight"] = local
    doctor.emit(result, json_only=args.json)
    return 0 if result["overall_status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
