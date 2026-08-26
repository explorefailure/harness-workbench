#!/usr/bin/env python3
"""Plan or execute one guarded five-subject repair certification recut."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Any

# A source checkout can execute this command directly without inheriting the
# relative ``PYTHONPATH=../../src`` spelling used by the old manual recipe.
# Materialized installed trees do not have this layout and keep the installed
# package resolution instead.
HERE = Path(__file__).resolve().parent
_CHECKOUT_SOURCE = HERE.parents[1]
if (_CHECKOUT_SOURCE / "harness_workbench" / "__init__.py").is_file():
    sys.path.insert(0, str(_CHECKOUT_SOURCE.resolve()))

import adapters
import doctor
import harness_workbench
from harness_workbench import __version__
from harness_workbench.canon import digest_file, digest_tree
from harness_workbench.capture import (
    Bounded,
    credential_values,
    redact_bytes,
    run_bounded,
)
import preflight
import runner as subject_runner
import usage_probe


SCHEMA = "cross-harness-certification-recut/v0.1"
CANDIDATE_SCHEMA = "cross-harness-certification-candidate/v0.1"
SUBJECTS = ("claude", "codex", "deepseek", "hermes", "pi")
WORKLOAD = "repair"
DRAWS = 3
RETRY_MAX = 2
NOMINAL_CALLS = len(SUBJECTS) * DRAWS
MAXIMUM_CALLS = NOMINAL_CALLS * RETRY_MAX
DEFAULT_LIMITS = {"rolling": 80, "weekly": 90}
MAX_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_SCAN_FILE_BYTES = 32 * 1024 * 1024
SOURCE_ROOT = Path(harness_workbench.__file__).resolve().parents[1]
COMPARATOR = (HERE / "compare.py").resolve()
CERTIFICATION = (HERE / "adapter_certification.json").resolve()
GITLEAKS_CONFIG = (HERE / ".gitleaks.toml").resolve()
SPEC_PATHS = {
    subject: (HERE / f"repair_{subject}.json").resolve()
    for subject in SUBJECTS
}


def _is_int(value: object) -> bool:
    return type(value) is int


def _validate_limits(limits: dict[str, int]) -> None:
    invalid = {
        name: value
        for name, value in limits.items()
        if name not in usage_probe.WINDOWS
        or not _is_int(value)
        or not 1 <= value <= 100
    }
    if invalid:
        raise ValueError("usage limits must be known windows between 1 and 100")


def _validate_spec_document(subject: str, document: object) -> dict[str, Any]:
    """Prove the retained Workbench semantics were not weakened."""
    if not isinstance(document, dict) or document.get("schema") != "hwbspec/v0.1":
        raise ValueError(f"{subject} repair spec schema is not recognized")
    features = document.get("features")
    if not isinstance(features, list):
        raise ValueError(f"{subject} repair spec has no feature list")
    names = [
        feature.get("name") if isinstance(feature, dict) else None
        for feature in features
    ]
    if names != ["freeze", "receipt", "retry", "sample", "timing"]:
        raise ValueError(f"{subject} repair spec changed its feature order")
    retry = features[2].get("config")
    sample = features[3].get("config")
    if retry != {"max": RETRY_MAX} or sample != {"n": DRAWS}:
        raise ValueError(f"{subject} repair spec changed retry/sample bounds")
    if RETRY_MAX * DRAWS != 6:
        raise ValueError("repair call arithmetic no longer has a six-call ceiling")
    steps = document.get("steps")
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
        raise ValueError(f"{subject} repair spec must contain exactly one step")
    step = steps[0]
    if step.get("argv") != ["./run_subject.sh", subject, WORKLOAD]:
        raise ValueError(f"{subject} repair spec changed the subject invocation")
    if step.get("inputs") != list(adapters.REPAIR_INPUTS):
        raise ValueError(f"{subject} repair spec changed the exact input set")
    if document.get("step_timeout_ms") is not None:
        raise ValueError(f"{subject} repair spec added a competing step timeout")
    return document


def _offline_apparatus(*, require_live_prerequisites: bool) -> dict[str, Any]:
    if tuple(doctor.SUBJECTS) != SUBJECTS:
        raise ValueError("doctor subject set is not the exact five-subject set")
    if not COMPARATOR.is_file() or not CERTIFICATION.is_file():
        raise ValueError("certification comparator or promotion target is missing")
    gitleaks = shutil.which("gitleaks")
    if not gitleaks and require_live_prerequisites:
        raise ValueError("gitleaks is required before a live certification recut")
    gitleaks_path = Path(gitleaks).resolve() if gitleaks else None
    if gitleaks_path is not None and (
        not gitleaks_path.is_file() or not os.access(gitleaks_path, os.X_OK)
    ):
        raise ValueError("resolved gitleaks executable is not usable")
    if not GITLEAKS_CONFIG.is_file():
        raise ValueError("the bundled gitleaks configuration is missing")

    specs: dict[str, str] = {}
    input_maps: list[dict[str, str]] = []
    for subject in SUBJECTS:
        path = SPEC_PATHS[subject]
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(f"{subject} repair spec is not readable JSON") from error
        _validate_spec_document(subject, document)
        specs[subject] = digest_file(path)
        input_maps.append({
            relative: digest_file(HERE / relative)
            for relative in adapters.REPAIR_INPUTS
        })
    if any(mapping != input_maps[0] for mapping in input_maps[1:]):
        raise ValueError("repair specs do not bind one exact input map")
    return {
        "python": str(Path(sys.executable).resolve()),
        "python_sha256": digest_file(Path(sys.executable).resolve()),
        "source_root": str(SOURCE_ROOT),
        "subjects_root": str(HERE),
        "comparator": str(COMPARATOR),
        "comparator_sha256": digest_file(COMPARATOR),
        "promotion_target": str(CERTIFICATION),
        "promotion_target_sha256": digest_file(CERTIFICATION),
        "gitleaks": str(gitleaks_path) if gitleaks_path is not None else None,
        "gitleaks_sha256": (
            digest_file(gitleaks_path) if gitleaks_path is not None else None
        ),
        "gitleaks_available": gitleaks_path is not None,
        "gitleaks_config": str(GITLEAKS_CONFIG),
        "gitleaks_config_sha256": digest_file(GITLEAKS_CONFIG),
        "specs": specs,
        "inputs": input_maps[0],
        "apparatus_modules": {
            name: digest_file(getattr(adapters, f"{name}_module").__file__)
            for name in ("canon", "capture")
        },
        "workflow_modules": {
            Path(path).name: digest_file(path)
            for path in (
                __file__,
                doctor.__file__,
                preflight.__file__,
                usage_probe.__file__,
            )
        },
    }


def build_plan(
    limits: dict[str, int], *, require_live_prerequisites: bool = False
) -> dict[str, Any]:
    """Describe an exact recut while authorizing no model calls."""
    _validate_limits(limits)
    apparatus = _offline_apparatus(
        require_live_prerequisites=require_live_prerequisites
    )
    run_root = "<fresh-record-dir>/runs"
    return {
        "schema": SCHEMA,
        "live": False,
        "model_calls_authorized": 0,
        "nominal_model_calls": NOMINAL_CALLS,
        "maximum_model_calls": MAXIMUM_CALLS,
        "subjects": list(SUBJECTS),
        "workload": WORKLOAD,
        "draws_per_subject": DRAWS,
        "retry_max_attempts_per_draw": RETRY_MAX,
        "usage_limits": dict(sorted(limits.items())),
        "record_dir_required_for_live": True,
        "apparatus": apparatus,
        "specs": [str(SPEC_PATHS[subject]) for subject in SUBJECTS],
        "commands": {
            "runs": {
                subject: [
                    apparatus["python"],
                    "-m",
                    "harness_workbench",
                    "--root",
                    run_root,
                    "run",
                    str(SPEC_PATHS[subject]),
                ]
                for subject in SUBJECTS
            },
            "verify_each": [
                apparatus["python"],
                "-m",
                "harness_workbench",
                "--root",
                run_root,
                "verify",
                "<run-id>",
            ],
            "compare_exact_five": [
                apparatus["python"],
                str(COMPARATOR),
                *(f"{run_root}/<{subject}-run-id>" for subject in SUBJECTS),
            ],
        },
        "supervisor_timeouts_seconds": {
            subject: _run_timeout(subject) for subject in SUBJECTS
        },
        "stages": [
            "offline_prerequisites",
            "usage_gate",
            "five_sealed_workbench_runs",
            "verify_every_sealed_store",
            "exact_five_comparison",
            "post_run_usage_and_postflight",
            "gitleaks_and_exact_credential_scan",
            "review_candidate",
        ],
        "promotion": {
            "automatic": False,
            "review_required": True,
            "target": str(CERTIFICATION),
        },
    }


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
    finally:
        os.close(descriptor)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes_exclusive(path, _json_bytes(payload))


def _process_clean(result: Bounded) -> bool:
    return bool(
        result.termination_reason is None
        and not result.stdout_overflow
        and not result.stderr_overflow
        and not result.group_alive_after_cleanup
    )


def _run_command(
    process_root: Path,
    index: int,
    label: str,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    stdout_limit: int = MAX_CAPTURE_BYTES,
    stderr_limit: int = MAX_CAPTURE_BYTES,
) -> tuple[Bounded, dict[str, Any]]:
    result = run_bounded(
        argv,
        cwd=cwd.resolve(),
        env=env,
        timeout=timeout,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
        termination_grace=5.0,
    )
    prefix = f"{index:02d}-{label}"
    stdout_path = process_root / f"{prefix}.stdout.bin"
    stderr_path = process_root / f"{prefix}.stderr.bin"
    receipt_path = process_root / f"{prefix}.json"
    _write_bytes_exclusive(stdout_path, result.stdout)
    _write_bytes_exclusive(stderr_path, result.stderr)
    receipt = {
        "schema": "cross-harness-bounded-process/v0.1",
        "label": label,
        "argv": list(argv),
        "cwd": str(cwd.resolve()),
        "timeout_seconds": timeout,
        "limits": {"stdout_bytes": stdout_limit, "stderr_bytes": stderr_limit},
        "returncode": result.returncode,
        "termination_reason": result.termination_reason,
        "timed_out": result.timed_out,
        "overflow": {
            "stdout": result.stdout_overflow,
            "stderr": result.stderr_overflow,
        },
        "process_group": {
            "alive_before_cleanup": result.group_alive_before_cleanup,
            "alive_after_cleanup": result.group_alive_after_cleanup,
        },
        "forwarded_signals": list(result.forwarded_signals),
        "stdout": {
            "path": str(stdout_path.relative_to(process_root.parent)),
            "stored_bytes": len(result.stdout),
            "source_bytes": result.stdout_source_bytes,
            "sha256": digest_file(stdout_path),
        },
        "stderr": {
            "path": str(stderr_path.relative_to(process_root.parent)),
            "stored_bytes": len(result.stderr),
            "source_bytes": result.stderr_source_bytes,
            "sha256": digest_file(stderr_path),
        },
        "cleanup_passed": not result.group_alive_after_cleanup,
    }
    _write_json_exclusive(receipt_path, receipt)
    receipt["receipt"] = str(receipt_path.relative_to(process_root.parent))
    receipt["receipt_sha256"] = digest_file(receipt_path)
    return result, receipt


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    # Deliberately replace rather than extend a possibly-relative caller value.
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    return environment


def _run_timeout(subject: str) -> int:
    return subject_runner.timeout_seconds(subject, WORKLOAD) * DRAWS * RETRY_MAX + 120


def _retained_attempt_count(run_dir: Path) -> int:
    path = run_dir / "attempts.jsonl"
    try:
        attempts = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot count retained attempts in {run_dir}") from error
    if any(
        not isinstance(row, dict) or row.get("executed", True) is not True
        for row in attempts
    ):
        raise ValueError(f"{run_dir} contains a non-executed attempt")
    return len(attempts)


def _attempt_count(run_dir: Path) -> int:
    count = _retained_attempt_count(run_dir)
    if not DRAWS <= count <= DRAWS * RETRY_MAX:
        raise ValueError(f"{run_dir} attempt count is outside the 3-to-6 bound")
    return count


def _scan_retained(
    destination: Path,
    values: tuple[str, ...],
    *,
    virtual_files: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Check every retained regular file without recording credential values."""
    files: list[str] = []
    errors: list[str] = []
    for path in sorted(destination.rglob("*")):
        relative = str(path.relative_to(destination))
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            errors.append(f"{relative}: cannot inspect retained path: {error}")
            continue
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            errors.append(f"{relative}: retained path is not a regular file")
            continue
        try:
            size = path.stat().st_size
            if size > MAX_SCAN_FILE_BYTES:
                raise ValueError("file exceeds the credential-scan byte limit")
            raw = path.read_bytes()
        except (OSError, ValueError) as error:
            errors.append(f"{relative}: cannot scan retained file: {error}")
            continue
        files.append(relative)
        if values and redact_bytes(raw, values)[1]:
            errors.append(f"{relative}: a configured credential value is present")
    for relative, raw in sorted((virtual_files or {}).items()):
        files.append(relative)
        if len(raw) > MAX_SCAN_FILE_BYTES:
            errors.append(f"{relative}: generated file exceeds the scan byte limit")
        elif values and redact_bytes(raw, values)[1]:
            errors.append(f"{relative}: a configured credential value would be present")
    return {
        "schema": "cross-harness-credential-scan/v0.1",
        "credential_values_checked": len(values),
        "files": sorted(set(files)),
        "passed": not errors and bool(values),
        "errors": errors + ([] if values else ["no credential values were available to check"]),
    }


def _record_digests(destination: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or "runs" in path.relative_to(destination).parts:
            continue
        relative = str(path.relative_to(destination))
        if relative in {
            "certification-candidate.json",
            "certification-report.json",
            "credential-scan.json",
        }:
            continue
        records[relative] = digest_file(path)
    return records


def _comparison_eligible(comparison: object) -> list[str]:
    if not isinstance(comparison, dict):
        return ["comparator output is not an object"]
    errors: list[str] = []
    if comparison.get("contract_passed") is not True or comparison.get("errors") != []:
        errors.append("exact-five comparator did not pass cleanly")
    subjects = comparison.get("subjects")
    if not isinstance(subjects, dict) or set(subjects) != set(SUBJECTS):
        return errors + ["comparator output does not contain exactly five subjects"]
    for subject in SUBJECTS:
        row = subjects.get(subject)
        if not isinstance(row, dict):
            errors.append(f"{subject} comparator row is missing")
            continue
        if (
            row.get("draws") != DRAWS
            or row.get("adapter_passed") != DRAWS
            or row.get("outcome_passed") != DRAWS
            or row.get("timed_out") != 0
        ):
            errors.append(f"{subject} is not adapter/outcome 3/3 with zero timeouts")
    return errors


def _limits(overrides: list[str]) -> dict[str, int]:
    limits = dict(DEFAULT_LIMITS)
    limits.update(usage_probe._limits(overrides))
    return limits


def execute(
    plan: dict[str, Any],
    record_dir: Path,
    *,
    config_path: Path,
    credential_file: Path | None = None,
    hermes_root: Path | None = None,
) -> tuple[dict[str, Any], int]:
    scanner = plan.get("apparatus", {}).get("gitleaks")
    scanner_digest = plan.get("apparatus", {}).get("gitleaks_sha256")
    if not isinstance(scanner, str) or not isinstance(scanner_digest, str):
        raise ValueError("gitleaks is required before a live certification recut")
    scanner_path = Path(scanner)
    if (
        not scanner_path.is_absolute()
        or not scanner_path.is_file()
        or not os.access(scanner_path, os.X_OK)
        or digest_file(scanner_path) != scanner_digest
    ):
        raise ValueError("the planned gitleaks executable is no longer usable")
    destination = record_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    process_root = destination / "process"
    runs_root = destination / "runs"
    process_root.mkdir(mode=0o700)
    runs_root.mkdir(mode=0o700)
    report: dict[str, Any] = {
        **plan,
        "live": True,
        "model_calls_authorized": MAXIMUM_CALLS,
        "record_dir": str(destination),
        "model_calls_started": 0,
        "runs": {},
        "processes": [],
    }
    certification_before = digest_file(CERTIFICATION)
    report["promotion"] = {
        **plan["promotion"],
        "performed": False,
        "target_sha256_before": certification_before,
    }

    def finish(status: str, exit_code: int) -> tuple[dict[str, Any], int]:
        report["promotion"]["target_sha256_after"] = digest_file(CERTIFICATION)
        report["promotion"]["unchanged"] = (
            report["promotion"]["target_sha256_after"] == certification_before
        )
        report["status"] = status
        report["passed"] = exit_code == 0
        report_path = destination / "certification-report.json"
        if not report_path.exists():
            _write_json_exclusive(report_path, report)
        return report, exit_code

    try:
        local = preflight.prepare_environment(
            SUBJECTS,
            config_path=config_path,
            credential_file=credential_file,
            hermes_root=hermes_root,
        )
        initial_doctor = doctor.report(SUBJECTS)
    except (OSError, TypeError, ValueError, preflight.PreflightError) as error:
        report["error"] = f"offline prerequisites: {error}"
        return finish("preflight_error", 2)
    report["preflight"] = {**initial_doctor, "local": local}
    if initial_doctor.get("overall_status") != "ready":
        return finish("preflight_blocked", 1)

    values = credential_values(os.environ)
    try:
        usage_before = usage_probe.snapshot()
        _write_json_exclusive(destination / "usage-before.json", usage_before)
    except (OSError, usage_probe.ProbeError) as error:
        report["error"] = f"usage before run: {error}"
        return finish("usage_unknown", 3)
    gate_passed, gate_reasons = usage_probe.gate(
        usage_before, plan["usage_limits"]
    )
    report["usage"] = {
        "before": usage_before,
        "gate": {
            "limits": plan["usage_limits"],
            "passed": gate_passed,
            "reasons": gate_reasons,
        },
    }
    if not gate_passed:
        return finish("usage_gate_blocked", 1)

    print(json.dumps({
        "stage": "live_authorization",
        "nominal_model_calls": NOMINAL_CALLS,
        "maximum_model_calls": MAXIMUM_CALLS,
        "usage": usage_before,
        "stop_thresholds": plan["usage_limits"],
        "record_dir": str(destination),
    }, sort_keys=True), flush=True)

    environment = _child_environment()
    process_index = 0
    operational_errors: list[str] = []
    run_paths: dict[str, Path] = {}
    for subject in SUBJECTS:
        before = {
            entry.name for entry in runs_root.iterdir() if entry.is_dir()
        }
        argv = [
            plan["apparatus"]["python"],
            "-m",
            "harness_workbench",
            "--root",
            str(runs_root),
            "run",
            str(SPEC_PATHS[subject]),
        ]
        result, receipt = _run_command(
            process_root,
            process_index,
            f"run-{subject}",
            argv,
            cwd=HERE,
            env=environment,
            timeout=_run_timeout(subject),
        )
        process_index += 1
        report["processes"].append(receipt)
        after = {
            entry.name for entry in runs_root.iterdir() if entry.is_dir()
        }
        created = sorted(after - before)
        if _process_clean(result) and result.returncode == 0 and len(created) == 1:
            run_paths[subject] = runs_root / created[0]
            try:
                attempts = _attempt_count(run_paths[subject])
            except ValueError as error:
                operational_errors.append(str(error))
                break
            report["model_calls_started"] += attempts
            report["runs"][subject] = {
                "run_id": created[0],
                "store": str(run_paths[subject].relative_to(destination)),
                "attempts_retained": attempts,
            }
        else:
            for offset, run_id in enumerate(created):
                run_path = runs_root / run_id
                if offset == 0:
                    run_paths[subject] = run_path
                try:
                    retained = _retained_attempt_count(run_path)
                except ValueError:
                    retained = None
                if retained is not None:
                    report["model_calls_started"] += retained
                report.setdefault("unexpected_or_incomplete_stores", []).append({
                    "subject_command": subject,
                    "run_id": run_id,
                    "store": str(run_path.relative_to(destination)),
                    "attempts_retained": retained,
                })
            operational_errors.append(
                f"{subject} Workbench run did not finish as one clean sealed store"
            )
            break

    # Every store that exists is verified, including one left by a failed command.
    verification_targets = list(run_paths.items())
    known_paths = set(run_paths.values())
    for index, run_path in enumerate(sorted(runs_root.iterdir())):
        if run_path.is_dir() and run_path not in known_paths:
            verification_targets.append((f"unexpected-{index}", run_path))
    for subject, run_path in verification_targets:
        argv = [
            plan["apparatus"]["python"],
            "-m",
            "harness_workbench",
            "--root",
            str(runs_root),
            "verify",
            run_path.name,
        ]
        result, receipt = _run_command(
            process_root,
            process_index,
            f"verify-{subject}",
            argv,
            cwd=HERE,
            env=environment,
            timeout=60,
            stdout_limit=1024 * 1024,
            stderr_limit=1024 * 1024,
        )
        process_index += 1
        report["processes"].append(receipt)
        verified = _process_clean(result) and result.returncode == 0
        target = report["runs"] if subject in SUBJECTS else report.setdefault(
            "unexpected_store_verification", {}
        )
        target.setdefault(subject, {
            "run_id": run_path.name,
            "store": str(run_path.relative_to(destination)),
            "attempts_retained": None,
        })
        target[subject]["verify_passed"] = verified
        if verified:
            target[subject].update({
                "store_sha256": digest_tree(str(run_path)),
                "record_sha256": digest_file(run_path / "record.json"),
                "integrity_sha256": digest_file(run_path / "integrity.json"),
            })
        else:
            operational_errors.append(f"{subject} sealed store failed hwb verify")

    comparison: dict[str, Any] | None = None
    comparison_path = destination / "comparison.json"
    if not operational_errors and tuple(run_paths) == SUBJECTS:
        argv = [
            plan["apparatus"]["python"],
            str(COMPARATOR),
            *(str(run_paths[subject]) for subject in SUBJECTS),
        ]
        result, receipt = _run_command(
            process_root,
            process_index,
            "compare-exact-five",
            argv,
            cwd=HERE,
            env=environment,
            timeout=120,
        )
        process_index += 1
        report["processes"].append(receipt)
        if _process_clean(result):
            _write_bytes_exclusive(comparison_path, result.stdout)
            try:
                comparison = json.loads(result.stdout)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                operational_errors.append("comparator output was not readable JSON")
            if result.returncode not in (0, 1):
                operational_errors.append("comparator process did not finish normally")
        else:
            operational_errors.append("comparator process exceeded an execution bound")

    after_error: str | None = None
    try:
        usage_after = usage_probe.snapshot()
        _write_json_exclusive(destination / "usage-after.json", usage_after)
    except (OSError, usage_probe.ProbeError) as error:
        after_error = str(error)
        report["usage"]["after_error"] = after_error
    else:
        report["usage"]["after"] = usage_after
        report["usage"]["delta"] = usage_probe.delta(usage_before, usage_after)
    try:
        postflight = doctor.report(SUBJECTS)
    except (OSError, TypeError, ValueError) as error:
        postflight = {"overall_status": "error", "error": str(error)}
    report["postflight"] = postflight

    gitleaks_report = destination / "gitleaks-report.json"
    gitleaks_result, gitleaks_receipt = _run_command(
        process_root,
        process_index,
        "gitleaks-retained-evidence",
        [
            plan["apparatus"]["gitleaks"],
            "dir",
            "--no-banner",
            "--redact=100",
            "--max-target-megabytes",
            str(MAX_SCAN_FILE_BYTES // (1024 * 1024)),
            "--timeout",
            "120",
            "--config",
            plan["apparatus"]["gitleaks_config"],
            "--report-format",
            "json",
            "--report-path",
            str(gitleaks_report),
            str(destination),
        ],
        cwd=HERE,
        env=environment,
        timeout=150,
        stdout_limit=1024 * 1024,
        stderr_limit=1024 * 1024,
    )
    report["processes"].append(gitleaks_receipt)
    gitleaks_clean = _process_clean(gitleaks_result) and gitleaks_result.returncode == 0
    if not _process_clean(gitleaks_result):
        operational_errors.append("gitleaks process exceeded an execution bound")
    if not gitleaks_report.exists():
        _write_json_exclusive(gitleaks_report, {"findings": "report missing"})
        gitleaks_clean = False
    report["gitleaks"] = {
        "passed": gitleaks_clean,
        "report": str(gitleaks_report.relative_to(destination)),
        "report_sha256": digest_file(gitleaks_report),
    }

    eligibility_errors = list(operational_errors)
    if after_error is not None:
        eligibility_errors.append("post-run usage could not be retained")
    if postflight.get("overall_status") != "ready":
        eligibility_errors.append("offline postflight is not ready")
    if not gitleaks_clean:
        eligibility_errors.append("gitleaks did not prove retained evidence clean")
    eligibility_errors.extend(_comparison_eligible(comparison))
    if digest_file(CERTIFICATION) != certification_before:
        eligibility_errors.append("adapter_certification.json changed during the recut")
    if report["model_calls_started"] > MAXIMUM_CALLS:
        eligibility_errors.append("retained attempt count exceeded the authorized maximum")

    comparison_digest = digest_file(comparison_path) if comparison_path.is_file() else None
    candidate = {
        "schema": CANDIDATE_SCHEMA,
        "review_status": "candidate" if not eligibility_errors else "ineligible",
        "eligible_for_review": not eligibility_errors,
        "eligibility_errors": eligibility_errors,
        "workload": WORKLOAD,
        "subjects": list(SUBJECTS),
        "draws_per_subject": DRAWS,
        "calls": {
            "nominal": NOMINAL_CALLS,
            "maximum": MAXIMUM_CALLS,
            "started": report["model_calls_started"],
        },
        "inputs": plan["apparatus"]["inputs"],
        "specs": plan["apparatus"]["specs"],
        "apparatus": {
            "package": "harness_workbench",
            "version": __version__,
            "source_root": str(SOURCE_ROOT),
            "modules": plan["apparatus"]["apparatus_modules"],
            "workflow_modules": plan["apparatus"]["workflow_modules"],
            "python": {
                "path": plan["apparatus"]["python"],
                "sha256": plan["apparatus"]["python_sha256"],
            },
            "gitleaks": {
                "path": plan["apparatus"]["gitleaks"],
                "sha256": plan["apparatus"]["gitleaks_sha256"],
                "config": plan["apparatus"]["gitleaks_config"],
                "config_sha256": plan["apparatus"]["gitleaks_config_sha256"],
            },
        },
        "comparator": {
            "program": str(COMPARATOR),
            "program_sha256": plan["apparatus"]["comparator_sha256"],
            "result": "comparison.json" if comparison_path.is_file() else None,
            "result_sha256": comparison_digest,
        },
        "runs": report["runs"],
        "records": _record_digests(destination),
        "usage": {
            "before": "usage-before.json",
            "after": "usage-after.json" if (destination / "usage-after.json").is_file() else None,
            "delta": report["usage"].get("delta"),
            "limits": plan["usage_limits"],
        },
        "security": {
            "gitleaks_passed": gitleaks_clean,
            "credential_scan": "credential-scan.json",
        },
        "promotion": {
            "performed": False,
            "review_required": True,
            "target": str(CERTIFICATION),
            "target_sha256_before": certification_before,
            "target_sha256_after": digest_file(CERTIFICATION),
        },
    }
    candidate_path = destination / "certification-candidate.json"
    candidate_bytes = _json_bytes(candidate)
    candidate_digest = "sha256:" + hashlib.sha256(candidate_bytes).hexdigest()
    report["candidate"] = {
        "manifest": candidate_path.name,
        "manifest_sha256": candidate_digest,
        "eligible_for_review": candidate["eligible_for_review"],
    }
    report["cleanup"] = {
        "all_process_groups_clean": all(
            process["cleanup_passed"] for process in report["processes"]
        ),
        "processes_observed": len(report["processes"]),
    }
    report["promotion"]["target_sha256_after"] = digest_file(CERTIFICATION)
    report["promotion"]["unchanged"] = (
        report["promotion"]["target_sha256_after"] == certification_before
    )
    report["status"] = "candidate_ready" if candidate["eligible_for_review"] else (
        "operational_failure" if operational_errors else "candidate_ineligible"
    )
    report["passed"] = candidate["eligible_for_review"]
    report_path = destination / "certification-report.json"
    report_bytes = _json_bytes(report)
    scan = _scan_retained(
        destination,
        values,
        virtual_files={
            candidate_path.name: candidate_bytes,
            report_path.name: report_bytes,
        },
    )
    if not scan["passed"] and candidate["eligible_for_review"]:
        candidate["eligible_for_review"] = False
        candidate["review_status"] = "ineligible"
        candidate["eligibility_errors"].append(
            "exact credential-value scan did not prove every retained file clean"
        )
        candidate_bytes = _json_bytes(candidate)
        candidate_digest = "sha256:" + hashlib.sha256(candidate_bytes).hexdigest()
        report["candidate"]["manifest_sha256"] = candidate_digest
        report["candidate"]["eligible_for_review"] = False
        report["status"] = "candidate_ineligible"
        report["passed"] = False
        report_bytes = _json_bytes(report)
        scan = _scan_retained(
            destination,
            values,
            virtual_files={
                candidate_path.name: candidate_bytes,
                report_path.name: report_bytes,
            },
        )
    scan_path = destination / "credential-scan.json"
    scan["files"] = sorted(set(scan["files"] + [scan_path.name]))
    scan_bytes = _json_bytes(scan)
    if values and redact_bytes(scan_bytes, values)[1]:
        scan["passed"] = False
        scan["errors"].append("credential-scan.json would contain a credential value")
        scan_bytes = _json_bytes(scan)
    _write_bytes_exclusive(candidate_path, candidate_bytes)
    _write_bytes_exclusive(report_path, report_bytes)
    _write_bytes_exclusive(scan_path, scan_bytes)
    if operational_errors:
        return report, 2
    if after_error is not None:
        return report, 3
    return report, 0 if report["passed"] and scan["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live",
        action="store_true",
        help="execute the plan; without this flag no credential or model is used",
    )
    parser.add_argument(
        "--record-dir",
        type=Path,
        help="required with --live; the directory must not already exist",
    )
    parser.add_argument(
        "--max",
        action="append",
        default=[],
        metavar="WINDOW=PCT",
        help="override a default usage gate (rolling=80, weekly=90)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("HWB_PREFLIGHT_CONFIG", preflight.DEFAULT_CONFIG)),
    )
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--hermes-root", type=Path)
    args = parser.parse_args()
    try:
        plan = build_plan(
            _limits(args.max), require_live_prerequisites=args.live
        )
        if not args.live:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        if args.record_dir is None:
            raise ValueError("--record-dir is required with --live")
        report, status = execute(
            plan,
            args.record_dir,
            config_path=args.config,
            credential_file=args.credential_file,
            hermes_root=args.hermes_root,
        )
    except (OSError, TypeError, ValueError, usage_probe.ProbeError) as error:
        parser.error(str(error))
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
