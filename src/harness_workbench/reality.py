"""User-facing Reality Checks composed from ordinary Workbench machinery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import features, interrupt, runner, spec as specmod, subject_tree
from .canon import canon_bytes, digest_file


CHECK = "coding/python-bug-fix"
RECIPE_SCHEMA = "hwb-recipe/v0.1"
RESULT_SCHEMA = "hwb-reality-check-result/v0.1"
SPEC_NAME = "reality-check.json"
RECIPE_NAME = "recipe.json"
STEP_ID = "pi-python-bug-fix"
CANONICAL_FILES = (
    "repair_fixture/slugger.py",
    "repair_fixture/test_slugger.py",
    "repair_task.md",
)
BLIND_SPOTS = (
    "Workloads and callbacks are trusted code; this is not a security sandbox.",
    "Only the disposable workspace manifests, declared inputs, process evidence, and tests are observed.",
    "Network, host-wide filesystem, child processes outside the owned group, and model quality beyond this job are unobserved.",
    "A pass applies only to the recorded Pi, model, runtime, prompt, fixture, and sensors.",
)


class RealityError(ValueError):
    """A Reality Check could not be prepared or interpreted honestly."""


def _default_recipe() -> Path:
    return Path.cwd() / ".hwb" / "recipes" / "coding-python-bug-fix"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(canon_bytes(value))


def _canonical_digests() -> dict[str, str]:
    root = Path(subject_tree.subjects_root())
    return {name: digest_file(root / name) for name in CANONICAL_FILES}


def _spec() -> dict[str, Any]:
    definition = (
        Path(__file__).resolve().parent
        / "reality_checks" / "coding_python_bug_fix.json"
    )
    value = json.loads(definition.read_text(encoding="utf-8"))
    value["steps"][0]["inputs"].append(RECIPE_NAME)
    return value


def prepare_recipe(destination: Path) -> tuple[Path, bool]:
    """Create the thin manifest/spec over the existing subject tree."""
    manifest_path = destination / RECIPE_NAME
    if destination.exists():
        if not manifest_path.is_file():
            raise RealityError(
                f"recipe directory exists without {RECIPE_NAME}: {destination}"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RealityError(f"could not read Recipe: {error}") from error
        if manifest.get("schema") != RECIPE_SCHEMA or manifest.get("check") != CHECK:
            raise RealityError(f"Recipe does not describe {CHECK}: {destination}")
        return destination / SPEC_NAME, False

    destination.mkdir(parents=True)
    subject_tree.materialize(str(destination))
    selection_path = destination / "model_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if "local-ollama" not in selection.get("profiles", {}):
        raise RealityError("shipped subject tree has no local-ollama model profile")
    selection["active"] = "local-ollama"
    _write_json(selection_path, selection)
    _write_json(manifest_path, {
        "schema": RECIPE_SCHEMA,
        "check": CHECK,
        "spec": SPEC_NAME,
        "subject": "pi",
        "workload": "repair",
        "model_profile": "local-ollama",
        "canonical_fixture_digests": _canonical_digests(),
        "rerun": f"hwb try {CHECK} --recipe {destination}",
    })
    _write_json(destination / SPEC_NAME, _spec())
    return destination / SPEC_NAME, True


def _attempt_document(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "steps" / STEP_ID / "attempts" / "0" / "stdout.bin"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RealityError(f"sealed Pi attempt is not readable JSON: {error}") from error
    if not isinstance(value, dict):
        raise RealityError("sealed Pi attempt is not a JSON object")
    return value


def verify_run(run_dir: Path, recipe_dir: Path) -> dict[str, Any]:
    """Independently derive the verdict from the sealed record and effects."""
    facts: list[dict[str, Any]] = []
    errors: list[str] = []
    lifecycle = interrupt.inspect_state(str(run_dir))
    integrity_ok = lifecycle.get("state") == interrupt.COMPLETE
    facts.append({"fact": "sealed_record", "observed": integrity_ok})
    if not integrity_ok:
        errors.append("run record is incomplete, nonconforming, or edited")

    expected = _canonical_digests()
    template_ok = all(
        (recipe_dir / name).is_file()
        and digest_file(recipe_dir / name) == digest
        for name, digest in expected.items()
    )
    facts.append({"fact": "original_template_unchanged", "observed": template_ok})
    if not template_ok:
        errors.append("Recipe fixture/template differs from the shipped canonical bytes")

    try:
        document = _attempt_document(run_dir)
    except RealityError as error:
        return {
            "verdict": "invalid", "facts": facts,
            "changed_files": [], "errors": errors + [str(error)], "setup": {},
        }

    if isinstance(document.get("error"), str):
        errors.append(document["error"])
        return {
            "verdict": "invalid", "facts": facts,
            "changed_files": [], "errors": errors,
            "setup": {"subject": "Pi", "model": "unobserved",
                      "model_profile": "unobserved", "pi_version": "unobserved"},
        }

    adapter = document.get("adapter") if isinstance(document.get("adapter"), dict) else {}
    outcome = adapter.get("outcome") if isinstance(adapter.get("outcome"), dict) else {}
    oracle = adapter.get("oracle_evidence") if isinstance(adapter.get("oracle_evidence"), dict) else {}
    initial = oracle.get("initial_test") if isinstance(oracle.get("initial_test"), dict) else {}
    final = oracle.get("final_test") if isinstance(oracle.get("final_test"), dict) else {}
    baseline_failed = initial.get("returncode") == 1 and initial.get("termination_reason") is None
    final_passed = final.get("returncode") == 0 and final.get("termination_reason") is None
    facts.extend([
        {"fact": "baseline_failed_before_attempt", "observed": baseline_failed},
        {"fact": "required_test_passed_after_attempt", "observed": final_passed},
    ])

    workspace = adapter.get("workspace") if isinstance(adapter.get("workspace"), dict) else {}
    before = workspace.get("before") if isinstance(workspace.get("before"), list) else []
    after = workspace.get("after") if isinstance(workspace.get("after"), list) else []
    before_map = {item.get("path"): item for item in before if isinstance(item, dict)}
    after_map = {item.get("path"): item for item in after if isinstance(item, dict)}
    changed = sorted(
        path for path in set(before_map) | set(after_map)
        if before_map.get(path) != after_map.get(path)
    )
    allowed_changes = changed == ["slugger.py"]
    facts.append({"fact": "only_permitted_files_changed", "observed": allowed_changes})

    adapter_verdict = adapter.get("verdict") if isinstance(adapter.get("verdict"), dict) else {}
    measured = adapter_verdict.get("passed") is True
    independent_outcome = outcome.get("passed") is True
    facts.extend([
        {"fact": "adapter_measurement_valid", "observed": measured},
        {"fact": "independent_repair_oracle_passed", "observed": independent_outcome},
        {"fact": "model_completion_claim_used_as_verdict", "observed": False},
    ])
    required = integrity_ok and template_ok and baseline_failed and final_passed and allowed_changes
    if not baseline_failed:
        errors.append("independent baseline test did not prove the fixture was red")
    if not final_passed:
        errors.append("independent final test did not prove the fixture was green")
    if not allowed_changes:
        errors.append(f"changed files were {changed!r}; only slugger.py is permitted")
    if not measured:
        errors.append("Pi adapter measurement was invalid")
    if not independent_outcome:
        errors.extend(str(item) for item in outcome.get("errors", []) if isinstance(item, str))

    if not integrity_ok or not measured:
        verdict = "invalid"
    elif required and independent_outcome:
        verdict = "pass"
    elif baseline_failed or final_passed:
        verdict = "partial"
    else:
        verdict = "fail"
    subject = adapter.get("subject") if isinstance(adapter.get("subject"), dict) else {}
    return {
        "verdict": verdict,
        "facts": facts,
        "changed_files": changed,
        "errors": errors,
        "setup": {
            "subject": "Pi",
            "model": subject.get("model", "unobserved"),
            "model_profile": subject.get("model_profile", "unobserved"),
            "model_digest": subject.get("model_digest", "unobserved"),
            "pi_version": subject.get("version", "unobserved"),
        },
    }


def run_check(check: str, *, runs_root: str, recipe_dir: str | None) -> dict[str, Any]:
    if check != CHECK:
        raise RealityError(f"unknown Reality Check: {check}")
    destination = (Path(recipe_dir).expanduser() if recipe_dir else _default_recipe()).resolve()
    spec_path, created = prepare_recipe(destination)
    try:
        specification = specmod.load(str(spec_path))
        loaded = features.resolve(specification)
        record = runner.execute(specification, loaded, runs_root)
    except (specmod.SpecError, features.FeatureError, runner.HarnessError, OSError) as error:
        raise RealityError(str(error)) from error
    run_dir = Path(runs_root).resolve() / record["run_id"]
    verified = verify_run(run_dir, destination)
    return {
        "schema": RESULT_SCHEMA,
        "job": CHECK,
        **verified,
        "record": str(run_dir),
        "recipe": str(destination),
        "recipe_created": created,
        "blind_spots": list(BLIND_SPOTS),
        "rerun": f"hwb try {CHECK} --recipe {destination}",
    }


def print_result(result: dict[str, Any]) -> None:
    print(f"Reality Check: {result['job']}")
    setup = result.get("setup", {})
    print(
        "Setup: {subject} {pi_version} · {model} ({model_profile}) · digest {model_digest}".format(
            subject=setup.get("subject", "Pi"),
            pi_version=setup.get("pi_version", "unobserved"),
            model=setup.get("model", "unobserved"),
            model_profile=setup.get("model_profile", "unobserved"),
            model_digest=setup.get("model_digest", "unobserved"),
        )
    )
    print(f"Verdict: {str(result['verdict']).upper()}")
    print("\nVerification facts")
    for item in result["facts"]:
        state = "yes" if item["observed"] else "NO"
        print(f"  {state:3}  {item['fact']}")
    print("\nChanged files: " + (", ".join(result["changed_files"]) or "none observed"))
    if result["errors"]:
        print("\nWhy this was not a pass")
        for error in result["errors"]:
            print(f"  - {error}")
    print(f"\nSealed record: {result['record']}")
    print(f"Recipe: {result['recipe']}")
    print(f"Rerun: {result['rerun']}")
    print("\nExplicit blind spots")
    for item in result["blind_spots"]:
        print(f"  - {item}")
