#!/usr/bin/env python3
"""Independently review a retained certification candidate without promoting it."""
from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
_CHECKOUT_SOURCE = HERE.parents[1]
if (_CHECKOUT_SOURCE / "harness_workbench" / "__init__.py").is_file():
    sys.path.insert(0, str(_CHECKOUT_SOURCE.resolve()))

import certify
from harness_workbench.canon import digest_file, digest_tree
from harness_workbench.capture import Bounded, minimal_environment
import usage_probe


SCHEMA = "cross-harness-certification-review/v0.1"
SUBJECTS = certify.SUBJECTS
WORKLOAD = certify.WORKLOAD
MAX_FILE_BYTES = certify.MAX_SCAN_FILE_BYTES
MAX_CAPTURE_BYTES = certify.MAX_CAPTURE_BYTES
ROUTE_SCHEMA = "cross-harness-provider-route-canary/v0.1"
TARGET_SCHEMA = "cross-harness-live-certification/v0.1"
COMPARISON_SCHEMA = "cross-harness-contract-comparison/v0.1"
TARGET_KEYS = {
    "schema",
    "certified_date",
    "workload",
    "draws_per_subject",
    "comparator_sha256",
    "contract_passed",
    "inputs",
    "apparatus_modules",
    "subjects",
}
REQUIRED_WORKFLOW_MODULES = {
    "certify.py",
    "doctor.py",
    "preflight.py",
    "route_canary.py",
    "usage_probe.py",
}
OPTIONAL_WORKFLOW_MODULES = {"review_candidate.py"}
PATCH_TARGET = Path("src/harness_workbench/subjects/adapter_certification.json")


class ReviewError(ValueError):
    """A candidate cannot be reviewed without guessing."""


def _regular_file(path: Path, label: str, *, executable: bool = False) -> Path:
    expanded = path.expanduser()
    try:
        mode = expanded.lstat().st_mode
    except OSError as error:
        raise ReviewError(f"{label} is not inspectable: {expanded}") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ReviewError(f"{label} must be a regular non-symlink file: {expanded}")
    resolved = expanded.resolve()
    if executable and not os.access(resolved, os.X_OK):
        raise ReviewError(f"{label} is not executable: {resolved}")
    try:
        if resolved.stat().st_size > MAX_FILE_BYTES:
            raise ReviewError(f"{label} exceeds the review byte limit: {resolved}")
    except OSError as error:
        raise ReviewError(f"{label} size cannot be read: {resolved}") from error
    return resolved


def _json_object(path: Path, label: str) -> dict[str, Any]:
    regular = _regular_file(path, label)
    try:
        payload = json.loads(regular.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReviewError(f"{label} is not readable JSON: {regular}") from error
    if not isinstance(payload, dict):
        raise ReviewError(f"{label} must contain a JSON object: {regular}")
    return payload


def _safe_relative(root: Path, value: object, label: str) -> Path:
    root = root.resolve()
    if not isinstance(value, str) or not value:
        raise ReviewError(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReviewError(f"{label} escapes the retained record: {value}")
    resolved = (root / relative).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ReviewError(f"{label} escapes the retained record: {value}") from error
    return resolved


def _source_paths(source_root: Path) -> dict[str, Path]:
    package = source_root / "harness_workbench"
    subjects = package / "subjects"
    if not (package / "__init__.py").is_file() or not subjects.is_dir():
        raise ReviewError(
            f"source root does not contain a materialized Workbench tree: {source_root}"
        )
    return {
        "source_root": source_root,
        "package": package,
        "subjects": subjects,
        "comparator": subjects / "compare.py",
        "target": subjects / "adapter_certification.json",
        "gitleaks_config": subjects / ".gitleaks.toml",
    }


def build_plan(
    candidate_path: Path,
    *,
    source_root: Path | None = None,
    target: Path | None = None,
) -> dict[str, Any]:
    """Resolve an offline review while authorizing no calls and no promotion."""
    candidate_file = _regular_file(candidate_path, "candidate manifest")
    candidate = _json_object(candidate_file, "candidate manifest")
    apparatus = candidate.get("apparatus")
    if not isinstance(apparatus, dict):
        raise ReviewError("candidate manifest has no apparatus object")
    selected_source = source_root or Path(str(apparatus.get("source_root", "")))
    resolved_source = selected_source.expanduser().resolve()
    paths = _source_paths(resolved_source)
    selected_target = target or paths["target"]
    target_file = _regular_file(selected_target, "promotion target")

    python_row = apparatus.get("python")
    gitleaks_row = apparatus.get("gitleaks")
    if not isinstance(python_row, dict) or not isinstance(gitleaks_row, dict):
        raise ReviewError("candidate apparatus lacks Python or gitleaks binding")
    python = _regular_file(
        Path(str(python_row.get("path", ""))), "candidate Python", executable=True
    )
    gitleaks = _regular_file(
        Path(str(gitleaks_row.get("path", ""))), "candidate gitleaks", executable=True
    )
    record_root = candidate_file.parent.resolve()
    run_root = record_root / "runs"
    if not run_root.is_dir():
        raise ReviewError(f"candidate record has no runs directory: {run_root}")
    return {
        "schema": SCHEMA,
        "review": False,
        "model_calls_authorized": 0,
        "network_calls_authorized": 0,
        "promotion_authorized": False,
        "fresh_review_dir_required": True,
        "candidate": str(candidate_file),
        "candidate_sha256": digest_file(candidate_file),
        "record_root": str(record_root),
        "source_root": str(resolved_source),
        "target": str(target_file),
        "python": str(python),
        "gitleaks": str(gitleaks),
        "subjects": list(SUBJECTS),
        "commands": {
            "verify_each": [
                str(python),
                "-m",
                "harness_workbench",
                "--root",
                str(run_root),
                "verify",
                "<run-id>",
            ],
            "compare_exact_five": [
                str(python),
                str(paths["comparator"].resolve()),
                *(f"{run_root}/<{subject}-run-id>" for subject in SUBJECTS),
            ],
            "gitleaks": [
                str(gitleaks),
                "dir",
                "--config",
                str(paths["gitleaks_config"].resolve()),
                str(record_root),
            ],
        },
        "outputs": [
            "promotion-review.json",
            "adapter-certification.proposed.json",
            "adapter-certification.patch",
            "comparison-replayed.json",
            "gitleaks-replayed.json",
            "process/",
        ],
        "policy": "review evidence and propose bytes; never edit or apply promotion",
    }


def _candidate_shape_errors(candidate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if candidate.get("schema") != certify.CANDIDATE_SCHEMA:
        errors.append("candidate schema is not recognized")
    if candidate.get("review_status") != "candidate":
        errors.append("candidate review_status is not candidate")
    if candidate.get("eligible_for_review") is not True:
        errors.append("candidate is not eligible for review")
    if candidate.get("eligibility_errors") != []:
        errors.append("candidate carries eligibility errors")
    if candidate.get("workload") != WORKLOAD:
        errors.append("candidate workload is not repair")
    if candidate.get("subjects") != list(SUBJECTS):
        errors.append("candidate subject order is not the exact five")
    if candidate.get("draws_per_subject") != certify.DRAWS:
        errors.append("candidate draw count is not three")
    calls = candidate.get("calls")
    if not isinstance(calls, dict):
        errors.append("candidate has no call accounting")
    else:
        expected = {
            "nominal": certify.NOMINAL_CALLS,
            "maximum": certify.MAXIMUM_CALLS,
        }
        for name, value in expected.items():
            if calls.get(name) != value:
                errors.append(f"candidate {name} call bound changed")
        started = calls.get("started")
        if (
            type(started) is not int
            or not certify.NOMINAL_CALLS <= started <= certify.MAXIMUM_CALLS
        ):
            errors.append("candidate total call count is outside its bound")
        canary = calls.get("provider_route_canary")
        matrix = calls.get("repair_matrix")
        if not isinstance(canary, dict) or canary != {
            "nominal": certify.ROUTE_CANARY_CALLS,
            "maximum": certify.ROUTE_CANARY_CALLS,
            "started": certify.ROUTE_CANARY_CALLS,
        }:
            errors.append("candidate did not retain exactly three canary calls")
        if (
            not isinstance(matrix, dict)
            or matrix.get("nominal") != certify.MATRIX_NOMINAL_CALLS
            or matrix.get("maximum") != certify.MATRIX_MAXIMUM_CALLS
            or type(matrix.get("started")) is not int
        ):
            errors.append("candidate matrix call accounting is missing")
        elif (
            type(started) is int
            and started != matrix["started"] + certify.ROUTE_CANARY_CALLS
        ):
            errors.append("candidate call subtotals do not equal the total")
    promotion = candidate.get("promotion")
    if not isinstance(promotion, dict):
        errors.append("candidate has no promotion guard")
    else:
        if (
            promotion.get("performed") is not False
            or promotion.get("review_required") is not True
        ):
            errors.append("candidate does not prove promotion was withheld")
        before = promotion.get("target_sha256_before")
        after = promotion.get("target_sha256_after")
        if not isinstance(before, str) or before != after:
            errors.append("candidate promotion target changed during certification")
        declared_target = promotion.get("target")
        target_path = (
            Path(declared_target) if isinstance(declared_target, str) else None
        )
        if (
            target_path is None
            or not target_path.is_absolute()
            or target_path.name != "adapter_certification.json"
            or target_path.parent.name != "subjects"
        ):
            errors.append(
                "candidate promotion target is not the certification manifest"
            )
    return errors


def _target_errors(target: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(target) != TARGET_KEYS:
        errors.append("promotion target fields do not match the certification schema")
    if target.get("schema") != TARGET_SCHEMA:
        errors.append("promotion target schema is not recognized")
    if target.get("workload") != WORKLOAD:
        errors.append("promotion target workload is not repair")
    if target.get("draws_per_subject") != certify.DRAWS:
        errors.append("promotion target draw count is not three")
    subjects = target.get("subjects")
    if not isinstance(subjects, dict) or set(subjects) != set(SUBJECTS):
        errors.append("promotion target subject map is not the exact five")
    return errors


def _digest_errors(
    candidate: dict[str, Any], source_root: Path, record_root: Path
) -> list[str]:
    errors: list[str] = []
    paths = _source_paths(source_root)
    subjects = paths["subjects"]
    maps: tuple[tuple[str, object, dict[str, Path]], ...] = (
        (
            "input",
            candidate.get("inputs"),
            {name: subjects / name for name in certify.adapters.REPAIR_INPUTS},
        ),
        (
            "spec",
            candidate.get("specs"),
            {subject: subjects / f"repair_{subject}.json" for subject in SUBJECTS},
        ),
        (
            "apparatus module",
            candidate.get("apparatus", {}).get("modules"),
            {
                "canon": paths["package"] / "canon.py",
                "capture": paths["package"] / "capture.py",
            },
        ),
    )
    for label, declared, actual in maps:
        if not isinstance(declared, dict) or set(declared) != set(actual):
            errors.append(f"candidate {label} map does not match the expected names")
            continue
        for name, path in actual.items():
            try:
                found = digest_file(_regular_file(path, f"{label} {name}"))
            except ReviewError as error:
                errors.append(str(error))
                continue
            if declared.get(name) != found:
                errors.append(f"{label} digest drift: {name}")

    for subject in SUBJECTS:
        spec_path = subjects / f"repair_{subject}.json"
        try:
            document = _json_object(spec_path, f"{subject} repair spec")
            certify._validate_spec_document(subject, document)
        except ValueError as error:
            errors.append(f"{subject} repair spec validation failed: {error}")

    workflow = candidate.get("apparatus", {}).get("workflow_modules")
    if not isinstance(workflow, dict):
        errors.append("candidate workflow module map is missing")
    else:
        names = set(workflow)
        if (
            not all(isinstance(name, str) for name in names)
            or not REQUIRED_WORKFLOW_MODULES <= names
            or names - REQUIRED_WORKFLOW_MODULES - OPTIONAL_WORKFLOW_MODULES
        ):
            errors.append("candidate workflow module names are not recognized")
        else:
            for name in sorted(names):
                try:
                    found = digest_file(
                        _regular_file(subjects / name, f"workflow module {name}")
                    )
                except ReviewError as error:
                    errors.append(str(error))
                    continue
                if workflow.get(name) != found:
                    errors.append(f"workflow module digest drift: {name}")

    comparator = candidate.get("comparator")
    if not isinstance(comparator, dict):
        errors.append("candidate comparator binding is missing")
    else:
        try:
            current = digest_file(_regular_file(paths["comparator"], "comparator"))
        except ReviewError as error:
            errors.append(str(error))
        else:
            if comparator.get("program_sha256") != current:
                errors.append("comparator program digest drift")

    apparatus = candidate.get("apparatus")
    if isinstance(apparatus, dict):
        python_row = apparatus.get("python")
        gitleaks_row = apparatus.get("gitleaks")
        if isinstance(python_row, dict):
            try:
                actual = digest_file(
                    _regular_file(
                        Path(str(python_row.get("path", ""))), "candidate Python"
                    )
                )
                if python_row.get("sha256") != actual:
                    errors.append("candidate Python digest drift")
            except ReviewError as error:
                errors.append(str(error))
        if isinstance(gitleaks_row, dict):
            for label, key, path in (
                ("gitleaks", "sha256", Path(str(gitleaks_row.get("path", "")))),
                ("gitleaks config", "config_sha256", paths["gitleaks_config"]),
            ):
                try:
                    actual = digest_file(_regular_file(path, label))
                    if gitleaks_row.get(key) != actual:
                        errors.append(f"candidate {label} digest drift")
                except ReviewError as error:
                    errors.append(str(error))

    records = candidate.get("records")
    if not isinstance(records, dict) or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in records.items()
    ):
        errors.append("candidate retained-record digest map is malformed")
    else:
        try:
            actual_records = certify._record_digests(record_root)
        except (OSError, ValueError) as error:
            errors.append(f"retained-record digest map cannot be reproduced: {error}")
        else:
            missing = sorted(set(records) - set(actual_records))
            extra = sorted(set(actual_records) - set(records))
            changed = sorted(
                name for name in set(records) & set(actual_records)
                if records[name] != actual_records[name]
            )
            if missing:
                errors.append(
                    f"candidate-bound retained records are missing: {missing}"
                )
            if extra:
                errors.append(f"unbound retained records are present: {extra}")
            if changed:
                errors.append(f"retained-record digest drift: {changed}")
    return errors


def _run_errors(
    candidate: dict[str, Any], record_root: Path
) -> tuple[list[str], dict[str, Path]]:
    errors: list[str] = []
    run_paths: dict[str, Path] = {}
    rows = candidate.get("runs")
    if not isinstance(rows, dict) or set(rows) != set(SUBJECTS):
        return ["candidate run map is not the exact five"], run_paths
    total_attempts = 0
    for subject in SUBJECTS:
        row = rows.get(subject)
        if not isinstance(row, dict):
            errors.append(f"{subject} run binding is missing")
            continue
        try:
            run_dir = _safe_relative(record_root, row.get("store"), f"{subject} store")
        except ReviewError as error:
            errors.append(str(error))
            continue
        if not run_dir.is_dir() or run_dir.name != row.get("run_id"):
            errors.append(f"{subject} run directory does not match its run id")
            continue
        for path in run_dir.rglob("*"):
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                errors.append(f"{subject} run path cannot be inspected: {error}")
                continue
            if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                errors.append(f"{subject} run contains a non-regular retained path")
        checks = {
            "store_sha256": digest_tree(run_dir),
            "record_sha256": (
                digest_file(run_dir / "record.json")
                if (run_dir / "record.json").is_file() else None
            ),
            "integrity_sha256": (
                digest_file(run_dir / "integrity.json")
                if (run_dir / "integrity.json").is_file() else None
            ),
        }
        for name, actual in checks.items():
            if row.get(name) != actual:
                errors.append(f"{subject} {name} does not match retained bytes")
        try:
            attempts = certify._attempt_count(run_dir)
        except ValueError as error:
            errors.append(str(error))
        else:
            total_attempts += attempts
            if row.get("attempts_retained") != attempts:
                errors.append(f"{subject} attempt count does not match retained bytes")
        if row.get("verify_passed") is not True:
            errors.append(
                f"{subject} candidate does not carry a passing verify receipt"
            )
        run_paths[subject] = run_dir
    calls = candidate.get("calls", {}).get("repair_matrix", {}).get("started")
    if type(calls) is int and total_attempts != calls:
        errors.append("retained attempts do not equal matrix calls started")
    return errors, run_paths


def _security_receipt_errors(candidate: dict[str, Any], record_root: Path) -> list[str]:
    errors: list[str] = []
    security = candidate.get("security")
    if not isinstance(security, dict) or security.get("gitleaks_passed") is not True:
        errors.append("candidate does not carry a passing gitleaks verdict")
        return errors
    try:
        scan_path = _safe_relative(
            record_root, security.get("credential_scan"), "credential scan"
        )
        scan = _json_object(scan_path, "credential scan")
    except ReviewError as error:
        return [str(error)]
    if (
        scan.get("schema") != "cross-harness-credential-scan/v0.1"
        or scan.get("passed") is not True
        or scan.get("errors") != []
        or type(scan.get("credential_values_checked")) is not int
        or scan.get("credential_values_checked", 0) < 1
    ):
        errors.append("credential scan receipt is not a clean positive result")
    retained: list[str] = []
    for path in sorted(record_root.rglob("*")):
        relative = str(path.relative_to(record_root))
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            errors.append(f"{relative}: retained path cannot be inspected: {error}")
            continue
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            errors.append(f"{relative}: retained path is not a regular file")
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"{relative}: retained file exceeds the review byte limit")
        retained.append(relative)
    if scan.get("files") != retained:
        errors.append("credential scan file coverage does not match retained evidence")
    return errors


def _route_row_passed(subject: str, row: object) -> bool:
    if not isinstance(row, dict) or row.get("subject") != subject:
        return False
    receipt = row.get("receipt")
    cleanup = row.get("render_cleanup")
    return bool(
        isinstance(receipt, dict)
        and receipt.get("passed") is True
        and receipt.get("status") == 200
        and receipt.get("error") is None
        and receipt.get("connection_closed_after_first_event") is True
        and receipt.get("redirects_followed") is False
        and isinstance(cleanup, dict)
        and cleanup.get("adapter_process_group_clean") is True
        and cleanup.get("server_thread_stopped") is True
    )


def _canary_usage_errors(
    candidate: dict[str, Any], record_root: Path
) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    canary = candidate.get("provider_route_canary")
    if not isinstance(canary, dict):
        errors.append("candidate provider-route canary binding is missing")
    else:
        if (
            canary.get("calls_started") != certify.ROUTE_CANARY_CALLS
            or canary.get("passed") is not True
            or canary.get("status") != "passed"
            or canary.get("store") != "route-canary"
            or canary.get("report") != "route-canary/route-canary-report.json"
        ):
            errors.append("candidate canary summary is not a clean exact-three result")
        try:
            report_path = _safe_relative(
                record_root, canary.get("report"), "canary report"
            )
            report = _json_object(report_path, "canary report")
        except ReviewError as error:
            errors.append(str(error))
        else:
            if canary.get("report_sha256") != digest_file(report_path):
                errors.append("canary report digest does not match retained bytes")
            routes = report.get("routes")
            if (
                report.get("schema") != ROUTE_SCHEMA
                or report.get("passed") is not True
                or report.get("status") != "passed"
                or report.get("model_calls_started") != certify.ROUTE_CANARY_CALLS
                or not isinstance(routes, dict)
                or set(routes) != {"deepseek", "hermes", "pi"}
                or not all(
                    _route_row_passed(subject, routes[subject])
                    for subject in ("deepseek", "hermes", "pi")
                )
            ):
                errors.append("provider-route canary is not a clean exact-three result")

    usage = candidate.get("usage")
    after: dict[str, Any] | None = None
    if not isinstance(usage, dict):
        return errors + ["candidate usage binding is missing"], after
    try:
        before = _json_object(
            _safe_relative(record_root, usage.get("before"), "usage before"),
            "usage before",
        )
        after = _json_object(
            _safe_relative(record_root, usage.get("after"), "usage after"),
            "usage after",
        )
    except ReviewError as error:
        return errors + [str(error)], after
    if (
        before.get("schema") != usage_probe.SCHEMA
        or after.get("schema") != usage_probe.SCHEMA
        or before.get("metered") is not True
        or after.get("metered") is not True
    ):
        errors.append("retained usage snapshots are not readable metered evidence")
    if usage.get("delta") != usage_probe.delta(before, after):
        errors.append("usage delta does not reproduce from retained snapshots")
    limits = usage.get("limits")
    if limits != certify.DEFAULT_LIMITS:
        errors.append("candidate usage limits are not the certified stop thresholds")
    elif usage_probe.gate(after, limits)[0] is not True:
        errors.append("post-run usage does not pass the retained limits")
    return errors, after


def _proposed_certification(
    candidate: dict[str, Any], comparison: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    timestamp = after.get("read_at")
    try:
        certified_date = datetime.date.fromisoformat(str(timestamp)[:10]).isoformat()
    except ValueError as error:
        raise ReviewError("usage-after read_at does not contain an ISO date") from error
    subjects: dict[str, Any] = {}
    rows = comparison.get("subjects")
    if not isinstance(rows, dict):
        raise ReviewError("comparison has no subject rows")
    for subject in SUBJECTS:
        run = candidate["runs"][subject]
        row = rows[subject]
        subjects[subject] = {
            "run_id": run["run_id"],
            "record_sha256": run["record_sha256"],
            "adapter": f"{row['adapter_passed']}/{row['draws']}",
            "outcome": f"{row['outcome_passed']}/{row['draws']}",
            "timeouts": row["timed_out"],
        }
    return {
        "schema": TARGET_SCHEMA,
        "certified_date": certified_date,
        "workload": candidate["workload"],
        "draws_per_subject": candidate["draws_per_subject"],
        "comparator_sha256": candidate["comparator"]["result_sha256"],
        "contract_passed": comparison["contract_passed"],
        "inputs": candidate["inputs"],
        "apparatus_modules": candidate["apparatus"]["modules"],
        "subjects": subjects,
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=False) + "\n").encode("utf-8")


def _patch_bytes(target: Path, before: bytes, after: bytes) -> bytes:
    patch = difflib.unified_diff(
        before.decode("utf-8").splitlines(keepends=True),
        after.decode("utf-8").splitlines(keepends=True),
        fromfile=str(target),
        tofile=str(target),
    )
    return "".join(patch).encode("utf-8")


def _process_ok(result: Bounded) -> bool:
    return bool(
        result.returncode == 0
        and result.termination_reason is None
        and not result.stdout_overflow
        and not result.stderr_overflow
        and not result.group_alive_after_cleanup
    )


def execute(plan: dict[str, Any], review_dir: Path) -> tuple[dict[str, Any], int]:
    """Retain one bounded offline review and never mutate the promotion target."""
    destination = review_dir.expanduser().resolve()
    record_root = Path(plan["record_root"]).resolve()
    try:
        destination.relative_to(record_root)
    except ValueError:
        pass
    else:
        raise ReviewError("review directory must be outside the retained candidate record")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    process_root = destination / "process"
    process_root.mkdir(mode=0o700)
    report: dict[str, Any] = {
        **plan,
        "review": True,
        "review_dir": str(destination),
        "processes": [],
        "errors": [],
        "promotion": {
            "performed": False,
            "automatic": False,
            "patch_applied": False,
            "review_required": True,
            "target": plan["target"],
        },
    }
    errors: list[str] = report["errors"]
    operational = False
    try:
        candidate_path = _regular_file(Path(plan["candidate"]), "candidate manifest")
        if digest_file(candidate_path) != plan["candidate_sha256"]:
            raise ReviewError("candidate manifest changed after planning")
        candidate = _json_object(candidate_path, "candidate manifest")
        source_root = Path(plan["source_root"]).resolve()
        target = _regular_file(Path(plan["target"]), "promotion target")
        target_before = target.read_bytes()
        target_before_digest = digest_file(target)
        report["promotion"]["target_sha256_before"] = target_before_digest

        errors.extend(_candidate_shape_errors(candidate))
        shape_passed = not errors
        target_errors = _target_errors(_json_object(target, "promotion target"))
        errors.extend(target_errors)
        digest_errors = _digest_errors(candidate, source_root, record_root)
        errors.extend(digest_errors)
        run_errors, run_paths = _run_errors(candidate, record_root)
        errors.extend(run_errors)
        security_errors = _security_receipt_errors(candidate, record_root)
        errors.extend(security_errors)
        canary_errors, after_usage = _canary_usage_errors(candidate, record_root)
        errors.extend(canary_errors)
        report["validation"] = {
            "candidate_shape": shape_passed,
            "promotion_target_shape": not target_errors,
            "input_apparatus_and_record_digests": not digest_errors,
            "exact_five_run_store_digests": not run_errors,
            "credential_scan_coverage": not security_errors,
            "provider_canary_and_usage": not canary_errors,
            "retained_record_digests": len(candidate.get("records", {})),
            "credential_scan_files": len(
                _json_object(
                    _safe_relative(
                        record_root,
                        candidate.get("security", {}).get("credential_scan"),
                        "credential scan",
                    ),
                    "credential scan",
                ).get("files", [])
            ),
        }

        child_env = minimal_environment(
            destination,
            {"PYTHONPATH": str(source_root)},
        )
        python = plan["python"]
        runs_root = record_root / "runs"
        process_index = 0
        for subject in SUBJECTS:
            run_dir = run_paths.get(subject)
            if run_dir is None:
                continue
            result, receipt = certify._run_command(
                process_root,
                process_index,
                f"verify-{subject}",
                [
                    python,
                    "-m",
                    "harness_workbench",
                    "--root",
                    str(runs_root),
                    "verify",
                    run_dir.name,
                ],
                cwd=source_root,
                env=child_env,
                timeout=60,
                stdout_limit=1024 * 1024,
                stderr_limit=1024 * 1024,
            )
            process_index += 1
            report["processes"].append(receipt)
            if not _process_ok(result):
                errors.append(f"{subject} independent hwb verify did not pass cleanly")

        comparison: dict[str, Any] | None = None
        if set(run_paths) == set(SUBJECTS):
            comparator = _source_paths(source_root)["comparator"].resolve()
            result, receipt = certify._run_command(
                process_root,
                process_index,
                "compare-exact-five",
                [python, str(comparator), *(str(run_paths[name]) for name in SUBJECTS)],
                cwd=source_root,
                env=child_env,
                timeout=120,
            )
            process_index += 1
            report["processes"].append(receipt)
            if not _process_ok(result):
                errors.append("independent exact-five comparator did not pass cleanly")
            else:
                try:
                    loaded = json.loads(result.stdout)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    errors.append(f"replayed comparator output is not JSON: {error}")
                else:
                    if isinstance(loaded, dict):
                        comparison = loaded
                        certify._write_bytes_exclusive(
                            destination / "comparison-replayed.json", result.stdout
                        )
                    else:
                        errors.append("replayed comparator output is not an object")
            replayed_digest = "sha256:" + hashlib.sha256(result.stdout).hexdigest()
            if replayed_digest != candidate.get("comparator", {}).get("result_sha256"):
                errors.append("replayed comparator bytes do not match the candidate")
            if comparison is not None:
                if comparison.get("schema") != COMPARISON_SCHEMA:
                    errors.append("replayed comparator schema is not recognized")
                errors.extend(certify._comparison_eligible(comparison))

        gitleaks_report = destination / "gitleaks-replayed.json"
        apparatus = candidate.get("apparatus", {}).get("gitleaks", {})
        gitleaks = plan["gitleaks"]
        result, receipt = certify._run_command(
            process_root,
            process_index,
            "gitleaks-replay",
            [
                gitleaks,
                "dir",
                "--no-banner",
                "--redact=100",
                "--max-target-megabytes",
                str(MAX_FILE_BYTES // (1024 * 1024)),
                "--timeout",
                "120",
                "--config",
                str(_source_paths(source_root)["gitleaks_config"].resolve()),
                "--report-format",
                "json",
                "--report-path",
                str(gitleaks_report),
                str(record_root),
            ],
            cwd=source_root,
            env=child_env,
            timeout=150,
            stdout_limit=1024 * 1024,
            stderr_limit=1024 * 1024,
        )
        report["processes"].append(receipt)
        if not _process_ok(result) or not gitleaks_report.is_file():
            errors.append("independent gitleaks replay did not pass cleanly")
        report["security"] = {
            "credential_scan_receipt_reproduced": not _security_receipt_errors(
                candidate, record_root
            ),
            "gitleaks_replayed": _process_ok(result) and gitleaks_report.is_file(),
            "gitleaks_program_sha256": apparatus.get("sha256"),
        }

        if comparison is not None and after_usage is not None and not errors:
            proposed = _proposed_certification(candidate, comparison, after_usage)
            proposed_bytes = _json_bytes(proposed)
            proposed_digest = "sha256:" + hashlib.sha256(proposed_bytes).hexdigest()
            candidate_target = candidate["promotion"]["target_sha256_before"]
            if target_before_digest == proposed_digest:
                state = "already_promoted"
            elif target_before_digest == candidate_target:
                state = "proposal_ready"
            else:
                errors.append(
                    "promotion target drifted since certification; rebase review"
                )
                state = "target_drift"
            if not errors:
                proposal_path = destination / "adapter-certification.proposed.json"
                patch_path = destination / "adapter-certification.patch"
                patch_bytes = _patch_bytes(PATCH_TARGET, target_before, proposed_bytes)
                certify._write_bytes_exclusive(proposal_path, proposed_bytes)
                certify._write_bytes_exclusive(patch_path, patch_bytes)
                report["proposal"] = {
                    "state": state,
                    "document": proposal_path.name,
                    "document_sha256": proposed_digest,
                    "patch": patch_path.name,
                    "patch_sha256": digest_file(patch_path),
                    "patch_bytes": len(patch_bytes),
                    "apply_automatically": False,
                }

        report["promotion"]["target_sha256_after"] = digest_file(target)
        report["promotion"]["target_unchanged"] = (
            report["promotion"]["target_sha256_after"] == target_before_digest
        )
        if not report["promotion"]["target_unchanged"]:
            errors.append("promotion target changed during offline review")
    except (OSError, TypeError, KeyError, ValueError) as error:
        operational = True
        errors.append(f"review operation failed: {error}")

    report["cleanup"] = {
        "all_process_groups_clean": all(
            process.get("cleanup_passed") is True for process in report["processes"]
        ),
        "processes_observed": len(report["processes"]),
    }
    if not report["cleanup"]["all_process_groups_clean"]:
        errors.append("one or more review process groups did not clean up")
    report["passed"] = not errors and "proposal" in report
    report["status"] = (
        "review_complete" if report["passed"]
        else "operational_failure" if operational
        else "candidate_rejected"
    )
    report_path = destination / "promotion-review.json"
    certify._write_json_exclusive(report_path, report)
    return report, 0 if report["passed"] else 2 if operational else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument(
        "--review",
        action="store_true",
        help="execute the offline review; default is a zero-write plan",
    )
    parser.add_argument(
        "--review-dir",
        type=Path,
        help="fresh retained output directory required with --review",
    )
    args = parser.parse_args()
    if args.review != (args.review_dir is not None):
        parser.error("--review and --review-dir must be supplied together")
    try:
        plan = build_plan(
            args.candidate,
            source_root=args.source_root,
            target=args.target,
        )
        if not args.review:
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
        report, status = execute(plan, args.review_dir)
    except (OSError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
