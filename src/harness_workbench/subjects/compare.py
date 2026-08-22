#!/usr/bin/env python3
"""Validate and compare the five shared-contract candidate envelopes."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import harness_workbench
from harness_workbench import canon as canon_module
from harness_workbench import capture as capture_module
from harness_workbench.capture import digest_file, manifest, parse_jsonl
from harness_workbench.conform import NonConforming, validate_record
from oracles import EXPECTED_CONTENT
from runner import exit_status
from workloads import AMBIENT_CONFIG, GUARD_VARIANTS, WORKLOADS


SUBJECTS = {"claude", "codex", "deepseek", "hermes", "pi"}
HERE = Path(__file__).resolve().parent
EXPECTED_EFFECT_SHA = hashlib.sha256(EXPECTED_CONTENT).hexdigest()
PINS = json.loads((HERE / "pin.json").read_text(encoding="utf-8"))
MODEL_SELECTION = json.loads(
    (HERE / "model_selection.json").read_text(encoding="utf-8")
)
ATTEMPT_ARTIFACT_CONTRACT = "attempt-artifacts/0.1"


@dataclass(frozen=True)
class OutcomeState:
    passed: bool | None
    evaluable: bool
    oracle_key: tuple[str, ...]
    repair_sequence: tuple[int | None, int | None, int | None] | None


@dataclass(frozen=True)
class CaptureState:
    interrupted: bool
    timed_out: bool
    measurement_fault: bool


@dataclass(frozen=True)
class ApparatusState:
    key: str
    baseline_agrees: bool | None


@dataclass(frozen=True)
class LifecycleState:
    acquisition: str
    completeness: str
    tool_attempts: int
    tool_executions: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class RequestState:
    workload: str
    variant: str | None
    prompt_sha256: str
    input_key: str


@dataclass(frozen=True)
class NormalizedDraw:
    subject: str
    request: RequestState
    apparatus_key: str
    capabilities_key: str
    adapter_passed: bool
    outcome: OutcomeState
    capture: CaptureState
    lifecycle: LifecycleState
    capabilities: dict[str, Any]


def _string_list(value: Any) -> bool:
    return type(value) is list and all(type(item) is str for item in value)


def _sha256(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def verify_adapter_subject(
    label: str, subject: Any, errors: list[str]
) -> str | None:
    if not isinstance(subject, dict):
        errors.append(f"{label} adapter has an invalid subject: {subject!r}")
        return None
    name = subject.get("name")
    if type(name) is not str or name not in SUBJECTS:
        errors.append(f"{label} adapter has an invalid subject: {name!r}")
        return None
    if subject != _declared_subject_identity(name):
        errors.append(f"{label} adapter identity disagrees with declarations")
    return name


def _active_model_profile() -> tuple[str, dict[str, Any]]:
    name = MODEL_SELECTION["active"]
    return name, MODEL_SELECTION["profiles"][name]


def _declared_subject_identity(subject: str) -> dict[str, Any]:
    pin_name = {
        "claude": "claude_code",
        "codex": "codex_cli",
        "deepseek": "deepseek_harness",
        "hermes": "hermes_agent",
        "pi": "pi_coding_agent",
    }[subject]
    pin = PINS[pin_name]
    digest_key = "launcher_sha256" if subject == "hermes" else "executable_sha256"
    expected: dict[str, Any] = {
        "name": subject,
        "version": pin["version"],
        "executable_sha256": pin[digest_key],
        "model": pin.get("model"),
    }
    if subject == "deepseek":
        expected["provider"] = pin["provider"]
    if subject == "hermes":
        expected["source_commit"] = pin["source_commit"]
    if subject in {"deepseek", "hermes", "pi"}:
        profile_name, profile = _active_model_profile()
        expected.update({
            "model": profile["models"][subject],
            "model_profile": profile_name,
            "model_identity_strength": profile["identity_strength"],
            "model_base_url": profile["base_url"],
            "model_api_key_env": profile["api_key_env"],
            "model_subject_key_env": profile.get("subject_key_env", {}).get(
                subject, profile["api_key_env"]
            ),
        })
        if profile.get("verify_digest"):
            expected["model_digest"] = PINS["ollama"]["model_digest"]
    return expected


def verify_adapter_verdict(
    label: str, verdict: Any, errors: list[str]
) -> bool | None:
    if not isinstance(verdict, dict):
        errors.append(f"{label} adapter has an invalid verdict: {verdict!r}")
        return None
    passed = verdict.get("passed")
    complaints = verdict.get("errors")
    if type(passed) is not bool:
        errors.append(f"{label} adapter verdict has invalid passed: {passed!r}")
    if not _string_list(complaints):
        errors.append(
            f"{label} adapter verdict has invalid errors: {complaints!r}"
        )
    if type(passed) is not bool or not _string_list(complaints):
        return None
    if passed is not (len(complaints) == 0):
        errors.append(
            f"{label} adapter verdict contradicts its errors:"
            f" passed={passed!r}, errors={complaints!r}"
        )
    return passed


def verify_outcome_state(
    label: str,
    outcome: Any,
    workload: str,
    request_variant: Any,
    errors: list[str],
) -> OutcomeState | None:
    if not isinstance(outcome, dict):
        errors.append(f"{label} adapter has an invalid outcome: {outcome!r}")
        return None
    complaints = outcome.get("errors")
    evaluable = (
        outcome.get("evaluable")
        if workload == "guard"
        else outcome.get("evaluable", True)
    )
    passed = outcome.get("passed")
    declared_effect = outcome.get("declared_effect")
    repair_sequence = None
    valid = True
    if not _string_list(complaints):
        errors.append(f"{label} outcome has invalid errors: {complaints!r}")
        valid = False
    if type(declared_effect) is not str:
        errors.append(
            f"{label} outcome has invalid declared_effect: {declared_effect!r}"
        )
        valid = False
    if type(evaluable) is not bool:
        profile = "guard outcome" if workload == "guard" else "outcome"
        errors.append(
            f"{label} {profile} has invalid evaluable: {evaluable!r}"
        )
        valid = False
    elif evaluable and type(passed) is not bool:
        errors.append(f"{label} outcome has invalid passed: {passed!r}")
        valid = False
    elif not evaluable and passed is not None:
        errors.append(
            f"{label} non-evaluable outcome must have passed null: {passed!r}"
        )
        valid = False
    if workload != "guard" and evaluable is False:
        errors.append(f"{label} non-guard outcome cannot be non-evaluable")
        valid = False
    expected_sha = outcome.get("expected_sha256")
    if workload in {"write", "guard"}:
        if not _sha256(expected_sha):
            errors.append(
                f"{label} {workload} outcome has invalid expected_sha256:"
                f" {expected_sha!r}"
            )
            valid = False
        elif expected_sha != EXPECTED_EFFECT_SHA:
            errors.append(
                f"{label} {workload} outcome expected digest disagrees"
            )
            valid = False
    if workload == "write":
        effect_sha = outcome.get("effect_sha256")
        if declared_effect != "shared.txt":
            errors.append(
                f"{label} write outcome has invalid declared_effect:"
                f" {declared_effect!r}"
            )
            valid = False
        if effect_sha is not None and not _sha256(effect_sha):
            errors.append(
                f"{label} write outcome has invalid effect_sha256:"
                f" {effect_sha!r}"
            )
            valid = False
        if passed is True and effect_sha is None:
            errors.append(f"{label} passing write outcome has no effect digest")
            valid = False
        elif passed is True and effect_sha != expected_sha:
            errors.append(
                f"{label} passing write outcome effect digest disagrees"
            )
            valid = False
    elif workload == "guard":
        variant = outcome.get("variant")
        guard_loaded = outcome.get("guard_loaded")
        calls_seen = outcome.get("calls_seen")
        denials = outcome.get("denials")
        effect_present = outcome.get("effect_present")
        effect_landed = outcome.get("effect_landed")
        contained = outcome.get("contained")
        if declared_effect != "shared.txt":
            errors.append(
                f"{label} guard outcome has invalid declared_effect:"
                f" {declared_effect!r}"
            )
            valid = False
        if type(variant) is not str or variant not in GUARD_VARIANTS:
            errors.append(
                f"{label} guard outcome has invalid variant: {variant!r}"
            )
            valid = False
        elif variant != request_variant:
            errors.append(
                f"{label} guard outcome variant disagrees with request:"
                f" {variant!r} != {request_variant!r}"
            )
            valid = False
        if type(guard_loaded) is not bool:
            errors.append(
                f"{label} guard outcome has invalid guard_loaded:"
                f" {guard_loaded!r}"
            )
            valid = False
        elif type(evaluable) is bool and guard_loaded is not evaluable:
            errors.append(
                f"{label} guard outcome guard_loaded disagrees with evaluable"
            )
            valid = False
        if type(calls_seen) is not int or calls_seen < 0:
            errors.append(f"{label} guard outcome has invalid calls_seen")
            valid = False
        if type(denials) is not int or denials < 0:
            errors.append(f"{label} guard outcome has invalid denials")
            valid = False
        elif type(calls_seen) is int and denials > calls_seen:
            errors.append(f"{label} guard outcome denials exceed calls_seen")
            valid = False
        for field, value in (
            ("effect_present", effect_present),
            ("effect_landed", effect_landed),
        ):
            if type(value) is not bool:
                errors.append(
                    f"{label} guard outcome has invalid {field}: {value!r}"
                )
                valid = False
        if effect_landed is True and effect_present is not True:
            errors.append(
                f"{label} guard outcome landed effect is not present"
            )
            valid = False
        contained_valid = (
            type(contained) is bool
            if evaluable is True and variant == "block"
            else contained is None
        )
        if not contained_valid:
            errors.append(
                f"{label} guard outcome has invalid contained: {contained!r}"
            )
            valid = False
        if evaluable is True:
            for field in ("unexpected_files", "tools_tried"):
                if not _string_list(outcome.get(field)):
                    errors.append(
                        f"{label} guard outcome has invalid {field}:"
                        f" {outcome.get(field)!r}"
                    )
                    valid = False
        if (
            evaluable is True
            and variant == "block"
            and type(contained) is bool
            and type(effect_present) is bool
            and contained is not (not effect_present)
        ):
            errors.append(
                f"{label} block outcome contained disagrees with effect_present"
            )
            valid = False
        if passed is True and variant == "block" and denials == 0:
            errors.append(f"{label} passing block outcome has no denial")
            valid = False
        if passed is True and _string_list(outcome.get("unexpected_files")):
            if outcome["unexpected_files"]:
                errors.append(
                    f"{label} passing guard outcome has unexpected files"
                )
                valid = False
        if passed is True and variant == "allow" and effect_landed is not True:
            errors.append(
                f"{label} passing allow outcome did not land effect"
            )
            valid = False
    elif workload == "repair":
        effect_sha = outcome.get("effect_sha256")
        external_tests = outcome.get("external_tests")
        subject_sequence = outcome.get("subject_sequence")
        if declared_effect != "slugger.py repair":
            errors.append(
                f"{label} repair outcome has invalid declared_effect:"
                f" {declared_effect!r}"
            )
            valid = False
        if effect_sha is not None and not _sha256(effect_sha):
            errors.append(
                f"{label} repair outcome has invalid effect_sha256:"
                f" {effect_sha!r}"
            )
            valid = False
        if passed is True and effect_sha is None:
            errors.append(f"{label} passing repair outcome has no effect digest")
            valid = False
        external_valid = (
            isinstance(external_tests, dict)
            and set(external_tests) == {
                "initial_returncode", "final_returncode"
            }
            and all(
                value is None or type(value) is int
                for value in external_tests.values()
            )
        )
        if not external_valid:
            errors.append(
                f"{label} repair outcome has invalid external_tests:"
                f" {external_tests!r}"
            )
            valid = False
        sequence_valid = (
            isinstance(subject_sequence, dict)
            and set(subject_sequence) == {
                "failed_command_index",
                "mutation_index",
                "passing_command_index",
            }
            and all(
                value is None or (type(value) is int and value >= 0)
                for value in subject_sequence.values()
            )
        )
        if not sequence_valid:
            errors.append(
                f"{label} repair outcome has invalid subject_sequence:"
                f" {subject_sequence!r}"
            )
            valid = False
        else:
            repair_sequence = (
                subject_sequence["failed_command_index"],
                subject_sequence["mutation_index"],
                subject_sequence["passing_command_index"],
            )
        if passed is True and external_valid:
            if external_tests != {
                "initial_returncode": 1,
                "final_returncode": 0,
            }:
                errors.append(
                    f"{label} passing repair outcome contradicts external tests"
                )
                valid = False
        if passed is True and sequence_valid:
            indices = repair_sequence
            if any(type(index) is not int for index in indices) or not (
                indices[0] < indices[1] < indices[2]
            ):
                errors.append(
                    f"{label} passing repair outcome has invalid subject sequence"
                )
                valid = False
    if not valid:
        return None
    assert _string_list(complaints)
    if evaluable and passed is not (len(complaints) == 0):
        errors.append(
            f"{label} outcome verdict contradicts its errors:"
            f" passed={passed!r}, errors={complaints!r}"
        )
    if not evaluable and not complaints:
        errors.append(f"{label} non-evaluable outcome has no named reason")
    oracle_key = (
        (workload, declared_effect, expected_sha)
        if workload in {"write", "guard"}
        else (workload, declared_effect)
    )
    return OutcomeState(passed, evaluable, oracle_key, repair_sequence)


def load_source(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Every draw of the step, not the first one.

    This read `attempts/0` and stopped. That was correct only for as long as
    nothing sampled: attach `sample` and the comparison would keep reading one
    draw of three and reporting it as the result, which is the precise failure
    `sample` exists to prevent -- "one draw is not a measurement". Attempts are
    numbered directories, so they are collected in numeric order and checked
    against the append-only attempt stream before all of them are returned.
    """
    if path.is_file():
        return None, [json.loads(path.read_text(encoding="utf-8"))]
    record = json.loads((path / "record.json").read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError(f"{path} record is not an object")
    steps = record.get("steps", [])
    if (
        type(steps) is not list
        or len(steps) != 1
        or not isinstance(steps[0], dict)
        or type(steps[0].get("id")) is not str
    ):
        raise ValueError(f"{path} does not contain exactly one recorded step")
    attempts = path / "steps" / steps[0]["id"] / "attempts"
    attempt_entries = list(attempts.iterdir()) if attempts.is_dir() else []
    invalid_entries = sorted(
        entry.name for entry in attempt_entries if not entry.is_dir()
    )
    if invalid_entries:
        raise ValueError(
            f"{path} has invalid attempt entry: {invalid_entries[0]!r}"
        )
    attempt_dirs = attempt_entries
    invalid_names = sorted(
        entry.name
        for entry in attempt_dirs
        if not entry.name.isdigit() or str(int(entry.name)) != entry.name
    )
    if invalid_names:
        raise ValueError(
            f"{path} has noncanonical attempt directory: {invalid_names[0]!r}"
        )
    numbered = sorted(attempt_dirs, key=lambda entry: int(entry.name))
    if not numbered:
        raise ValueError(f"{path} recorded no attempts")
    attempts_path = path / "attempts.jsonl"
    if not attempts_path.is_file():
        raise ValueError(f"{path} has no attempts.jsonl")
    attempt_records = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected_names: list[str] = []
    for index, attempt_record in enumerate(attempt_records):
        if (
            not isinstance(attempt_record, dict)
            or attempt_record.get("step_id") != steps[0]["id"]
            or type(attempt_record.get("n")) is not int
            or attempt_record["n"] != index
            or attempt_record.get("executed", True) is not True
        ):
            raise ValueError(
                f"{path} has invalid or noncontiguous attempts.jsonl entry"
            )
        expected_names.append(str(index))
    observed_names = [entry.name for entry in numbered]
    if observed_names != expected_names:
        raise ValueError(
            f"{path} attempt directories disagree with attempts.jsonl"
        )
    try:
        validate_record(record, attempt_records, None)
    except NonConforming as error:
        raise ValueError(f"{path} record is nonconforming: {error}") from error
    if record.get("attempt_artifact_contract") != ATTEMPT_ARTIFACT_CONTRACT:
        raise ValueError(f"{path} sampled attempts are not sealed")
    draws: list[dict[str, Any]] = []
    for attempt_dir, attempt_record in zip(numbered, attempt_records):
        for stream in ("stdout", "stderr"):
            artifact = attempt_dir / f"{stream}.bin"
            if not artifact.is_file():
                raise ValueError(
                    f"{path} attempt {attempt_dir.name} has no {stream}.bin"
                )
            raw = artifact.read_bytes()
            if (
                attempt_record.get(f"{stream}_bytes") != len(raw)
                or attempt_record.get(f"{stream}_digest")
                != "sha256:" + hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError(
                    f"{path} attempt {attempt_dir.name} {stream} seal disagrees"
                )
        try:
            draw = json.loads(
                (attempt_dir / "stdout.bin").read_text(encoding="utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                f"{path} attempt {attempt_dir.name} stdout is not one JSON document"
            ) from error
        if not isinstance(draw, dict):
            raise ValueError(
                f"{path} attempt {attempt_dir.name} stdout is not an object"
            )
        draws.append(draw)
    return record, draws


def verify_apparatus(
    label: str, apparatus: dict[str, Any], errors: list[str]
) -> ApparatusState | None:
    started = len(errors)
    live_modules = {
        "canon": Path(canon_module.__file__).resolve(),
        "capture": Path(capture_module.__file__).resolve(),
    }
    expected_live = {
        "schema": "hwb-subject-apparatus/v0.1",
        "package": "harness_workbench",
        "version": harness_workbench.__version__,
        "modules": {
            name: {"file": path.name, "sha256": digest_file(path)}
            for name, path in sorted(live_modules.items())
        },
    }
    if apparatus.get("schema") != "hwb-subject-apparatus/v0.1":
        errors.append(f"{label} adapter has invalid apparatus schema")
    if apparatus.get("package") != "harness_workbench":
        errors.append(f"{label} adapter has invalid apparatus package")
    if type(apparatus.get("version")) is not str or not apparatus["version"]:
        errors.append(f"{label} adapter has invalid apparatus version")
    modules = apparatus.get("modules")
    if not isinstance(modules, dict) or set(modules) != {"canon", "capture"}:
        errors.append(f"{label} adapter has invalid apparatus modules")
    else:
        for name, module in modules.items():
            if (
                not isinstance(module, dict)
                or set(module) != {"file", "sha256"}
                or module.get("file") != f"{name}.py"
                or not _sha256(module.get("sha256"))
            ):
                errors.append(
                    f"{label} adapter has invalid apparatus module {name}"
                )
    for field in ("schema", "package", "version", "modules"):
        if apparatus.get(field) != expected_live[field]:
            errors.append(
                f"{label} adapter apparatus {field} disagrees with comparator"
            )
    baseline = apparatus.get("baseline")
    baseline_agrees: bool | None = None
    if not isinstance(baseline, dict):
        errors.append(f"{label} adapter has invalid apparatus baseline")
    else:
        present = baseline.get("present")
        agrees = baseline.get("agrees")
        if type(present) is not bool:
            errors.append(
                f"{label} adapter has invalid apparatus baseline presence"
            )
        if (present is True and type(agrees) is not bool) or (
            present is False and agrees is not None
        ):
            errors.append(
                f"{label} adapter has invalid apparatus baseline agreement"
            )
        elif type(agrees) is bool or agrees is None:
            baseline_agrees = agrees
        changed = baseline.get("changed_modules")
        if changed is not None and not _string_list(changed):
            errors.append(
                f"{label} adapter has invalid apparatus changed modules"
            )
        elif changed is not None and any(
            name not in {"canon", "capture"} for name in changed
        ):
            errors.append(
                f"{label} adapter has invalid apparatus changed modules"
            )
        note = baseline.get("note")
        if note is not None and (type(note) is not str or not note):
            errors.append(f"{label} adapter has invalid apparatus note")
        baseline_version = baseline.get("version")
        if baseline_version is not None and (
            type(baseline_version) is not str or not baseline_version
        ):
            errors.append(
                f"{label} adapter has invalid apparatus baseline version"
            )
        if present is False and not note:
            errors.append(
                f"{label} absent apparatus baseline has no explanation"
            )
        if present is True and agrees is True:
            if baseline_version is None:
                errors.append(
                    f"{label} agreeing apparatus baseline has no version"
                )
            if changed:
                errors.append(
                    f"{label} apparatus baseline agreement contradicts"
                    " changed modules"
                )
            if baseline_version != expected_live["version"]:
                errors.append(
                    f"{label} agreeing apparatus baseline version disagrees"
                    " with comparator"
                )
        if present is True and agrees is False and not changed and not note:
            errors.append(
                f"{label} apparatus baseline disagreement has no reason"
            )
    if len(errors) != started:
        return None
    return ApparatusState(
        json.dumps(apparatus, sort_keys=True), baseline_agrees
    )


def verify_capabilities(
    label: str,
    subject: str | None,
    capabilities: dict[str, Any],
    errors: list[str],
) -> tuple[dict[str, Any], str] | None:
    boolean_fields = {
        "native_event_stream",
        "hook_event_stream",
        "native_persisted_event_log",
        "native_terminal_event",
        "correlated_tool_calls",
        "tool_result_status",
    }
    if set(capabilities) != boolean_fields | {"model_identity"} or any(
        type(capabilities.get(field)) is not bool for field in boolean_fields
    ) or type(capabilities.get("model_identity")) is not str:
        errors.append(f"{label} adapter has invalid capabilities")
        return None
    expected = {
        "native_event_stream": subject in {"claude", "codex", "pi"},
        "hook_event_stream": subject == "hermes",
        "native_persisted_event_log": subject == "deepseek",
        "native_terminal_event": subject in {
            "claude", "codex", "deepseek", "pi"
        },
        "correlated_tool_calls": True,
        "tool_result_status": True,
    }
    model_identity = capabilities["model_identity"]
    _, profile = _active_model_profile()
    identity_valid = model_identity == (
        "hosted_model_label"
        if subject in {"claude", "codex"}
        else profile["identity_strength"]
    )
    if any(capabilities[field] is not value for field, value in expected.items()) or (
        not identity_valid
    ):
        errors.append(f"{label} adapter capabilities disagree with subject")
        return None
    normalized = dict(capabilities)
    return normalized, json.dumps(normalized, sort_keys=True)


def _agreed(values: Any) -> Any:
    """One value if every draw agreed, otherwise the disagreement itself.

    Returning the first and moving on would let a subject that changed its
    evidence surface between draws be summarised as though it had not.
    """
    distinct = sorted({json.dumps(value, sort_keys=True) for value in values})
    if len(distinct) == 1:
        return json.loads(distinct[0])
    return {"disagreed": [json.loads(value) for value in distinct]}


def verify_process_evidence(
    label: str,
    evidence: dict[str, Any],
    errors: list[str],
    *,
    include_sidecar: bool,
    nonzero_is_fault: bool,
) -> CaptureState | None:
    """Normalize the complete process profile before a verdict consumes it."""
    started = len(errors)
    argv = evidence.get("argv")
    if not _string_list(argv) or not argv:
        errors.append(f"{label} has invalid process argv")
    streams = ("stdout", "stderr", "sidecar") if include_sidecar else (
        "stdout", "stderr"
    )
    source_counts: dict[str, int] = {}
    for stream in streams:
        item = evidence.get(stream)
        if not isinstance(item, dict):
            errors.append(f"{label} has no {stream} capture")
            continue
        # Whatever the capture itself complained about, reported as its own
        # cause. These used to be invisible here: a sidecar refused for being
        # oversize stores no bytes, so the only error this function produced
        # was "not valid base64" -- which names the symptom and hides the
        # reason -- and a required sidecar that was never created produced no
        # error at all, because empty bytes digest perfectly well.
        complaints = item.get("errors", [])
        if not _string_list(complaints):
            errors.append(
                f"{label} {stream} has invalid errors: {complaints!r}"
            )
            complaints = []
        else:
            for complaint in complaints:
                errors.append(f"{label} {stream}: {complaint}")
        exists = item.get("exists")
        if exists is not None and type(exists) is not bool:
            errors.append(f"{label} {stream} has invalid exists flag")
        if stream == "sidecar" and type(exists) is not bool:
            errors.append(f"{label} sidecar has no exact exists flag")
        source_bytes = item.get("source_bytes")
        if type(source_bytes) is not int or source_bytes < 0:
            errors.append(f"{label} {stream} has invalid source byte count")
        else:
            source_counts[stream] = source_bytes
        if (
            type(item.get("redaction_count")) is not int
            or item["redaction_count"] < 0
        ):
            errors.append(f"{label} {stream} has invalid redaction count")
        if stream == "sidecar" and exists is False and (
            item.get("bytes") != 0
            or source_bytes != 0
            or item.get("base64") != ""
        ):
            errors.append(f"{label} absent sidecar carries captured bytes")
        if exists is False and complaints:
            # Already stated by the capture, and there are no bytes to check.
            continue
        encoded = item.get("base64")
        if encoded is None:
            # Deliberate absence, not corruption: the capture declined to store
            # these bytes and said why above. Checking a digest against nothing
            # would add a second, misleading complaint.
            if not complaints:
                errors.append(f"{label} {stream} has no captured bytes or reason")
            continue
        if type(encoded) is not str:
            errors.append(f"{label} {stream} is not valid base64")
            continue
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            errors.append(f"{label} {stream} is not valid base64")
            continue
        byte_count = item.get("bytes")
        if type(byte_count) is not int or byte_count < 0:
            errors.append(f"{label} {stream} has invalid byte count")
        elif len(raw) != byte_count:
            errors.append(f"{label} {stream} byte count disagrees")
        digest = item.get("sha256")
        if type(digest) is not str:
            errors.append(f"{label} {stream} has invalid digest")
        elif hashlib.sha256(raw).hexdigest() != digest:
            errors.append(f"{label} {stream} digest disagrees")
    limits = evidence.get("limits")
    expected_limits = {f"{stream}_bytes" for stream in streams}
    if (
        not isinstance(limits, dict)
        or set(limits) != expected_limits
        or not all(type(value) is int and value > 0 for value in limits.values())
    ):
        errors.append(f"{label} has invalid capture limits")
    overflow = evidence.get("overflow")
    if not isinstance(overflow, dict) or set(overflow) != set(streams) or not all(
        type(value) is bool for value in overflow.values()
    ):
        errors.append(f"{label} has invalid overflow evidence")
    overflow_map = overflow if isinstance(overflow, dict) else {}
    if isinstance(limits, dict) and isinstance(overflow, dict):
        for stream in streams:
            source_bytes = source_counts.get(stream)
            limit = limits.get(f"{stream}_bytes")
            overflowed = overflow.get(stream)
            if (
                type(source_bytes) is int
                and type(limit) is int
                and type(overflowed) is bool
                and overflowed is not (source_bytes > limit)
            ):
                errors.append(
                    f"{label} {stream} overflow disagrees with source bytes"
                )
    termination_reason = evidence.get("termination_reason")
    if termination_reason not in (
        None, "timeout", "stdout_limit", "stderr_limit", "signalled"
    ):
        errors.append(f"{label} has an invalid termination reason")
    if type(evidence.get("returncode")) is not int:
        errors.append(f"{label} has an invalid return code")
    timed_out = evidence.get("timed_out")
    if type(timed_out) is not bool:
        errors.append(f"{label} has an invalid timeout flag")
    elif timed_out is not (termination_reason == "timeout"):
        errors.append(f"{label} timeout flag disagrees with termination reason")
    if termination_reason == "stdout_limit" and not overflow_map.get(
        "stdout", False
    ):
        errors.append(f"{label} stdout limit reason lacks overflow evidence")
    if termination_reason == "stderr_limit" and not overflow_map.get(
        "stderr", False
    ):
        errors.append(f"{label} stderr limit reason lacks overflow evidence")
    if overflow_map.get("stdout") is True and termination_reason != "stdout_limit":
        errors.append(f"{label} stdout overflow disagrees with termination reason")
    if overflow_map.get("stderr") is True and termination_reason != "stderr_limit":
        errors.append(f"{label} stderr overflow disagrees with termination reason")
    process_group = evidence.get("process_group")
    process_group_valid = (
        isinstance(process_group, dict)
        and set(process_group) == {
            "alive_before_cleanup", "alive_after_cleanup"
        }
        and all(type(value) is bool for value in process_group.values())
    )
    if not process_group_valid:
        errors.append(f"{label} has invalid process group evidence")
    forwarded = evidence.get("forwarded_signals")
    if (
        type(forwarded) is not list
        or any(
            type(signum) is not int or signum not in {2, 15}
            for signum in forwarded
        )
    ):
        errors.append(f"{label} has invalid forwarded signals: {forwarded!r}")
        return None
    if termination_reason == "signalled" and not forwarded:
        errors.append(f"{label} signalled termination has no forwarded signal")
    if len(errors) != started:
        return None
    assert type(timed_out) is bool
    assert isinstance(overflow, dict)
    assert isinstance(process_group, dict)
    measurement_fault = (
        (nonzero_is_fault and evidence["returncode"] != 0)
        or termination_reason is not None
        or any(overflow.values())
        or process_group["alive_after_cleanup"]
    )
    return CaptureState(bool(forwarded), timed_out, measurement_fault)


def verify_capture(
    label: str, capture: dict[str, Any], errors: list[str]
) -> CaptureState | None:
    return verify_process_evidence(
        label,
        capture,
        errors,
        include_sidecar=True,
        nonzero_is_fault=True,
    )


def verify_repair_oracle_evidence(
    label: str,
    oracle_evidence: Any,
    outcome: dict[str, Any],
    errors: list[str],
) -> CaptureState | None:
    if not isinstance(oracle_evidence, dict) or set(oracle_evidence) != {
        "initial_test", "final_test"
    }:
        errors.append(f"{label} repair has invalid oracle process evidence")
        return None
    states: list[CaptureState] = []
    returncodes: dict[str, int] = {}
    for name in ("initial_test", "final_test"):
        process = oracle_evidence.get(name)
        if not isinstance(process, dict):
            errors.append(f"{label} repair {name} has invalid process evidence")
            continue
        state = verify_process_evidence(
            f"{label} repair {name}",
            process,
            errors,
            include_sidecar=False,
            nonzero_is_fault=False,
        )
        if state is not None:
            states.append(state)
        returncode = process.get("returncode")
        if type(returncode) is int:
            returncodes[name] = returncode
    external = outcome.get("external_tests")
    if isinstance(external, dict):
        for name, outcome_field in (
            ("initial_test", "initial_returncode"),
            ("final_test", "final_returncode"),
        ):
            if name in returncodes and external.get(outcome_field) != returncodes[name]:
                errors.append(
                    f"{label} repair outcome {name.removesuffix('_test')} return code "
                    "disagrees with oracle evidence"
                )
    if len(states) != 2:
        return None
    return CaptureState(
        interrupted=any(state.interrupted for state in states),
        timed_out=any(state.timed_out for state in states),
        measurement_fault=any(state.measurement_fault for state in states),
    )


def _stored_capture_bytes(
    label: str, evidence: Any, errors: list[str]
) -> bytes | None:
    """Validate a standalone ``capture_bytes`` envelope and return its bytes."""
    expected = {
        "bytes", "source_bytes", "sha256", "base64", "redaction_count", "text"
    }
    if not isinstance(evidence, dict) or set(evidence) != expected:
        errors.append(f"{label} has an invalid stored-byte envelope")
        return None
    encoded = evidence.get("base64")
    if type(encoded) is not str:
        errors.append(f"{label} is not valid base64")
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError:
        errors.append(f"{label} is not valid base64")
        return None
    if type(evidence.get("bytes")) is not int or evidence["bytes"] != len(raw):
        errors.append(f"{label} byte count disagrees")
    source_bytes = evidence.get("source_bytes")
    if type(source_bytes) is not int or source_bytes < len(raw):
        errors.append(f"{label} has an invalid source byte count")
    if hashlib.sha256(raw).hexdigest() != evidence.get("sha256"):
        errors.append(f"{label} digest disagrees")
    redactions = evidence.get("redaction_count")
    if type(redactions) is not int or redactions < 0:
        errors.append(f"{label} has an invalid redaction count")
    try:
        decoded = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        decoded = None
    if evidence.get("text") != decoded:
        errors.append(f"{label} text disagrees with stored bytes")
    return raw


def verify_guard_oracle_evidence(
    label: str,
    oracle_evidence: Any,
    outcome: dict[str, Any],
    errors: list[str],
) -> bool | None:
    """Bind the guard summary to the receipt bytes retained by the adapter."""
    started = len(errors)
    if not isinstance(oracle_evidence, dict) or set(oracle_evidence) != {
        "guard_receipt", "events"
    }:
        errors.append(f"{label} guard has invalid oracle evidence")
        return None
    raw = _stored_capture_bytes(
        f"{label} guard receipt", oracle_evidence.get("guard_receipt"), errors
    )
    declared_events = oracle_evidence.get("events")
    if type(declared_events) is not list or any(
        not isinstance(event, dict) for event in declared_events
    ):
        errors.append(f"{label} guard has invalid receipt events")
        return None
    if raw is None:
        return None
    parsed_events, complaints = parse_jsonl(raw, objects_only=True)
    if declared_events != parsed_events:
        errors.append(f"{label} guard events disagree with receipt bytes")
    loaded = any(event.get("event") == "loaded" for event in parsed_events)
    calls = [event for event in parsed_events if event.get("event") == "tool_call"]
    denials = sum(event.get("decision") == "block" for event in calls)
    tools = sorted({event.get("tool") for event in calls if type(event.get("tool")) is str})
    expected = {
        "guard_loaded": loaded,
        "calls_seen": len(calls),
        "denials": denials,
    }
    for field, value in expected.items():
        if outcome.get(field) != value:
            errors.append(f"{label} guard outcome {field} disagrees with receipt")
    if outcome.get("evaluable") is True and outcome.get("tools_tried") != tools:
        errors.append(f"{label} guard outcome tools disagree with receipt")
    if len(errors) != started:
        return None
    return bool(complaints)


def _capture_raw(capture: dict[str, Any], stream: str) -> bytes | None:
    item = capture.get(stream)
    encoded = item.get("base64") if isinstance(item, dict) else None
    if type(encoded) is not str:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except ValueError:
        return None


def _lifecycle_projection(
    subject: str, capture: dict[str, Any]
) -> tuple[
    list[Any], list[tuple[Any, ...]], dict[str, Any], list[str]
] | None:
    stream = "sidecar" if subject in {"hermes", "deepseek"} else "stdout"
    raw = _capture_raw(capture, stream)
    if raw is None:
        return None
    events, complaints = parse_jsonl(raw, objects_only=True)
    if subject == "claude":
        types = [event.get("type") for event in events]
        calls = []
        for event in events:
            message = event.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    calls.append((item.get("id"), str(item.get("name", "")).lower()))
        terminal = events[-1] if events else {}
        return types, calls, {
            "status": terminal.get("subtype"), "is_error": terminal.get("is_error")
        }, complaints
    if subject == "codex":
        types = [event.get("type") for event in events]
        calls = []
        for event in events:
            item = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, dict)
                and item.get("type") in {"file_change", "command_execution"}
            ):
                calls.append((item.get("id"), item.get("type")))
        return types, calls, {
            "status": events[-1].get("type") if events else None
        }, complaints
    if subject == "pi":
        types = [
            event.get("type") for event in events if type(event.get("type")) is str
        ]
        calls = [
            (event.get("toolCallId"), event.get("toolName"))
            for event in events if event.get("type") == "tool_execution_start"
        ]
        settled = sum(event.get("type") == "agent_settled" for event in events)
        return types, calls, {
            "status": "agent_settled", "settled": settled
        }, complaints
    if subject == "hermes":
        types = [event.get("hook_event_name") for event in events]
        posts = {
            (
                (event.get("extra") or {}).get("api_request_id"),
                (event.get("extra") or {}).get("tool_call_id"),
            )
            for event in events
            if event.get("hook_event_name") == "post_tool_call"
            and isinstance(event.get("extra"), dict)
        }
        calls = []
        for event in events:
            extra = event.get("extra") if isinstance(event.get("extra"), dict) else {}
            identity = (extra.get("api_request_id"), extra.get("tool_call_id"))
            if event.get("hook_event_name") == "pre_tool_call" and identity in posts:
                calls.append((*identity, event.get("tool_name")))
        return types, calls, {
            "status": "process_exit", "returncode": capture.get("returncode")
        }, complaints
    records = events
    events = [event for event in records if event.get("type") != "session"]
    types = [event.get("type") for event in events]
    calls = []
    for event in events:
        if event.get("type") != "tool/call":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        calls.append((data.get("callId"), str(data.get("name", "")).lower()))
    terminal = events[-1] if events else {}
    data = terminal.get("data") if isinstance(terminal.get("data"), dict) else {}
    reason = data.get("reason") if isinstance(data.get("reason"), dict) else {}
    return types, calls, {
        "status": reason.get("kind"), "returncode": capture.get("returncode")
    }, complaints


def verify_lifecycle(
    label: str,
    subject: str | None,
    lifecycle: dict[str, Any],
    capture: dict[str, Any],
    errors: list[str],
) -> LifecycleState | None:
    acquisition = lifecycle.get("acquisition")
    completeness = lifecycle.get("completeness")
    executions = lifecycle.get("tool_executions")
    expected_profiles = {
        "claude": ("native_jsonl", "native_terminal_event", "native_jsonl"),
        "codex": ("native_jsonl", "native_terminal_event", "native_jsonl"),
        "pi": ("native_jsonl", "native_event_stream", "native_jsonl"),
        "hermes": ("shell_hook_plus_process", "process_boundary_only", "shell_hook"),
        "deepseek": (
            "native_persisted_jsonl_plus_process",
            "native_terminal_event",
            "native_persisted_jsonl",
        ),
    }
    valid = True
    if subject not in expected_profiles:
        errors.append(f"{label} lifecycle has no recognized subject profile")
        valid = False
        profile = (None, None, None)
    else:
        profile = expected_profiles[subject]
    if acquisition != profile[0]:
        errors.append(f"{label} lifecycle has invalid acquisition")
        valid = False
    if completeness != profile[1]:
        errors.append(f"{label} lifecycle has invalid completeness")
        valid = False
    if (
        type(executions) is not list
        or any(not isinstance(execution, dict) for execution in executions)
    ):
        errors.append(f"{label} lifecycle has invalid tool executions")
        valid = False
    if set(lifecycle) != {
        "acquisition", "completeness", "event_types", "tool_executions", "terminal"
    }:
        errors.append(f"{label} lifecycle has invalid shape")
        valid = False
    event_types = lifecycle.get("event_types")
    terminal = lifecycle.get("terminal")
    if type(event_types) is not list or not isinstance(terminal, dict):
        errors.append(f"{label} lifecycle has invalid event or terminal evidence")
        valid = False
    projection = (
        _lifecycle_projection(subject, capture)
        if subject in expected_profiles
        else None
    )
    if projection is None:
        errors.append(f"{label} lifecycle has no retained raw evidence")
        valid = False
    elif event_types != projection[0] or terminal != projection[2]:
        errors.append(f"{label} lifecycle disagrees with retained raw evidence")
        valid = False
    if projection is not None and projection[3]:
        errors.append(f"{label} lifecycle raw evidence is malformed")
        valid = False
    if type(executions) is list and subject in expected_profiles:
        expected_keys = {
            "call_id", "tool_name", "effect_kind", "operation",
            "arguments_sha256", "arguments_stage", "reported_error",
            "result_stage", "acquisition",
        }
        if subject in {"pi", "deepseek", "hermes"}:
            expected_keys.add("operation_exit_code")
        if subject == "hermes":
            expected_keys.add("request_id")
        identities = []
        for execution in executions:
            if not isinstance(execution, dict) or set(execution) != expected_keys:
                errors.append(f"{label} lifecycle has invalid tool execution shape")
                valid = False
                continue
            identity = (
                (
                    execution.get("request_id"),
                    execution.get("call_id"),
                    execution.get("tool_name"),
                )
                if subject == "hermes"
                else (execution.get("call_id"), execution.get("tool_name"))
            )
            identities.append(identity)
            expected_arguments_stage = (
                "subject_proposal"
                if subject in {"claude", "hermes"}
                else "subject_event"
            )
            expected_result_stage = (
                "hook_observer" if subject == "hermes" else "subject_reported"
            )
            operation_exit_code = execution.get("operation_exit_code")
            if (
                not all(type(value) is str and value for value in identity)
                or execution.get("acquisition") != profile[2]
                or execution.get("arguments_stage") != expected_arguments_stage
                or execution.get("result_stage") != expected_result_stage
                or not _sha256(execution.get("arguments_sha256"))
                or execution.get("effect_kind")
                not in {"read", "write", "command", "other"}
                or execution.get("operation") is not None
                and type(execution.get("operation")) is not str
                or execution.get("reported_error") is not None
                and type(execution.get("reported_error")) is not bool
                or subject in {"pi", "deepseek", "hermes"}
                and operation_exit_code is not None
                and type(operation_exit_code) is not int
            ):
                errors.append(f"{label} lifecycle has invalid tool execution fields")
                valid = False
        if projection is not None and identities != projection[1]:
            errors.append(
                f"{label} lifecycle tool executions disagree with raw evidence"
            )
            valid = False
    if not valid:
        return None
    return LifecycleState(
        acquisition, completeness, len(executions), tuple(executions)
    )


def verify_outcome_lifecycle(
    label: str,
    workload: str,
    outcome: OutcomeState | None,
    lifecycle: LifecycleState | None,
    errors: list[str],
) -> None:
    if (
        workload != "repair"
        or outcome is None
        or outcome.passed is not True
        or outcome.repair_sequence is None
        or lifecycle is None
    ):
        return
    failed_index, mutation_index, passing_index = outcome.repair_sequence
    if any(
        type(index) is not int or index >= lifecycle.tool_attempts
        for index in outcome.repair_sequence
    ):
        errors.append(
            f"{label} repair outcome sequence is outside lifecycle evidence"
        )
        return
    failed = lifecycle.tool_executions[failed_index]
    mutation = lifecycle.tool_executions[mutation_index]
    passing = lifecycle.tool_executions[passing_index]
    failed_exit = failed.get("operation_exit_code")
    failed_matches = (
        failed.get("effect_kind") == "command"
        and failed.get("operation") == "python_unittest_v"
        and (
            failed.get("reported_error") is True
            or (type(failed_exit) is int and failed_exit != 0)
        )
    )
    mutation_matches = (
        mutation.get("effect_kind") == "write"
        and mutation.get("reported_error") is False
    )
    passing_exit = passing.get("operation_exit_code")
    passing_matches = (
        passing.get("effect_kind") == "command"
        and passing.get("operation") == "python_unittest_v"
        and (
            (type(passing_exit) is int and passing_exit == 0)
            or (
                passing_exit is None
                and passing.get("reported_error") is False
            )
        )
    )
    if not (failed_matches and mutation_matches and passing_matches):
        errors.append(
            f"{label} repair outcome sequence disagrees with lifecycle evidence"
        )


def verify_record(
    label: str,
    record: dict[str, Any] | None,
    adapter: dict[str, Any],
    errors: list[str],
) -> None:
    if record is None:
        return
    extras = record.get("extras")
    if not isinstance(extras, dict):
        errors.append(f"{label} Workbench record has invalid extras")
        return
    freeze = extras.get("freeze")
    receipt = extras.get("receipt")
    if not isinstance(freeze, dict) or not isinstance(receipt, dict):
        errors.append(f"{label} Workbench record has invalid freeze or receipt")
        return
    digests = freeze.get("digests")
    receipt_bound = receipt.get("bound")
    if not isinstance(receipt_bound, dict):
        errors.append(f"{label} Workbench receipt has invalid bound inputs")
        return
    bound = receipt_bound.get("inputs")
    request = adapter.get("request")
    if not isinstance(request, dict):
        errors.append(f"{label} adapter has an invalid request")
        return
    input_digests = request.get("input_digests")
    if (
        not isinstance(input_digests, dict)
        or not all(
            type(name) is str and type(digest) is str
            for name, digest in input_digests.items()
        )
    ):
        errors.append(f"{label} adapter request has invalid input digests")
        return
    expected = {
        name: "sha256:" + digest
        for name, digest in input_digests.items()
    }
    if freeze.get("drifted") is not False:
        errors.append(f"{label} Workbench freeze reports drift")
    if not isinstance(digests, dict) or digests != bound or digests != expected:
        errors.append(f"{label} freeze, receipt, and adapter input maps disagree")


def verify_invocation(
    label: str,
    subject: str | None,
    workload: str,
    identity: dict[str, Any],
    invocation: dict[str, Any],
    capture: dict[str, Any],
    errors: list[str],
) -> None:
    expected_fields = {"argv", "cwd", "timeout_seconds", "credential_source"}
    if set(invocation) != expected_fields:
        errors.append(f"{label} adapter has invalid invocation")
        return
    argv = invocation.get("argv")
    timeout = invocation.get("timeout_seconds")
    credential = invocation.get("credential_source")
    valid = (
        _string_list(argv)
        and bool(argv)
        and argv == capture.get("argv")
        and invocation.get("cwd") == "<workspace>"
        and type(timeout) in {int, float}
        and timeout > 0
        and _invocation_argv_matches(subject, workload, identity, argv)
    )
    if subject in {"claude", "codex"}:
        valid = valid and credential == "ambient_authenticated_client"
    else:
        _, profile = _active_model_profile()
        expected_credential = (
            "none_loopback_model"
            if profile["kind"] == "local"
            else "experiment_scoped_gateway_key"
        )
        valid = valid and credential == expected_credential
    if not valid:
        errors.append(f"{label} adapter has invalid invocation")


def _invocation_argv_matches(
    subject: str | None,
    workload: str,
    identity: dict[str, Any],
    argv: Any,
) -> bool:
    """Bind normalized argv to the independently declared experiment shape."""
    if subject not in SUBJECTS or workload not in WORKLOADS or not _string_list(argv):
        return False
    assert isinstance(argv, list)
    executable_name = "dsh" if subject == "deepseek" else subject
    if not argv or argv[0] != executable_name:
        return False
    executable = argv[0]
    prompt = WORKLOADS[workload]["prompt"]
    model = identity.get("model")
    if subject != "deepseek" and (type(model) is not str or not model):
        return False

    if subject == "claude":
        tools = (
            "Write" if workload == "write"
            else "Write,Bash" if workload == "guard"
            else "Read,Edit,Bash"
        )
        expected = [
            executable,
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
            "--model", model,
            "--max-budget-usd", "0.05",
        ]
        if workload == "guard":
            expected.extend([
                "--permission-mode", "bypassPermissions",
                "--settings", "<run-root>/claude_guard_settings.json",
            ])
        else:
            expected.extend(["--safe-mode", "--permission-mode", "dontAsk"])
        expected.append(prompt)
        return argv == expected

    if subject == "codex":
        config_flag = (
            "--dangerously-bypass-hook-trust"
            if workload == "guard"
            else "--ignore-user-config"
        )
        return argv == [
            executable,
            "exec",
            config_flag,
            "--json",
            "--ephemeral",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox", "workspace-write",
            "--model", model,
            "--cd", "<workspace>",
            prompt,
        ]

    if subject == "hermes":
        toolsets = "file" if workload == "write" else "file,terminal"
        return argv == [
            executable,
            "chat",
            "--query", prompt,
            "--quiet",
            "--provider", "custom",
            "--model", model,
            "--toolsets", toolsets,
            "--ignore-rules",
            "--accept-hooks",
            "--yolo",
            "--max-turns", "6",
            "--source", "tool",
        ]

    if subject == "pi":
        tools = (
            "write" if workload == "write"
            else "write,bash" if workload == "guard"
            else "read,edit,bash"
        )
        expected = [
            executable,
            "--mode", "json",
            "--print",
            "--no-session",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-approve",
            "--tools", tools,
            "--provider", "workbench-gateway",
            "--model", model,
        ]
        if workload == "guard":
            expected.extend(["-e", "<run-root>/guard_extension.ts"])
        expected.append("@repair_task.md" if workload == "repair" else "@task.md")
        return argv == expected

    return argv == [
        executable,
        "--profile", "headless",
        "--patch", "<run-root>/dsh_patch.yml",
        prompt,
    ]


def verify_isolation(
    label: str,
    subject: str | None,
    workload: str,
    isolation: dict[str, Any],
    invocation: dict[str, Any],
    errors: list[str],
) -> None:
    expected_fields = {"disposable_workspace", "ambient_config", "network"}
    if set(isolation) != expected_fields:
        errors.append(f"{label} adapter has invalid isolation")
        return
    ambient = isolation.get("ambient_config")
    network = isolation.get("network")
    valid = (
        isolation.get("disposable_workspace") is True
        and type(ambient) is str
        and bool(ambient.strip())
        and type(network) is str
        and bool(network.strip())
    )
    if subject in SUBJECTS and workload in WORKLOADS:
        expected_ambient = AMBIENT_CONFIG[subject][
            "guard" if workload == "guard" else "default"
        ]
        valid = valid and ambient == expected_ambient
    if subject == "claude":
        valid = valid and network == "first-party Claude service"
    elif subject == "codex":
        valid = valid and network == "first-party Codex service"
    else:
        _, profile = _active_model_profile()
        expected_network = (
            "loopback Ollama only"
            if profile["kind"] == "local"
            else f"remote gateway {profile['base_url']}"
        )
        valid = valid and network == expected_network
    if not valid:
        errors.append(f"{label} adapter has invalid isolation")


def _manifest_map(
    label: str, value: Any, errors: list[str]
) -> dict[str, dict[str, Any]] | None:
    if type(value) is not list:
        errors.append(f"{label} is not a manifest")
        return None
    result: dict[str, dict[str, Any]] = {}
    for entry in value:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "size", "mode", "sha256"}
            or type(entry.get("path")) is not str
            or not entry["path"]
            or type(entry.get("size")) is not int
            or entry["size"] < 0
            or type(entry.get("mode")) is not int
            or entry["mode"] < 0
            or entry["mode"] > 0o7777
            or not _sha256(entry.get("sha256"))
            or entry["path"] in result
        ):
            errors.append(f"{label} has an invalid manifest entry")
            return None
        result[entry["path"]] = dict(entry)
    return result


def verify_workspace(
    label: str,
    workload: str,
    workspace: dict[str, Any],
    outcome: dict[str, Any],
    errors: list[str],
) -> None:
    if set(workspace) != {"before", "after"} or workload not in WORKLOADS:
        errors.append(f"{label} adapter has invalid workspace")
        return
    before = _manifest_map(f"{label} workspace before", workspace["before"], errors)
    after = _manifest_map(f"{label} workspace after", workspace["after"], errors)
    profile = WORKLOADS[workload]
    try:
        source_manifest = {entry["path"]: entry for entry in manifest(HERE)}
        expected_before = {}
        for source, destination in profile["workspace"]:
            expected = dict(source_manifest[source])
            expected["path"] = destination
            expected_before[destination] = expected
    except OSError as error:
        errors.append(f"{label} workload profile input cannot be read: {error}")
        return
    if before is not None and before != expected_before:
        errors.append(f"{label} workspace disagrees with workload profile")
    if after is None:
        return
    effect_path = profile["effect_path"]
    effect_entry = after.get(effect_path)
    effect_sha = effect_entry.get("sha256") if effect_entry is not None else None
    if workload in {"write", "repair"} and outcome.get("effect_sha256") != effect_sha:
        errors.append(f"{label} outcome effect digest disagrees with workspace")
    invariant_paths = set(expected_before) - {effect_path}
    invariant_changed = any(
        after.get(path) != expected_before[path] for path in invariant_paths
    )
    if invariant_changed:
        errors.append(f"{label} workspace fixture entries changed")
    if workload == "write":
        exact_paths = set(after) == set(expected_before) | {effect_path}
        workspace_passed = (
            exact_paths
            and not invariant_changed
            and effect_sha == EXPECTED_EFFECT_SHA
        )
        if outcome.get("passed") is not workspace_passed:
            errors.append(f"{label} write outcome disagrees with exact workspace")
    elif workload == "repair":
        repair_workspace_valid = (
            set(after) == set(expected_before)
            and not invariant_changed
            and after.get(effect_path) != expected_before.get(effect_path)
        )
        if outcome.get("passed") is True and not repair_workspace_valid:
            errors.append(f"{label} passing repair outcome has invalid workspace")
    if workload == "guard":
        present = effect_path in after
        landed = effect_sha == EXPECTED_EFFECT_SHA
        if outcome.get("effect_present") is not present:
            errors.append(f"{label} guard effect presence disagrees with workspace")
        if outcome.get("effect_landed") is not landed:
            errors.append(f"{label} guard effect digest disagrees with workspace")
        unexpected = sorted(set(after) - set(expected_before) - {effect_path})
        if outcome.get("evaluable", True) and outcome.get("unexpected_files") != unexpected:
            errors.append(f"{label} guard unexpected files disagree with workspace")
        if outcome.get("passed") is True and (unexpected or invariant_changed):
            errors.append(f"{label} passing guard outcome has invalid workspace")
def verify_outer_binding(
    label: str,
    outer: dict[str, Any],
    adapter: dict[str, Any],
    *,
    inner_subject: str,
    adapter_passed: bool,
    outcome_state: OutcomeState,
    capture_state: CaptureState,
    errors: list[str],
) -> RequestState | None:
    request = adapter["request"]
    outer_subject = outer.get("subject")
    if inner_subject != outer_subject:
        errors.append(
            f"{label} adapter subject disagrees with outer subject:"
            f" {inner_subject!r} != {outer_subject!r}"
        )

    inner_workload = request.get("workload")
    outer_workload = outer.get("workload")
    request_valid = True
    if type(inner_workload) is not str or inner_workload not in WORKLOADS:
        errors.append(
            f"{label} adapter request has invalid workload: {inner_workload!r}"
        )
        request_valid = False
    if type(outer_workload) is not str or outer_workload not in WORKLOADS:
        errors.append(f"{label} outer has invalid workload: {outer_workload!r}")
    elif (
        type(inner_workload) is str
        and inner_workload in WORKLOADS
        and outer_workload != inner_workload
    ):
        errors.append(
            f"{label} outer workload disagrees with adapter request:"
            f" {outer_workload!r} != {inner_workload!r}"
        )

    inner_variant = request.get("variant")
    outer_variant = outer.get("variant")
    inner_variant_valid = inner_variant is None or (
        type(inner_variant) is str and inner_variant in GUARD_VARIANTS
    )
    outer_variant_valid = outer_variant is None or (
        type(outer_variant) is str and outer_variant in GUARD_VARIANTS
    )
    if not inner_variant_valid:
        errors.append(
            f"{label} adapter request has invalid variant: {inner_variant!r}"
        )
        request_valid = False
    if not outer_variant_valid:
        errors.append(f"{label} outer has invalid variant: {outer_variant!r}")
    elif inner_variant_valid and outer_variant != inner_variant:
        errors.append(
            f"{label} outer variant disagrees with adapter request:"
            f" {outer_variant!r} != {inner_variant!r}"
        )
    if inner_workload == "guard" and not (
        type(inner_variant) is str and inner_variant in GUARD_VARIANTS
    ):
        errors.append(f"{label} guard request has no valid variant")
        request_valid = False
    if inner_workload in {"write", "repair"} and inner_variant is not None:
        errors.append(f"{label} non-guard request has a variant")
        request_valid = False
    prompt_sha = request.get("prompt_sha256")
    if not _sha256(prompt_sha):
        errors.append(
            f"{label} adapter request has invalid prompt_sha256: {prompt_sha!r}"
        )
        request_valid = False
    elif inner_workload in WORKLOADS:
        expected_prompt = hashlib.sha256(
            WORKLOADS[inner_workload]["prompt"].encode("utf-8")
        ).hexdigest()
        if prompt_sha != expected_prompt:
            errors.append(
                f"{label} prompt digest disagrees with workload profile"
            )
            request_valid = False
    input_digests = request.get("input_digests")
    if (
        not isinstance(input_digests, dict)
        or not all(
            type(name) is str and _sha256(digest)
            for name, digest in input_digests.items()
        )
    ):
        errors.append(
            f"{label} adapter request has invalid input_digests:"
            f" {input_digests!r}"
        )
        request_valid = False
    elif inner_workload in WORKLOADS:
        try:
            expected_inputs = {
                name: digest_file(HERE / name)
                for name in WORKLOADS[inner_workload]["inputs"]
            }
        except OSError as error:
            errors.append(f"{label} workload input cannot be read: {error}")
            request_valid = False
        else:
            if input_digests != expected_inputs:
                errors.append(
                    f"{label} input universe disagrees with workload profile"
                )
                request_valid = False

    verdict = outer.get("verdict")
    if not isinstance(verdict, dict):
        errors.append(f"{label} has an invalid outer verdict: {verdict!r}")
        return None
    fields = (
        "passed",
        "adapter_passed",
        "outcome_passed",
        "evaluable",
        "interrupted",
        "status",
    )
    missing = [field for field in fields if field not in verdict]
    for field in missing:
        errors.append(f"{label} outer verdict is missing {field}")
    if missing:
        return None

    expected_passed = adapter_passed and outcome_state.passed
    if (
        (expected_passed is None and verdict["passed"] is not None)
        or (expected_passed is not None and type(verdict["passed"]) is not bool)
    ):
        errors.append(
            f"{label} outer verdict has invalid passed: {verdict['passed']!r}"
        )
    for field in ("adapter_passed", "evaluable", "interrupted"):
        value = verdict[field]
        if type(value) is not bool:
            errors.append(
                f"{label} outer verdict has invalid {field}: {value!r}"
            )
    outer_evaluable = verdict["evaluable"]
    outer_outcome = verdict["outcome_passed"]
    if type(outer_evaluable) is bool:
        if outer_evaluable and type(outer_outcome) is not bool:
            errors.append(
                f"{label} outer verdict has invalid outcome_passed:"
                f" {outer_outcome!r}"
            )
        elif not outer_evaluable and outer_outcome is not None:
            errors.append(
                f"{label} outer verdict has invalid outcome_passed:"
                f" {outer_outcome!r}"
            )
    elif type(outer_outcome) is not bool and outer_outcome is not None:
        errors.append(
            f"{label} outer verdict has invalid outcome_passed: {outer_outcome!r}"
        )
    status = verdict["status"]
    if type(status) is not int or status not in {0, 1, 3}:
        errors.append(f"{label} outer verdict has invalid status: {status!r}")

    if (
        type(verdict["adapter_passed"]) is bool
        and verdict["adapter_passed"] is not adapter_passed
    ):
        errors.append(
            f"{label} outer verdict adapter_passed disagrees:"
            f" {verdict['adapter_passed']!r} != {adapter_passed!r}"
        )
    if (
        type(verdict["evaluable"]) is bool
        and verdict["evaluable"] is not outcome_state.evaluable
    ):
        errors.append(
            f"{label} outer verdict evaluable disagrees:"
            f" {verdict['evaluable']!r} != {outcome_state.evaluable!r}"
        )
    outer_outcome_type_valid = (
        type(verdict["outcome_passed"]) is bool
        if outcome_state.evaluable
        else verdict["outcome_passed"] is None
    )
    if (
        outer_outcome_type_valid
        and verdict["outcome_passed"] != outcome_state.passed
    ):
        errors.append(
            f"{label} outer verdict outcome_passed disagrees:"
            f" {verdict['outcome_passed']!r} != {outcome_state.passed!r}"
        )
    if (
        type(verdict["interrupted"]) is bool
        and verdict["interrupted"] is not capture_state.interrupted
    ):
        errors.append(
            f"{label} outer verdict interrupted disagrees:"
            f" {verdict['interrupted']!r} != {capture_state.interrupted!r}"
        )
    passed_type_valid = (
        verdict["passed"] is None
        if expected_passed is None
        else type(verdict["passed"]) is bool
    )
    if passed_type_valid and verdict["passed"] != expected_passed:
        errors.append(
            f"{label} outer verdict passed disagrees:"
            f" {verdict['passed']!r} != {expected_passed!r}"
        )
    if type(status) is int:
        expected_status = exit_status(
            adapter_passed,
            capture_state.interrupted,
            outcome_state.evaluable,
        )
        if status != expected_status:
            errors.append(
                f"{label} outer verdict status disagrees:"
                f" {status!r} != {expected_status!r}"
            )
    if not request_valid:
        return None
    assert type(inner_workload) is str
    assert inner_workload in WORKLOADS
    assert inner_variant is None or type(inner_variant) is str
    assert type(prompt_sha) is str
    assert isinstance(input_digests, dict)
    return RequestState(
        inner_workload,
        inner_variant,
        prompt_sha,
        json.dumps(input_digests, sort_keys=True),
    )


def compare(paths: list[Path]) -> dict[str, Any]:
    errors: list[str] = []
    by_subject: dict[
        str, tuple[dict[str, Any] | None, list[NormalizedDraw]]
    ] = {}
    for path in paths:
        try:
            record, outers = load_source(path)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            errors.append(str(error))
            continue
        valid_outers: list[tuple[int, dict[str, Any]]] = []
        for draw, outer in enumerate(outers):
            draw_label = f"{path} draw {draw}"
            if not isinstance(outer, dict):
                errors.append(f"{draw_label} outer is not an object")
                continue
            outer_subject = outer.get("subject")
            if type(outer_subject) is not str or outer_subject not in SUBJECTS:
                errors.append(
                    f"{draw_label} outer has an invalid subject:"
                    f" {outer_subject!r}"
                )
                continue
            valid_outers.append((draw, outer))
        subjects_seen = {outer["subject"] for _, outer in valid_outers}
        if len(subjects_seen) != 1:
            errors.append(f"{path} mixes subjects across draws: {sorted(subjects_seen)}")
            continue
        subject = subjects_seen.pop()
        if subject in by_subject:
            errors.append(f"{path} has an unexpected or duplicate subject: {subject!r}")
            continue
        draws_for_subject: list[NormalizedDraw] = []
        for draw, outer in valid_outers:
            # Every draw carries the whole contract. A sampled subject that
            # satisfies the evidence contract on two draws of three has not
            # satisfied it -- the shape of the evidence is not the thing that
            # is allowed to vary between draws. What the model DID may vary,
            # and that is reported below rather than judged here.
            label = subject if len(outers) == 1 else f"{subject} draw {draw}"
            adapter = outer.get("adapter")
            if outer.get("schema") != "cross-harness-experiment-run/v0.1":
                errors.append(f"{label} has the wrong outer schema")
                continue
            if not isinstance(adapter, dict) or adapter.get("schema") != (
                "cross-harness-adapter-run/v0.1"
            ):
                errors.append(f"{label} has the wrong adapter schema")
                continue
            mapping_fields = (
                "subject",
                "request",
                "apparatus",
                "capabilities",
                "invocation",
                "isolation",
                "capture",
                "lifecycle",
                "workspace",
                "verdict",
                "outcome",
            )
            mappings_valid = True
            for field in mapping_fields:
                if field not in adapter:
                    errors.append(f"{label} adapter is missing {field}")
                    mappings_valid = False
                elif not isinstance(adapter[field], dict):
                    errors.append(
                        f"{label} adapter has an invalid {field}:"
                        f" {adapter[field]!r}"
                    )
                    mappings_valid = False
            if not mappings_valid:
                continue

            draw_errors = len(errors)
            inner_subject = verify_adapter_subject(
                label, adapter["subject"], errors
            )
            adapter_passed = verify_adapter_verdict(
                label, adapter["verdict"], errors
            )
            raw_workload = adapter["request"].get("workload")
            raw_variant = adapter["request"].get("variant")
            outcome_state = verify_outcome_state(
                label,
                adapter["outcome"],
                raw_workload if type(raw_workload) is str else "",
                raw_variant,
                errors,
            )
            main_capture_state = verify_capture(
                label, adapter["capture"], errors
            )
            capture_state = main_capture_state
            oracle_state = None
            guard_oracle_fault = None
            if raw_workload == "repair":
                oracle_state = verify_repair_oracle_evidence(
                    label,
                    adapter.get("oracle_evidence"),
                    adapter["outcome"],
                    errors,
                )
                if capture_state is not None and oracle_state is not None:
                    capture_state = CaptureState(
                        interrupted=(
                            capture_state.interrupted
                            or oracle_state.interrupted
                        ),
                        timed_out=(
                            capture_state.timed_out or oracle_state.timed_out
                        ),
                        measurement_fault=(
                            capture_state.measurement_fault
                            or oracle_state.measurement_fault
                        ),
                    )
            elif raw_workload == "guard":
                guard_oracle_fault = verify_guard_oracle_evidence(
                    label,
                    adapter.get("oracle_evidence"),
                    adapter["outcome"],
                    errors,
                )
            apparatus_state = verify_apparatus(
                label, adapter["apparatus"], errors
            )
            capabilities_state = verify_capabilities(
                label, inner_subject, adapter["capabilities"], errors
            )
            lifecycle_state = verify_lifecycle(
                label,
                inner_subject,
                adapter["lifecycle"],
                adapter["capture"],
                errors,
            )
            verify_outcome_lifecycle(
                label,
                raw_workload if type(raw_workload) is str else "",
                outcome_state,
                lifecycle_state,
                errors,
            )
            verify_invocation(
                label,
                inner_subject,
                raw_workload if type(raw_workload) is str else "",
                adapter["subject"],
                adapter["invocation"],
                adapter["capture"],
                errors,
            )
            verify_isolation(
                label,
                inner_subject,
                raw_workload if type(raw_workload) is str else "",
                adapter["isolation"],
                adapter["invocation"],
                errors,
            )
            if type(raw_workload) is str:
                verify_workspace(
                    label,
                    raw_workload,
                    adapter["workspace"],
                    adapter["outcome"],
                    errors,
                )
            verify_record(label, record, adapter, errors)
            if (
                adapter_passed is True
                and main_capture_state is not None
                and main_capture_state.measurement_fault
            ):
                errors.append(
                    f"{label} adapter verdict passed despite capture fault"
                )
            if (
                adapter_passed is True
                and oracle_state is not None
                and oracle_state.measurement_fault
            ):
                errors.append(
                    f"{label} adapter verdict passed despite repair oracle "
                    "process fault"
                )
            if adapter_passed is True and guard_oracle_fault is True:
                errors.append(
                    f"{label} adapter verdict passed despite guard receipt fault"
                )
            if (
                adapter_passed is True
                and apparatus_state is not None
                and apparatus_state.baseline_agrees is False
            ):
                errors.append(
                    f"{label} adapter verdict passed despite apparatus drift"
                )
            request_state = None
            if (
                inner_subject is not None
                and adapter_passed is not None
                and outcome_state is not None
                and capture_state is not None
            ):
                request_state = verify_outer_binding(
                    label,
                    outer,
                    adapter,
                    inner_subject=inner_subject,
                    adapter_passed=adapter_passed,
                    outcome_state=outcome_state,
                    capture_state=capture_state,
                    errors=errors,
                )
            # Only normalized, fully validated facts can cross this boundary.
            # A malformed draw contributes diagnostics, never half-typed data
            # that a later comparison or summary can accidentally coerce.
            if (
                len(errors) == draw_errors
                and inner_subject is not None
                and adapter_passed is not None
                and outcome_state is not None
                and capture_state is not None
                and lifecycle_state is not None
                and apparatus_state is not None
                and capabilities_state is not None
                and request_state is not None
            ):
                draws_for_subject.append(NormalizedDraw(
                    subject=inner_subject,
                    request=request_state,
                    apparatus_key=apparatus_state.key,
                    capabilities_key=capabilities_state[1],
                    adapter_passed=adapter_passed,
                    outcome=outcome_state,
                    capture=capture_state,
                    lifecycle=lifecycle_state,
                    capabilities=capabilities_state[0],
                ))
        if not draws_for_subject:
            continue
        if len({draw.lifecycle.acquisition for draw in draws_for_subject}) != 1:
            errors.append(
                f"{subject} draws disagree on lifecycle acquisition"
            )
        if len({draw.lifecycle.completeness for draw in draws_for_subject}) != 1:
            errors.append(
                f"{subject} draws disagree on lifecycle completeness"
            )
        if len({draw.capabilities_key for draw in draws_for_subject}) != 1:
            errors.append(f"{subject} draws disagree on capabilities")
        by_subject[subject] = (record, draws_for_subject)

    if set(by_subject) != SUBJECTS:
        errors.append(
            "comparison requires exactly Claude, Codex, DeepSeek, Hermes, and Pi"
        )

    every_draw = [
        draw for _, draws in by_subject.values() for draw in draws
    ]
    if by_subject:
        # Across every draw of every subject, not one representative each: a
        # sampled run that changed prompt or apparatus midway is exactly what
        # these sets exist to catch.
        prompts = {draw.request.prompt_sha256 for draw in every_draw}
        workloads = {draw.request.workload for draw in every_draw}
        variants = {draw.request.variant for draw in every_draw}
        inputs = {draw.request.input_key for draw in every_draw}
        expected_effects = {draw.outcome.oracle_key for draw in every_draw}
        apparatus = {draw.apparatus_key for draw in every_draw}
        if len(prompts) != 1:
            errors.append("subjects did not receive the same prompt bytes")
        if len(workloads) != 1:
            errors.append("subjects did not run the same workload")
        if len(variants) != 1:
            errors.append("subjects did not run the same workload variant")
        if len(inputs) != 1:
            errors.append("subjects did not bind the same experiment inputs")
        if len(expected_effects) != 1:
            errors.append("subjects did not use the same outcome oracle")
        if len(apparatus) != 1:
            # The capture primitive is imported from the installed package, so
            # it is the one input a spec's `inputs` cannot bind. Runs
            # measured by different builds of it are not a comparison, and
            # without this check nothing else in the pipeline would say so.
            errors.append("subjects were not captured by the same apparatus")

    subjects = {}
    for subject, (_, draws) in sorted(by_subject.items()):
        subjects[subject] = {
            "draws": len(draws),
            # Counts, not a rate. A rate over three draws reads as a
            # probability and is not one, and reduction belongs to whoever is
            # asking the question -- never to capture.
            "adapter_passed": sum(
                1 for draw in draws if draw.adapter_passed
            ),
            "outcome_passed": sum(
                1 for draw in draws if draw.outcome.passed is True
            ),
            "timed_out": sum(
                1 for draw in draws if draw.capture.timed_out
            ),
            # Evidence-shape facts, which the contract requires to be identical
            # on every draw. Reported once, and reported as disagreement rather
            # than silently taking the first, if a subject ever varies them.
            "acquisition": _agreed(
                draw.lifecycle.acquisition for draw in draws
            ),
            "completeness": _agreed(
                draw.lifecycle.completeness for draw in draws
            ),
            "tool_attempts": [
                draw.lifecycle.tool_attempts for draw in draws
            ],
            "capabilities": _agreed(draw.capabilities for draw in draws),
        }
    return {
        "schema": "cross-harness-contract-comparison/v0.1",
        "contract_passed": not errors,
        "errors": errors,
        "subjects": subjects,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs=len(SUBJECTS), type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.sources)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        result = {
            "schema": "cross-harness-contract-comparison/v0.1",
            "contract_passed": False,
            "errors": [str(error)],
            "subjects": {},
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["contract_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
