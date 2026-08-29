#!/usr/bin/env python3
"""Independent replay validation for retained declarative-task evidence."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from agent_task_archives import (
    apply_effects_archive,
    extract_workspace_archive,
    snapshot_tree,
    validate_archive,
)
from agent_task_routes import normalize_fake_route
from agent_task_schema import (
    EFFECTS_SCHEMA,
    SUBJECTS,
    WORKSPACE_SCHEMA,
    bytes_sha256,
    canonical_sha256,
    validate_run,
    validate_task,
)


def _assertions(entries: list[dict[str, Any]], expected: list[dict[str, Any]]) -> list[str]:
    current = {row["path"]: row for row in entries}
    errors: list[str] = []
    for wanted in expected:
        observed = current.get(wanted["path"])
        if wanted["kind"] == "absent":
            if observed is not None:
                errors.append(f"expected absent path exists: {wanted['path']}")
        elif observed != wanted:
            errors.append(f"verification mismatch: {wanted['path']}")
    return errors


def validate_retained_run(
    run: dict[str, Any],
    *,
    task: dict[str, Any],
    workspace_archive: bytes,
) -> dict[str, Any]:
    errors: list[str] = []
    try:
        validate_task(task)
        validate_run(run)
    except ValueError as error:
        return {"passed": False, "errors": [str(error)]}
    if run["task_sha256"] != canonical_sha256(task):
        errors.append("run task digest disagrees with retained task")
    if run["input_archive_sha256"] != bytes_sha256(workspace_archive):
        errors.append("run archive digest disagrees with retained archive")
    try:
        workspace_manifest = validate_archive(workspace_archive, WORKSPACE_SCHEMA)
    except ValueError as error:
        errors.append(str(error))
        workspace_manifest = {"entries": []}
    if run["workspace"]["before"] != workspace_manifest["entries"]:
        errors.append("run before-manifest disagrees with retained input archive")

    effects_field = run["effects_archive"]
    try:
        effects_raw = base64.b64decode(effects_field["base64"], validate=True)
    except (ValueError, TypeError) as error:
        effects_raw = b""
        errors.append(f"effects archive base64 is invalid: {error}")
    if len(effects_raw) != effects_field["bytes"]:
        errors.append("effects archive byte count disagrees")
    if bytes_sha256(effects_raw) != effects_field["sha256"]:
        errors.append("effects archive digest disagrees")
    try:
        effects_manifest = validate_archive(effects_raw, EFFECTS_SCHEMA)
    except ValueError as error:
        errors.append(str(error))
        effects_manifest = {"entries": []}
    if effects_manifest["entries"] != task["effects_policy"]["operations"]:
        errors.append("retained effects do not equal the task policy")

    provider = run["provider"]
    for stream_name in ("stdout", "stderr"):
        stream = provider["capture"][stream_name]
        try:
            raw = base64.b64decode(stream["base64"], validate=True)
        except (ValueError, TypeError) as error:
            errors.append(f"provider {stream_name} base64 is invalid: {error}")
            continue
        if len(raw) != stream["bytes"] or hashlib.sha256(raw).hexdigest() != stream["sha256"]:
            errors.append(f"provider {stream_name} retained bytes disagree")
        if stream["redaction_count"] or stream["overflow"]:
            errors.append(f"provider {stream_name} is redacted or overflowed")
    try:
        stdout = base64.b64decode(provider["capture"]["stdout"]["base64"], validate=True)
        lifecycle = normalize_fake_route(run["subject"], stdout)
        if lifecycle != provider.get("lifecycle"):
            errors.append("provider lifecycle projection does not replay")
    except ValueError as error:
        errors.append(str(error))
    cleanup = provider["cleanup_receipt"]
    if cleanup.get("kind") != "clean_self_issued" or cleanup.get(
        "group_alive_after_cleanup"
    ) is not False:
        errors.append("provider cleanup receipt is not clean and self-issued")

    with tempfile.TemporaryDirectory(prefix="hwb-agent-review-") as raw:
        root = Path(raw)
        pre = root / "pre"
        post = root / "post"
        extracted = extract_workspace_archive(workspace_archive, pre)
        errors.extend(_assertions(extracted, task["verification"]["pre"]))
        extract_workspace_archive(workspace_archive, post)
        reconstructed = apply_effects_archive(effects_raw, post)
        if reconstructed != run["workspace"]["after"]:
            errors.append("effects reconstruction disagrees with run after-manifest")
        errors.extend(_assertions(reconstructed, task["verification"]["post"]))
        stable, _ = snapshot_tree(post)
        if stable != reconstructed:
            errors.append("independent verifier mutated reconstructed workspace")
    expected_verdict = not errors
    if run["verdict"]["adapter_valid"] is not expected_verdict:
        errors.append("producer adapter verdict disagrees with independent validation")
    if run["verdict"]["safety_eligible"] is not expected_verdict:
        errors.append("producer safety verdict disagrees with independent validation")
    if run["verdict"]["task_passed"] is not expected_verdict:
        errors.append("producer task verdict disagrees with independent validation")
    return {"passed": not errors, "errors": errors}


def scan_credentials(root: Path, values: tuple[str, ...]) -> dict[str, Any]:
    findings: list[str] = []
    encoded = [(value, value.encode("utf-8", errors="surrogatepass")) for value in values]
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        for value, raw in encoded:
            if value and value in relative:
                findings.append(f"credential value in filename: {relative}")
            if path.is_file() and raw and raw in path.read_bytes():
                findings.append(f"credential value in file: {relative}")
    return {"passed": not findings, "findings": findings}


def compare_exact_five(
    runs: list[dict[str, Any]],
    *,
    task: dict[str, Any],
    workspace_archive: bytes,
) -> dict[str, Any]:
    errors: list[str] = []
    if len(runs) != 5 or {run.get("subject") for run in runs} != set(SUBJECTS):
        errors.append("comparison requires exactly one run for each of the five subjects")
    subjects: dict[str, Any] = {}
    call_ids: list[int] = []
    for run in runs:
        subject = run.get("subject", "<invalid>")
        report = validate_retained_run(
            run, task=task, workspace_archive=workspace_archive
        )
        subjects[subject] = report
        errors.extend(f"{subject}: {error}" for error in report["errors"])
        attempt = run.get("base_attempt", {})
        if attempt.get("ordinal") != 0:
            errors.append(f"{subject}: base-attempt ordinal is not zero")
        if type(attempt.get("call_id")) is int:
            call_ids.append(attempt["call_id"])
    if sorted(call_ids) != [1, 2, 3, 4, 5]:
        errors.append("call-control IDs are not the exact contiguous five")
    return {
        "schema": "cross-harness-agent-task-comparison/v0.1",
        "passed": not errors,
        "errors": errors,
        "task_sha256": canonical_sha256(task),
        "input_archive_sha256": bytes_sha256(workspace_archive),
        "subjects": dict(sorted(subjects.items())),
    }
