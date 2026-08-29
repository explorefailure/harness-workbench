#!/usr/bin/env python3
"""Assemble exact ordinary Workbench specs and freeze locks before permits."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from harness_workbench import canon

from agent_task_schema import SUBJECTS, bytes_sha256, canonical_sha256


HERE = Path(__file__).resolve().parent
STEP = HERE / "agent_task_step.py"
PHASES = ("write-smoke", "repair-matrix")
STEP_MODULE_INPUTS = (
    "agent_task_archives.py",
    "agent_task_authorization.py",
    "agent_task_broker.py",
    "agent_task_control.py",
    "agent_task_coordinator.py",
    "agent_task_fake_provider.py",
    "agent_task_phase_review.py",
    "agent_task_process.py",
    "agent_task_providers.py",
    "agent_task_routes.py",
    "agent_task_runtime.py",
    "agent_task_schema.py",
    "agent_task_services.py",
    "agent_task_specs.py",
    "agent_task_store.py",
    "agent_task_validate.py",
)
STATIC_INPUTS = (
    "agent_task_step.py",
    *STEP_MODULE_INPUTS,
    "task.json",
    "workspace.zip",
    "fake-provider-plan.json",
    "execution-plan.json",
)


class SpecAssemblyError(ValueError):
    """Pre-call specs cannot be assembled exactly as planned."""


def build_spec_document(
    *, subject: str, phase: str, store_nonce: str,
) -> dict[str, Any]:
    if (
        subject not in SUBJECTS
        or phase not in PHASES
        or type(store_nonce) is not str
        or len(store_nonce) < 16
    ):
        raise SpecAssemblyError("spec identity is outside the exact plan")
    draws = 1 if phase == "write-smoke" else 3
    return {
        "schema": "hwbspec/v0.1",
        "run_class": "discovery",
        "features_root": "harness_workbench:builtin",
        "features": [
            {"name": "freeze"},
            {"name": "receipt"},
            {"name": "retry", "config": {"max": 2}},
            {"name": "sample", "config": {"n": draws}},
            {"name": "timing"},
        ],
        "steps": [{
            "id": f"{subject}-agent-task",
            "argv": [
                os.path.abspath(sys.executable), "agent_task_step.py",
                "--phase", phase, "--subject", subject,
                "--store-nonce", store_nonce,
                "--task", "task.json",
                "--workspace-archive", "workspace.zip",
                "--transport-plan", "fake-provider-plan.json",
                "--execution-plan", "execution-plan.json",
            ],
            "inputs": list(STATIC_INPUTS),
        }],
    }


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def assemble_pre_call_specs(bundle: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Materialize every planned spec and lock before the first permit."""
    if bundle.is_symlink() or not bundle.is_dir():
        raise SpecAssemblyError("bundle root is not a real directory")
    root = bundle / "precall-specs"
    if root.exists() or root.is_symlink():
        raise SpecAssemblyError("pre-call spec root is not fresh")
    planned = plan.get("inputs", {}).get("specs")
    nonces = plan.get("store_nonces")
    if (
        type(planned) is not dict or set(planned) != set(PHASES)
        or type(nonces) is not dict or set(nonces) != set(PHASES)
    ):
        raise SpecAssemblyError("execution plan spec phases are not exact")
    source_inputs = {
        "agent_task_step.py": STEP,
        **{name: HERE / name for name in STEP_MODULE_INPUTS},
        "task.json": bundle / "task.json",
        "workspace.zip": bundle / "workspace.zip",
        "fake-provider-plan.json": bundle / "fake-provider-plan.json",
        "execution-plan.json": bundle / "execution-plan.json",
    }
    apparatus = plan.get("inputs", {}).get("apparatus", {})
    for name in ("agent_task_step.py", *STEP_MODULE_INPUTS):
        source = source_inputs[name]
        if (
            source.is_symlink()
            or not source.is_file()
            or bytes_sha256(source.read_bytes()) != apparatus.get(name)
        ):
            raise SpecAssemblyError(f"pre-call step apparatus drifted: {name}")
    root.mkdir(mode=0o700)
    rows: dict[str, Any] = {}
    for phase in PHASES:
        if (
            type(planned[phase]) is not dict
            or type(nonces[phase]) is not dict
            or set(planned[phase]) != set(SUBJECTS)
            or set(nonces[phase]) != set(SUBJECTS)
        ):
            raise SpecAssemblyError(f"planned {phase} subjects are not exact-five")
        phase_root = root / phase
        phase_root.mkdir(mode=0o700)
        rows[phase] = {}
        for subject in SUBJECTS:
            own = phase_root / subject
            own.mkdir(mode=0o700)
            for name, source in source_inputs.items():
                if source.is_symlink() or not source.is_file():
                    raise SpecAssemblyError(f"pre-call spec input is invalid: {name}")
                shutil.copy2(source, own / name)
            document = build_spec_document(
                subject=subject, phase=phase, store_nonce=nonces[phase][subject]
            )
            expected = planned[phase][subject]
            if (
                type(expected) is not dict
                or expected.get("document") != document
                or expected.get("sha256") != canonical_sha256(document)
            ):
                raise SpecAssemblyError(f"planned spec digest disagrees: {phase}/{subject}")
            spec_path = own / f"{subject}.json"
            _write_json(spec_path, document)
            digests = {
                name: canon.digest_file(str(own / name)) for name in STATIC_INPUTS
            }
            lock_path = own / f"{subject}.freeze.lock"
            _write_json(lock_path, {"digests": digests})
            rows[phase][subject] = {
                "spec_sha256": canonical_sha256(document),
                "spec_file_sha256": bytes_sha256(spec_path.read_bytes()),
                "freeze_lock_sha256": bytes_sha256(lock_path.read_bytes()),
                "inputs": digests,
            }
    return {
        "schema": "agent-task-precall-spec-assembly/v0.1",
        "specs": rows,
        "tree_sha256": canon.digest_tree(str(root)),
    }
