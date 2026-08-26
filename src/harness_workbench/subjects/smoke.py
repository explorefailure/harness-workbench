#!/usr/bin/env python3
"""Plan or run one guarded, retained live smoke campaign."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import doctor
from harness_workbench.canon import digest_file
import preflight
import recertify
import usage_probe


SCHEMA = "cross-harness-live-smoke/v0.1"
DEFAULT_LIMITS = {"rolling": 80, "weekly": 90}
MAX_RECORD_BYTES = 16 * 1024 * 1024


def build_plan(
    subjects: tuple[str, ...],
    workload: str,
    draws: int,
    limits: dict[str, int],
) -> dict[str, Any]:
    """Describe the complete campaign while authorizing zero model calls."""
    if len(set(subjects)) != len(subjects):
        raise ValueError("live smoke subjects must not be repeated")
    invalid_limits = {
        name: value
        for name, value in limits.items()
        if name not in usage_probe.WINDOWS
        or type(value) is not int
        or not 1 <= value <= 100
    }
    if invalid_limits:
        raise ValueError("usage limits must be known windows between 1 and 100")
    recertification = recertify.build_plan(subjects, workload, draws)
    return {
        "schema": SCHEMA,
        "live": False,
        "model_calls_authorized": 0,
        "live_subject_runs_planned": recertification[
            "live_subject_runs_planned"
        ],
        "subjects": list(subjects),
        "workload": workload,
        "draws": draws,
        "usage_limits": dict(sorted(limits.items())),
        "commands": recertification["commands"],
        "record_dir_required_for_live": True,
        "stages": [
            "prepare_environment",
            "offline_preflight",
            "usage_gate",
            "retained_execution",
            "post_run_usage",
            "receipt_validation",
            "offline_postflight",
        ],
    }


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    """Create owner-only evidence without permitting a silent overwrite."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _manifest_map(value: object) -> dict[str, tuple[object, object, object]] | None:
    if not isinstance(value, list):
        return None
    result: dict[str, tuple[object, object, object]] = {}
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            return None
        path = entry["path"]
        if path in result:
            return None
        result[path] = (entry.get("sha256"), entry.get("size"), entry.get("mode"))
    return result


def _changed_paths(workspace: object) -> list[str] | None:
    if not isinstance(workspace, dict):
        return None
    before = _manifest_map(workspace.get("before"))
    after = _manifest_map(workspace.get("after"))
    if before is None or after is None:
        return None
    return sorted(
        path for path in set(before).union(after) if before.get(path) != after.get(path)
    )


def _process_errors(process: object, label: str) -> list[str]:
    if not isinstance(process, dict):
        return [f"{label} process evidence is missing"]
    errors = []
    if process.get("timed_out") is not False:
        errors.append(f"{label} did not prove timed_out=false")
    if process.get("termination_reason") is not None:
        errors.append(f"{label} has a termination reason")
    group = process.get("process_group")
    if not isinstance(group, dict) or group.get("alive_after_cleanup") is not False:
        errors.append(f"{label} did not prove process-group cleanup")
    return errors


def validate_receipt(
    path: Path,
    *,
    subject: str,
    workload: str,
    expected_sha256: str | None,
    credential: str | None,
) -> dict[str, Any]:
    """Independently check the retained evidence the smoke command relies on."""
    errors: list[str] = []
    try:
        metadata = path.stat()
        if metadata.st_size > MAX_RECORD_BYTES:
            raise ValueError("record exceeds the smoke byte limit")
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        return {
            "subject": subject,
            "record": path.name,
            "passed": False,
            "credential_value_absent": None,
            "changed_paths": None,
            "errors": [f"record is not bounded readable JSON: {error}"],
        }

    credential_absent = True
    if credential:
        credential_absent = credential.encode("utf-8") not in raw
        if not credential_absent:
            errors.append("prepared credential value appears in retained evidence")

    if not isinstance(document, dict):
        errors.append("record root is not an object")
        document = {}
    if document.get("schema") != "cross-harness-experiment-run/v0.1":
        errors.append("record schema is not recognized")
    if document.get("subject") != subject:
        errors.append("record subject does not match the plan")
    if document.get("workload") != workload or document.get("variant") is not None:
        errors.append("record workload does not match the plan")

    verdict = document.get("verdict")
    if (
        not isinstance(verdict, dict)
        or verdict.get("adapter_passed") is not True
        or verdict.get("outcome_passed") is not True
        or verdict.get("interrupted") is not False
        or type(verdict.get("status")) is not int
        or verdict.get("status") != 0
    ):
        errors.append("top-level verdict is not a clean pass")

    adapter = document.get("adapter")
    if not isinstance(adapter, dict):
        errors.append("adapter evidence is missing")
        adapter = {}
    adapter_verdict = adapter.get("verdict")
    if (
        not isinstance(adapter_verdict, dict)
        or adapter_verdict.get("passed") is not True
        or adapter_verdict.get("errors") != []
    ):
        errors.append("adapter verdict is not a clean pass")
    errors.extend(_process_errors(adapter.get("capture"), "subject"))
    capture = adapter.get("capture")
    if not isinstance(capture, dict) or capture.get("returncode") != 0:
        errors.append("subject process did not return zero")

    outcome = adapter.get("outcome")
    if (
        not isinstance(outcome, dict)
        or outcome.get("passed") is not True
        or outcome.get("errors") != []
    ):
        errors.append("outcome oracle is not a clean pass")
        outcome = {}

    changed = _changed_paths(adapter.get("workspace"))
    expected_changes = ["shared.txt"] if workload == "write" else ["slugger.py"]
    if changed != expected_changes:
        errors.append("workspace manifest does not prove the one allowed effect")

    if workload == "write":
        if (
            outcome.get("declared_effect") != "shared.txt"
            or outcome.get("effect_sha256") != outcome.get("expected_sha256")
        ):
            errors.append("write receipt does not prove the exact expected bytes")
    else:
        external = outcome.get("external_tests")
        sequence = outcome.get("subject_sequence")
        if (
            not isinstance(external, dict)
            or external.get("initial_returncode") != 1
            or external.get("final_returncode") != 0
        ):
            errors.append("repair outcome does not prove red then green")
        if not isinstance(sequence, dict) or not all(
            type(sequence.get(name)) is int
            for name in ("failed_command_index", "mutation_index", "passing_command_index")
        ) or not (
            sequence["failed_command_index"]
            < sequence["mutation_index"]
            < sequence["passing_command_index"]
        ):
            errors.append("repair receipt does not order red, edit, then green")
        oracle = adapter.get("oracle_evidence")
        if not isinstance(oracle, dict):
            errors.append("repair oracle process evidence is missing")
        else:
            initial = oracle.get("initial_test")
            final = oracle.get("final_test")
            if not isinstance(initial, dict) or initial.get("returncode") != 1:
                errors.append("initial external test did not return one")
            if not isinstance(final, dict) or final.get("returncode") != 0:
                errors.append("final external test did not return zero")
            errors.extend(_process_errors(initial, "initial external test"))
            errors.extend(_process_errors(final, "final external test"))

    actual_sha256 = digest_file(path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        errors.append("record digest does not match the recertification report")
    return {
        "subject": subject,
        "record": path.name,
        "record_sha256": actual_sha256,
        "passed": not errors,
        "credential_value_absent": credential_absent,
        "changed_paths": changed,
        "errors": errors,
    }


def scan_credential_value(paths: list[Path], credential: str | None) -> dict[str, Any]:
    """Prove the prepared gateway credential is absent from every retained file."""
    checked = []
    errors = []
    needle = credential.encode("utf-8") if credential else None
    for path in sorted(paths):
        try:
            metadata = path.stat()
            if metadata.st_size > MAX_RECORD_BYTES:
                raise ValueError("file exceeds the smoke byte limit")
            raw = path.read_bytes()
        except (OSError, ValueError) as error:
            errors.append(f"{path.name}: cannot scan retained file: {error}")
            continue
        checked.append(path.name)
        if needle and needle in raw:
            errors.append(f"{path.name}: prepared credential value is present")
    return {
        "checked": credential is not None,
        "files": checked,
        "passed": not errors,
        "errors": errors,
    }


def _finish(
    report_path: Path,
    report: dict[str, Any],
    *,
    status: str,
    exit_code: int,
) -> tuple[dict[str, Any], int]:
    report["status"] = status
    report["passed"] = exit_code == 0
    _write_json_exclusive(report_path, report)
    return report, exit_code


def execute(
    plan: dict[str, Any],
    record_dir: Path,
    *,
    config_path: Path,
    credential_file: Path | None = None,
    hermes_root: Path | None = None,
) -> tuple[dict[str, Any], int]:
    """Execute every guarded stage and retain a single audit report."""
    destination = record_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    report_path = destination / "smoke-report.json"
    subjects = tuple(plan["subjects"])
    report: dict[str, Any] = {
        **plan,
        "live": True,
        "model_calls_authorized": plan["live_subject_runs_planned"],
        "record_dir": str(destination),
        "live_subject_runs_started": 0,
    }

    try:
        local = preflight.prepare_environment(
            subjects,
            config_path=config_path,
            credential_file=credential_file,
            hermes_root=hermes_root,
        )
        initial_doctor = doctor.report(subjects)
    except (OSError, TypeError, preflight.PreflightError) as error:
        report["error"] = f"preflight: {error}"
        return _finish(report_path, report, status="preflight_error", exit_code=2)
    report["preflight"] = {**initial_doctor, "local": local}
    if initial_doctor.get("overall_status") != "ready":
        return _finish(report_path, report, status="preflight_blocked", exit_code=1)

    try:
        usage_before = usage_probe.snapshot()
    except usage_probe.ProbeError as error:
        report["error"] = f"usage before run: {error}"
        return _finish(report_path, report, status="usage_unknown", exit_code=3)
    try:
        _write_json_exclusive(destination / "usage-before.json", usage_before)
    except OSError as error:
        report["error"] = f"retain usage before run: {error}"
        return _finish(report_path, report, status="retention_error", exit_code=2)
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
        return _finish(report_path, report, status="usage_gate_blocked", exit_code=1)

    recertification_plan = recertify.build_plan(
        subjects, plan["workload"], plan["draws"]
    )
    recertification: dict[str, Any] | None = None
    recertification_status = 2
    execution_error: str | None = None
    try:
        recertification, recertification_status = recertify.execute(
            recertification_plan, destination
        )
    except (OSError, TypeError, ValueError) as error:
        execution_error = str(error)

    if recertification is not None:
        report["live_subject_runs_started"] = recertification.get(
            "live_subject_runs_started", 0
        )
        recertification_path = destination / "recertification-report.json"
        if recertification_path.is_file():
            report["recertification"] = {
                "report": recertification_path.name,
                "report_sha256": digest_file(recertification_path),
                "passed": recertification.get("passed") is True,
            }
        else:
            execution_error = "execution did not retain its recertification report"
    else:
        report["live_subject_runs_started"] = len(
            list(destination.glob(f"*-{plan['workload']}-*.json"))
        )
        report["error"] = f"execution: {execution_error}"

    usage_after_error: str | None = None
    try:
        usage_after = usage_probe.snapshot()
    except usage_probe.ProbeError as error:
        usage_after_error = str(error)
        report["usage"]["after_error"] = usage_after_error
    else:
        try:
            _write_json_exclusive(destination / "usage-after.json", usage_after)
        except OSError as error:
            usage_after_error = f"could not retain reading: {error}"
            report["usage"]["after_error"] = usage_after_error
        else:
            report["usage"]["after"] = usage_after
            report["usage"]["delta"] = usage_probe.delta(usage_before, usage_after)

    result_by_record = {
        row.get("record"): row
        for row in (recertification or {}).get("results", [])
        if isinstance(row, dict) and isinstance(row.get("record"), str)
    }
    credential = os.environ.get(usage_probe.KEY_ENV)
    receipts = []
    for subject in subjects:
        for draw in range(1, plan["draws"] + 1):
            name = f"{subject}-{plan['workload']}-{draw}.json"
            row = result_by_record.get(name, {})
            receipt = validate_receipt(
                destination / name,
                subject=subject,
                workload=plan["workload"],
                expected_sha256=row.get("record_sha256"),
                credential=credential,
            )
            if row.get("passed") is not True:
                receipt["errors"].append(
                    "recertification report does not retain a passing result"
                )
                receipt["passed"] = False
            receipts.append(receipt)
    report["receipts"] = receipts
    credential_scan = scan_credential_value(
        list(destination.glob("*.json")), credential
    )
    report["credential_scan"] = credential_scan

    try:
        postflight = doctor.report(subjects)
    except (OSError, TypeError, ValueError) as error:
        report["error"] = f"postflight: {error}"
        return _finish(report_path, report, status="postflight_error", exit_code=2)
    report["postflight"] = postflight
    if credential:
        report_bytes = json.dumps(report, sort_keys=True).encode("utf-8")
        if credential.encode("utf-8") in report_bytes:
            credential_scan["errors"].append(
                "smoke-report.json: prepared credential value would be present"
            )
            credential_scan["passed"] = False
        credential_scan["files"].append("smoke-report.json")
    report["credential_value_absent"] = credential_scan["passed"] and all(
        row["credential_value_absent"] is True for row in receipts
    )
    if usage_after_error is not None:
        report["error"] = f"usage after run: {usage_after_error}"
        return _finish(report_path, report, status="usage_unknown", exit_code=3)
    if execution_error is not None:
        report["error"] = f"execution: {execution_error}"
        return _finish(report_path, report, status="execution_error", exit_code=2)
    if recertification_status != 0:
        return _finish(report_path, report, status="execution_failed", exit_code=1)
    if not all(row["passed"] for row in receipts):
        return _finish(report_path, report, status="receipt_validation_failed", exit_code=1)
    if not report["credential_value_absent"]:
        return _finish(report_path, report, status="credential_scan_failed", exit_code=1)
    if postflight.get("overall_status") != "ready":
        return _finish(report_path, report, status="postflight_failed", exit_code=1)
    return _finish(report_path, report, status="passed", exit_code=0)


def _limits(overrides: list[str]) -> dict[str, int]:
    limits = dict(DEFAULT_LIMITS)
    limits.update(usage_probe._limits(overrides))
    return limits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subject", action="append", choices=doctor.SUBJECTS)
    parser.add_argument("--workload", default="repair", choices=("write", "repair"))
    parser.add_argument("--draws", type=int, default=1)
    parser.add_argument(
        "--max", action="append", default=[], metavar="WINDOW=PCT",
        help="override a default usage gate (rolling=80, weekly=90)",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="execute the plan; without this flag no credential or subject is used",
    )
    parser.add_argument(
        "--record-dir", type=Path,
        help="required with --live; the directory must not already exist",
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
        limits = _limits(args.max)
        plan = build_plan(
            tuple(args.subject or doctor.SUBJECTS),
            args.workload,
            args.draws,
            limits,
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
