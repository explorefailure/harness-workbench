#!/usr/bin/env python3
"""Offline-first readiness report for the five experimental subject adapters.

The doctor never submits a prompt.  It verifies installed client identity,
checks local authentication metadata, replays one frozen native lifecycle per
normalizer, and binds the current repair apparatus to reviewed live evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import adapters
from harness_workbench.canon import digest_file, digest_obj
from harness_workbench.capture import run_bounded


SUBJECTS = ("claude", "codex", "deepseek", "hermes", "pi")
STATUSES = (
    "ready",
    "pin_drift",
    "schema_drift",
    "auth_missing",
    "live_verification_required",
)
SCHEMA = "cross-harness-adapter-doctor/v0.1"
FIXTURE_SCHEMA = "cross-harness-normalizer-replay/v0.1"
CERTIFICATION_SCHEMA = "cross-harness-live-certification/v0.1"
CERTIFICATION = adapters.HERE / "adapter_certification.json"
FIXTURE_ROOT = adapters.HERE / "replay_fixtures"
PIN_ENTRIES = {
    "claude": ("claude_code", "executable_sha256"),
    "codex": ("codex_cli", "executable_sha256"),
    "deepseek": ("deepseek_harness", "executable_sha256"),
    "hermes": ("hermes_agent", "launcher_sha256"),
    "pi": ("pi_coding_agent", "executable_sha256"),
}


def _jsonl(events: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for event in events
    )


def _replay_fixture(subject: str) -> tuple[bool, str]:
    path = FIXTURE_ROOT / f"{subject}.json"
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture.get("schema") != FIXTURE_SCHEMA:
            return False, "fixture schema is not recognized"
        if fixture.get("subject") != subject:
            return False, "fixture subject does not match its filename"
        workspace = Path(str(fixture["workspace"]))
        stdout = (
            str(fixture.get("stdout_text", "")).encode("utf-8")
            if "stdout_text" in fixture
            else _jsonl(fixture.get("stdout_events", []))
        )
        evidence = _jsonl(fixture.get("evidence_events", []))
        returncode = fixture.get("returncode", 0)
        if type(returncode) is not int:
            return False, "fixture returncode is not an integer"
        if subject == "claude":
            lifecycle, errors = adapters._normalize_claude(stdout, workspace)
        elif subject == "codex":
            lifecycle, errors = adapters._normalize_codex(stdout, workspace)
        elif subject == "deepseek":
            lifecycle, errors = adapters._normalize_deepseek(
                evidence,
                workspace,
                returncode,
                str(fixture["provider"]),
                str(fixture["model"]),
            )
        elif subject == "hermes":
            lifecycle, errors = adapters._normalize_hermes(
                stdout, evidence, workspace, returncode
            )
        elif subject == "pi":
            lifecycle, errors = adapters._normalize_pi(stdout, workspace)
        else:
            return False, "unknown subject"
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        return False, f"fixture could not be replayed: {error}"
    if errors:
        return False, "; ".join(errors)
    observed = digest_obj(lifecycle)
    if observed != fixture.get("lifecycle_sha256"):
        return False, "normalized lifecycle digest changed"
    return True, "frozen native lifecycle replayed exactly"


def _identity_check(subject: str) -> tuple[bool, str]:
    try:
        identity = adapters._verify_identity(subject)
        if subject in adapters.CONFIGURABLE_MODEL_SUBJECTS:
            # Resolving a gateway profile is local.  A local profile would call
            # Ollama to verify its content digest, which is still a readiness
            # probe and never a prompt submission.
            identity.update(adapters._resolve_model(subject))
    except (adapters.AdapterError, OSError, TypeError, ValueError) as error:
        return False, str(error)
    return True, f"installed identity matches pin ({identity['version']})"


def _bounded_auth_command(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    required_json_true: str | None = None,
) -> tuple[bool, str]:
    try:
        result = run_bounded(
            argv,
            cwd=adapters.HERE,
            env=env or dict(os.environ),
            timeout=15,
            stdout_limit=64 * 1024,
            stderr_limit=64 * 1024,
            termination_grace=1.0,
        )
    except OSError as error:
        return False, f"authentication metadata probe could not start: {error}"
    if result.termination_reason is not None or result.group_alive_after_cleanup:
        return False, "authentication metadata probe did not finish cleanly"
    if result.returncode != 0:
        return False, "client reports no usable local authentication"
    if required_json_true is not None:
        try:
            payload = json.loads(result.stdout.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return False, "client authentication status is not valid JSON"
        if not isinstance(payload, dict) or payload.get(required_json_true) is not True:
            return False, "client reports no usable local authentication"
    return True, "client reports usable local authentication"


def _auth_check(subject: str) -> tuple[bool, str]:
    if subject == "claude":
        try:
            executable = str(adapters._executable("claude"))
        except adapters.AdapterError as error:
            return False, str(error)
        return _bounded_auth_command(
            [executable, "auth", "status", "--json"],
            required_json_true="loggedIn",
        )
    if subject == "codex":
        source_home = Path(
            os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
        )
        credential = source_home / "auth.json"
        if not credential.is_file():
            return False, "Codex credential file is not present"
        try:
            raw = credential.read_bytes()
            adapters._credential_file_values(raw, source=credential)
            executable = str(adapters._executable("codex"))
        except (adapters.AdapterError, OSError) as error:
            return False, f"Codex credential file is not usable: {error}"
        with tempfile.TemporaryDirectory(prefix="hwb-doctor-codex-") as directory:
            codex_home = Path(directory)
            adapters._write_private_bytes(codex_home / "auth.json", raw)
            environment = dict(os.environ)
            environment["CODEX_HOME"] = str(codex_home)
            passed, _ = _bounded_auth_command(
                [executable, "login", "status"], env=environment
            )
        return (
            (True, "isolated Codex credential source is usable")
            if passed
            else (False, "isolated Codex credential source is not usable")
        )
    try:
        _, profile = adapters._active_profile()
    except (adapters.AdapterError, OSError, ValueError, KeyError) as error:
        return False, str(error)
    placeholder = profile.get("api_key_placeholder")
    if placeholder is not None:
        return True, "active local profile needs no remote credential"
    name = str(profile["api_key_env"])
    if os.environ.get(name):
        return True, f"required credential environment variable is set ({name})"
    return False, f"required credential environment variable is not set ({name})"


def _certification_check(subject: str) -> tuple[bool, str]:
    try:
        certification = json.loads(CERTIFICATION.read_text(encoding="utf-8"))
        if certification.get("schema") != CERTIFICATION_SCHEMA:
            return False, "live certification schema is not recognized"
        if certification.get("contract_passed") is not True:
            return False, "live certification does not retain a passing contract"
        if certification.get("workload") != "repair":
            return False, "live certification is not for the repair workload"
        if certification.get("draws_per_subject") != 3:
            return False, "live certification does not retain three draws per subject"
        comparator = certification.get("comparator_sha256")
        if not _is_sha256(comparator):
            return False, "live certification comparator digest is malformed"
        subjects = certification.get("subjects")
        if not isinstance(subjects, dict) or subject not in subjects:
            return False, "subject has no reviewed live certification"
        row = subjects[subject]
        if not isinstance(row, dict):
            return False, "subject live certification is malformed"
        if (
            not isinstance(row.get("run_id"), str)
            or not row["run_id"]
            or not _is_sha256(row.get("record_sha256"))
            or row.get("adapter") != "3/3"
            or row.get("outcome") != "3/3"
            or row.get("timeouts") != 0
        ):
            return False, "subject live certification is incomplete"
        inputs = certification.get("inputs")
        if not isinstance(inputs, dict) or set(inputs) != set(adapters.REPAIR_INPUTS):
            return False, "live certification does not bind every repair input"
        for relative, expected in inputs.items():
            if not _is_sha256(expected):
                return False, f"certified input digest is malformed ({relative})"
            path = adapters.HERE / relative
            if digest_file(path) != expected:
                return False, f"certified input changed ({relative})"
        modules = certification.get("apparatus_modules")
        if not isinstance(modules, dict) or set(modules) != {"canon", "capture"}:
            return False, "live certification does not bind the complete apparatus"
        for name, expected in modules.items():
            if not _is_sha256(expected):
                return False, f"certified apparatus digest is malformed ({name})"
            module = getattr(adapters, f"{name}_module")
            if digest_file(module.__file__) != expected:
                return False, f"certified apparatus changed ({name})"
        recertifications = certification.get("recertifications", {})
        if not isinstance(recertifications, dict):
            return False, "live recertifications are malformed"
        if not set(recertifications).issubset(subjects):
            return False, "live recertification names an unknown subject"
        pins = adapters._pins()
        for recertified_subject, recertification in recertifications.items():
            if not isinstance(recertification, dict):
                return False, "live recertification row is malformed"
            baseline = subjects[recertified_subject]
            pin_name, digest_name = PIN_ENTRIES[recertified_subject]
            pin = pins[pin_name]
            if (
                not isinstance(recertification.get("certified_date"), str)
                or not recertification["certified_date"]
                or recertification.get("baseline_run_id") != baseline["run_id"]
                or recertification.get("version") != pin["version"]
                or recertification.get("executable_sha256")
                != f"sha256:{pin[digest_name]}"
                or recertification.get("pin_sha256") != inputs["pin.json"]
                or not _is_sha256(recertification.get("report_sha256"))
                or not _is_sha256(recertification.get("record_sha256"))
                or not _is_sha256(
                    recertification.get("semantic_evidence_sha256")
                )
                or recertification.get("draws") != 1
                or recertification.get("adapter") != "1/1"
                or recertification.get("outcome") != "1/1"
                or recertification.get("timeouts") != 0
                or recertification.get("normalized_evidence_changed") is not False
                or recertification.get("task_outcome_changed") is not False
            ):
                return False, (
                    "live recertification does not bind an unchanged one-draw "
                    f"bridge ({recertified_subject})"
                )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
        return False, f"live certification cannot be verified: {error}"
    return True, "current repair apparatus matches reviewed live evidence"


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    payload = value.removeprefix("sha256:")
    return len(payload) == 64 and all(
        character in "0123456789abcdef" for character in payload
    )


def diagnose_subject(subject: str) -> dict[str, Any]:
    checks = {
        "identity": _identity_check(subject),
        "schema_replay": _replay_fixture(subject),
        "authentication": _auth_check(subject),
        "certification": _certification_check(subject),
    }
    if not checks["identity"][0]:
        status = "pin_drift"
    elif not checks["schema_replay"][0]:
        status = "schema_drift"
    elif not checks["authentication"][0]:
        status = "auth_missing"
    elif not checks["certification"][0]:
        status = "live_verification_required"
    else:
        status = "ready"
    return {
        "subject": subject,
        "status": status,
        "checks": {
            name: {"passed": passed, "detail": detail}
            for name, (passed, detail) in checks.items()
        },
    }


def report(subjects: tuple[str, ...] = SUBJECTS) -> dict[str, Any]:
    rows = [diagnose_subject(subject) for subject in subjects]
    return {
        "schema": SCHEMA,
        "model_calls_made": False,
        "overall_status": (
            "ready" if all(row["status"] == "ready" for row in rows)
            else "attention_required"
        ),
        "subjects": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--subject",
        action="append",
        choices=SUBJECTS,
        help="check one subject (repeatable; default: all five)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args()
    result = report(tuple(args.subject or SUBJECTS))
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for row in result["subjects"]:
            print(f"{row['subject']:<9} {row['status']}")
            for name, check in row["checks"].items():
                mark = "ok" if check["passed"] else "NO"
                print(f"  {name:<16} {mark:<2}  {check['detail']}")
        print("\nNo model calls were made.")
    return 0 if result["overall_status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
