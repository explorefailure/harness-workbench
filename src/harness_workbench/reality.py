"""User-facing Reality Checks composed from ordinary Workbench machinery."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
import sys
import threading
from typing import Any, Callable, Optional
from urllib.error import URLError
from urllib.request import urlopen

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
MODEL_DIGEST_CONTRACT = "recorded_digest_not_pinned_reproduction"
EDITABLE_RECIPE_FILE = "model_selection.json"
# Profiles a Recipe may run.  Ollama stays the default: it is the easiest first
# setup.  llama.cpp is opt-in with `hwb try --profile local-llamacpp`.
RECIPE_PROFILES = ("local-ollama", "local-llamacpp")
DEFAULT_RECIPE_PROFILE = "local-ollama"
LOCAL_PROFILES_ENV = "HWB_MODEL_PROFILES"
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_CONTENT_ADDRESSED_GGUF = re.compile(r"(?:sha256[-:])?([0-9a-f]{64})(?:\.gguf)?")


def _shipped_selection() -> dict[str, Any]:
    path = Path(subject_tree.subjects_root()) / "model_selection.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _available_selection() -> dict[str, Any]:
    """Committed profiles plus owner-side additions used by Reality Checks.

    The certified subject adapter independently applies the same no-redefinition
    rule.  A selected owner profile is copied into the Recipe's one editable
    file, so a retained Recipe remains self-describing without the overlay.
    """
    selection = _shipped_selection()
    overlay_path = os.environ.get(LOCAL_PROFILES_ENV)
    if not overlay_path:
        return selection
    try:
        overlay = json.loads(Path(overlay_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RealityError(f"{LOCAL_PROFILES_ENV} is unreadable: {error}") from None
    profiles = overlay.get("profiles") if isinstance(overlay, dict) else None
    if not isinstance(profiles, dict) or not all(
        isinstance(name, str) and isinstance(profile, dict)
        for name, profile in profiles.items()
    ):
        raise RealityError(f"{LOCAL_PROFILES_ENV} must hold an object of profiles")
    clashes = sorted(set(profiles) & set(selection["profiles"]))
    if clashes:
        raise RealityError(
            f"{LOCAL_PROFILES_ENV} may not redefine committed profiles: "
            + ", ".join(clashes)
        )
    for name in profiles:
        if _PROFILE_NAME.fullmatch(name) is None:
            raise RealityError(
                f"{LOCAL_PROFILES_ENV} profile name {name!r} is unsafe; use letters, "
                "digits, dot, underscore or hyphen"
            )
    selection["profiles"] = {**selection["profiles"], **copy.deepcopy(profiles)}
    return selection


def _local_profile_names(selection: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        name for name, profile in selection["profiles"].items()
        if profile.get("kind") == "local"
        and profile.get("runtime", "ollama") in {"ollama", "llama.cpp"}
    )


def available_recipe_profiles() -> tuple[str, ...]:
    """Names that can create this local-runtime Reality Check."""
    return _local_profile_names(_available_selection())


def _local_model_digest(model: str, base_url: str) -> Optional[str]:
    """The digest this machine's Ollama serves for ``model``, or None.

    A Recipe's model baseline comes from the person's own setup, never from
    the digest some other machine happened to ship with the package.
    """
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3]
    try:
        with urlopen(endpoint + "/api/tags", timeout=3) as response:
            payload = json.load(response)
    except (URLError, OSError, ValueError):
        return None
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if isinstance(item, dict) and item.get("name") == model:
            digest = item.get("digest")
            if isinstance(digest, str) and len(digest) == 64:
                return digest
    return None


def _llamacpp_identity(model: str, base_url: str) -> tuple[Optional[str], Optional[str]]:
    """(file digest, build) this machine's llama-server reports, or Nones.

    llama.cpp publishes no digest; the served GGUF must be named by its own
    SHA-256 and the alias must be the selected model.  Anything else reads as
    unknown, and the certified adapter's own check then fails the run.
    """
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3]
    try:
        with urlopen(endpoint + "/props", timeout=3) as response:
            payload = json.load(response)
    except (URLError, OSError, ValueError):
        return None, None
    if not isinstance(payload, dict) or payload.get("model_alias") != model:
        return None, None
    build = payload.get("build_info")
    build = build if isinstance(build, str) and build else None
    name = str(payload.get("model_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    match = _CONTENT_ADDRESSED_GGUF.fullmatch(name)
    return (match.group(1) if match else None), build


def _live_identity(profile: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """(model digest, runtime build) for a Recipe profile; build is llama.cpp only."""
    model, base_url = str(profile["models"]["pi"]), str(profile["base_url"])
    if profile.get("runtime") == "llama.cpp":
        return _llamacpp_identity(model, base_url)
    return _local_model_digest(model, base_url), None


def _recipe_profile(selection: Any) -> tuple[str, dict[str, Any]]:
    name = selection.get("active") if isinstance(selection, dict) else None
    profiles = selection.get("profiles") if isinstance(selection, dict) else None
    profile = profiles.get(name) if isinstance(profiles, dict) else None
    if (_PROFILE_NAME.fullmatch(name or "") is None or not isinstance(profile, dict)
            or profile.get("kind") != "local"
            or profile.get("runtime", "ollama") not in {"ollama", "llama.cpp"}):
        raise RealityError("Recipe must keep a valid local runtime profile active")
    return str(name), profile


def refresh_live_digest(destination: Path) -> None:
    """Point the Recipe's checked digest at what this machine serves now.

    The shared adapter (a certified, hash-bound file) refuses a run whose live
    digest differs from ``model_digest``.  A Recipe records rather than pins,
    so before each run ``model_digest`` is set to the live digest and the
    person's reference stays in ``baseline_digest``; `model_change` then
    reports any difference without refusing.  If Ollama cannot be read,
    nothing changes and the adapter's own check still fails the run.
    """
    path = destination / EDITABLE_RECIPE_FILE
    try:
        selection = json.loads(path.read_text(encoding="utf-8"))
        _, local = _recipe_profile(selection)
        if not isinstance(local["models"]["pi"], str) or not isinstance(local["base_url"], str):
            raise TypeError("profile must name a Pi model and a base_url")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RealityError(f"editable model selection is malformed: {error}") from error
    live, build = _live_identity(local)
    if not live:
        return
    baseline = local.get("baseline_digest")
    changed = local.get("model_digest") != live
    if not (isinstance(baseline, str) and len(baseline) == 64):
        local["baseline_digest"] = live  # first readable digest becomes the baseline
        changed = True
    if changed:
        local["model_digest"] = live
    if build is not None and local.get("runtime") == "llama.cpp":
        # The build is recorded the same way: a newer llama.cpp is reported
        # against the baseline, never refused.
        if not local.get("baseline_runtime_build"):
            local["baseline_runtime_build"] = build
            changed = True
        if local.get("runtime_build") != build:
            local["runtime_build"] = build
            changed = True
    if changed:
        _write_json(path, selection)


def model_change(recipe_dir: Path, setup: dict[str, Any]) -> dict[str, Any]:
    """Compare the digest a run recorded with the Recipe's model baseline.

    Informational only: it never changes a verdict.  A different digest means
    the result applies to the recorded model, and the Recipe needs re-proof
    before its earlier result is relied on.
    """
    baseline = None
    profile: dict[str, Any] = {}
    try:
        selection = json.loads((recipe_dir / EDITABLE_RECIPE_FILE).read_text(encoding="utf-8"))
        _, profile = _recipe_profile(selection)
        value = profile.get("baseline_digest")
        baseline = value if isinstance(value, str) and len(value) == 64 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RealityError):
        baseline = None
    observed = setup.get("model_digest") if isinstance(setup, dict) else None
    observed = observed if isinstance(observed, str) and len(observed) == 64 else None
    matches = None if baseline is None or observed is None else baseline == observed
    change: dict[str, Any] = {
        "recipe_baseline": baseline, "recorded": observed, "matches_baseline": matches,
    }
    if profile.get("runtime") == "llama.cpp":
        base_build, build = profile.get("baseline_runtime_build"), profile.get("runtime_build")
        change["runtime_build"] = {
            "recipe_baseline": base_build or None,
            "recorded": build or None,
            "matches_baseline": None if not (base_build and build) else base_build == build,
        }
    return change


class RealityError(ValueError):
    """A Reality Check could not be prepared or interpreted honestly."""


def _default_recipe(profile: str = DEFAULT_RECIPE_PROFILE) -> Path:
    if _PROFILE_NAME.fullmatch(profile) is None:
        raise RealityError(f"unsafe Recipe profile name {profile!r}")
    name = "coding-python-bug-fix"
    if profile != DEFAULT_RECIPE_PROFILE:
        name += "-" + profile
    return Path.cwd() / ".hwb" / "recipes" / name


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
    value["env"] = ["HWB_PYTHON"]
    value["steps"][0]["inputs"].append(RECIPE_NAME)
    return value


def _required_inputs(specification: dict[str, Any]) -> list[str]:
    return sorted({
        name
        for step in specification.get("steps", [])
        for name in step.get("inputs", [])
        if name != RECIPE_NAME
    })


def _file_digests(root: Path, names: list[str]) -> dict[str, str]:
    return {name: digest_file(root / name) for name in names}


def _validate_recipe(destination: Path) -> Path:
    """Validate the fixed Recipe envelope before any command is executed.

    Recipes are intentionally editable configuration, not immutable
    reproductions.  Only ``model_selection.json`` is editable; all executable,
    task, fixture, and spec bytes remain bound to the manifest created by us.
    Every effective input is subsequently preserved/digested by the ordinary
    Workbench record.
    """
    manifest_path = destination / RECIPE_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RealityError(f"could not read Recipe: {error}") from error
    required_manifest = {
        "schema", "check", "spec", "subject", "workload", "model_profile",
        "model_digest_contract", "editable_files", "bound_file_digests",
        "canonical_fixture_digests", "rerun",
    }
    if not isinstance(manifest, dict) or set(manifest) != required_manifest:
        raise RealityError("Recipe manifest is incomplete or has unknown fields")
    expected_values = {
        "schema": RECIPE_SCHEMA, "check": CHECK, "spec": SPEC_NAME,
        "subject": "pi", "workload": "repair",
        "model_profile": manifest.get("model_profile"),
        "model_digest_contract": MODEL_DIGEST_CONTRACT,
        "editable_files": [EDITABLE_RECIPE_FILE],
    }
    for key, expected in expected_values.items():
        if manifest.get(key) != expected:
            raise RealityError(f"Recipe field {key!r} must be {expected!r}")

    spec_path = destination / SPEC_NAME
    try:
        specification = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RealityError(f"Recipe spec is missing or malformed: {error}") from error
    if specification != _spec():
        raise RealityError("Recipe spec differs from the packaged Reality Check")
    required_files = _required_inputs(specification)
    expected_bound = manifest.get("bound_file_digests")
    if not isinstance(expected_bound, dict):
        raise RealityError("Recipe bound_file_digests must be an object")
    bound_names = sorted(name for name in required_files if name != EDITABLE_RECIPE_FILE)
    if set(expected_bound) != set(bound_names):
        raise RealityError("Recipe bound-file inventory is incomplete or inconsistent")
    for name in required_files:
        path = destination / name
        if path.is_symlink() or not path.is_file():
            raise RealityError(f"Recipe required file is missing or unsafe: {name}")
        if name != EDITABLE_RECIPE_FILE and digest_file(path) != expected_bound[name]:
            raise RealityError(f"Recipe bound file was edited: {name}")

    try:
        selection = json.loads((destination / EDITABLE_RECIPE_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise RealityError(f"editable model selection is malformed: {error}") from error
    name, local = _recipe_profile(selection)
    if name != manifest.get("model_profile"):
        raise RealityError(
            f"Recipe profile changed: the manifest says {manifest.get('model_profile')!r}, "
            f"model_selection.json has {name!r} active"
        )
    if local.get("digest_contract") != MODEL_DIGEST_CONTRACT:
        raise RealityError(f"{name} must declare the recorded-only digest contract")
    if local.get("verify_digest") is not True:
        raise RealityError(f"{name} must record the live model digest")
    if name == "local-llamacpp" and local.get("runtime") != "llama.cpp":
        raise RealityError("local-llamacpp must keep the llama.cpp runtime")
    models = local.get("models")
    if not isinstance(models, dict) or not isinstance(models.get("pi"), str) or not models["pi"]:
        raise RealityError(f"{name} must name a Pi model")
    return spec_path


def prepare_recipe(destination: Path, profile: Optional[str] = None) -> tuple[Path, bool]:
    """Create the thin manifest/spec over the existing subject tree.

    `profile` chooses the model profile for a NEW Recipe (default Ollama).  An
    existing Recipe keeps the profile it was made with; asking for another one
    is refused rather than silently ignored.
    """
    manifest_path = destination / RECIPE_NAME
    if destination.exists():
        if not manifest_path.is_file():
            raise RealityError(
                f"recipe directory exists without {RECIPE_NAME}: {destination}"
            )
        spec_path = _validate_recipe(destination)
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))["model_profile"]
        if profile is not None and profile != existing:
            raise RealityError(
                f"Recipe at {destination} uses {existing}; choose another --recipe "
                f"directory for {profile}"
            )
        return spec_path, False
    profile = profile or DEFAULT_RECIPE_PROFILE

    available = _available_selection()
    choices = _local_profile_names(available)
    if profile not in choices:
        raise RealityError(
            f"unknown Recipe profile {profile!r}; choose one of these local profiles: "
            + ", ".join(choices)
        )

    destination.mkdir(parents=True)
    subject_tree.materialize(str(destination))
    selection_path = destination / "model_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if profile not in selection.get("profiles", {}):
        selection["profiles"][profile] = copy.deepcopy(available["profiles"][profile])
    selection["active"] = profile
    local = selection["profiles"][profile]
    local["identity_strength"] = "recorded_local_content_digest"
    local["digest_contract"] = MODEL_DIGEST_CONTRACT
    # Baseline from this machine's runtime, never the digest the package
    # shipped; empty when it cannot be read now (the first run adopts it).
    live, build = _live_identity(local)
    local["baseline_digest"] = live or ""
    local["model_digest"] = live or ""
    if local.get("runtime") == "llama.cpp" and build:
        local["baseline_runtime_build"] = build
        local["runtime_build"] = build
    _write_json(selection_path, selection)
    _write_json(destination / SPEC_NAME, _spec())
    required_files = _required_inputs(_spec())
    bound_names = [name for name in required_files if name != EDITABLE_RECIPE_FILE]
    _write_json(manifest_path, {
        "schema": RECIPE_SCHEMA,
        "check": CHECK,
        "spec": SPEC_NAME,
        "subject": "pi",
        "workload": "repair",
        "model_profile": profile,
        "model_digest_contract": MODEL_DIGEST_CONTRACT,
        "editable_files": [EDITABLE_RECIPE_FILE],
        "bound_file_digests": _file_digests(destination, bound_names),
        "canonical_fixture_digests": _canonical_digests(),
        "rerun": f"hwb try {CHECK} --recipe {destination}",
    })
    return _validate_recipe(destination), True


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

    adapter = document.get("adapter")
    malformed: list[str] = []
    if not isinstance(adapter, dict):
        adapter = {}
        malformed.append("adapter evidence is missing or malformed")
    outcome = adapter.get("outcome")
    if not isinstance(outcome, dict) or not isinstance(outcome.get("passed"), bool) or not isinstance(outcome.get("errors"), list):
        outcome = {}
        malformed.append("independent repair outcome is missing or malformed")
    oracle = adapter.get("oracle_evidence")
    if not isinstance(oracle, dict):
        oracle = {}
        malformed.append("oracle evidence is missing or malformed")
    initial = oracle.get("initial_test")
    final = oracle.get("final_test")
    for label, value in (("initial", initial), ("final", final)):
        if (not isinstance(value, dict)
                or not isinstance(value.get("returncode"), int)
                or "termination_reason" not in value):
            malformed.append(f"{label} required-test evidence is missing or malformed")
    initial = initial if isinstance(initial, dict) else {}
    final = final if isinstance(final, dict) else {}
    baseline_failed = initial.get("returncode") == 1 and initial.get("termination_reason") is None
    final_passed = final.get("returncode") == 0 and final.get("termination_reason") is None
    facts.extend([
        {"fact": "baseline_failed_before_attempt", "observed": baseline_failed},
        {"fact": "required_test_passed_after_attempt", "observed": final_passed},
    ])

    workspace = adapter.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
        malformed.append("workspace evidence is missing or malformed")
    before = workspace.get("before")
    after = workspace.get("after")
    valid_inventory = all(
        isinstance(items, list) and all(
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("sha256"), str)
            for item in items
        ) for items in (before, after)
    )
    if not valid_inventory:
        malformed.append("workspace inventories are missing or malformed")
    before = before if isinstance(before, list) else []
    after = after if isinstance(after, list) else []
    before_map = {item.get("path"): item for item in before if isinstance(item, dict)}
    after_map = {item.get("path"): item for item in after if isinstance(item, dict)}
    changed = sorted(
        path for path in set(before_map) | set(after_map)
        if before_map.get(path) != after_map.get(path)
    )
    allowed_changes = changed == ["slugger.py"]
    facts.append({"fact": "only_permitted_files_changed", "observed": allowed_changes})

    adapter_verdict = adapter.get("verdict")
    measured = (
        isinstance(adapter_verdict, dict)
        and adapter_verdict.get("passed") is True
        and isinstance(adapter_verdict.get("errors"), list)
    )
    if not isinstance(adapter_verdict, dict) or not isinstance(adapter_verdict.get("passed"), bool):
        malformed.append("adapter measurement is missing or malformed")
    independent_outcome = outcome.get("passed") is True
    facts.extend([
        {"fact": "adapter_measurement_valid", "observed": measured},
        {"fact": "independent_repair_oracle_passed", "observed": independent_outcome},
        {"fact": "model_completion_claim_used_as_verdict", "observed": False},
    ])
    required = (integrity_ok and template_ok and not malformed and baseline_failed
                and final_passed and allowed_changes and measured)
    errors.extend(malformed)
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

    if not integrity_ok or not template_ok or malformed or not measured or not baseline_failed:
        verdict = "invalid"
    elif required and independent_outcome:
        verdict = "pass"
    elif final_passed:
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


PROGRESS_PHASES = ("recipe", "model_identity", "run_started", "agent", "verifying")


def run_check(
    check: str, *, runs_root: str, recipe_dir: str | None, profile: str | None = None,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    stop: threading.Event | None = None,
) -> dict[str, Any]:
    """Run the Reality Check. `progress(phase, detail)` is told each phase of
    PROGRESS_PHASES as it begins. It is display only and never part of the
    record; an observer that raises is ignored. Setting `stop` ends the
    running step early; the attempt is then marked `stopped` in the record."""
    def phase(name: str, **detail: Any) -> None:
        if progress is not None:
            try:
                progress(name, detail)
            except Exception:
                pass

    def observe(event: str, detail: dict[str, Any]) -> None:
        if event == "run_started":
            phase("run_started", run_id=detail.get("run_id"))
        elif event == "step_started" and detail.get("step_id") == STEP_ID:
            phase("agent")

    if check != CHECK:
        raise RealityError(f"unknown Reality Check: {check}")
    phase("recipe")
    destination = (
        Path(recipe_dir).expanduser() if recipe_dir
        else _default_recipe(profile or DEFAULT_RECIPE_PROFILE)
    ).resolve()
    spec_path, created = prepare_recipe(destination, profile)
    phase("model_identity")
    refresh_live_digest(destination)
    specification_value = json.loads(spec_path.read_text(encoding="utf-8"))
    effective_digests = _file_digests(
        destination, _required_inputs(specification_value) + [RECIPE_NAME, SPEC_NAME]
    )
    try:
        specification = specmod.load(str(spec_path))
        loaded = features.resolve(specification)
        # The console-script shebang selects this interpreter even when its
        # virtual environment is not activated.  Carry that identity across
        # the shell boundary explicitly; the spec declares it, so the normal
        # record retains the effective value without baking a machine path
        # into the portable Recipe.
        environment = os.environ.copy()
        # The selected owner profile has already been copied into the Recipe.
        # Reapplying its creation-time overlay inside the certified adapter
        # would look like an attempted redefinition of that embedded profile.
        environment.pop(LOCAL_PROFILES_ENV, None)
        environment["HWB_PYTHON"] = sys.executable
        record = runner.execute(
            specification, loaded, runs_root, environment=environment,
            observer=observe, stop=stop,
        )
    except (specmod.SpecError, features.FeatureError, runner.HarnessError, OSError) as error:
        raise RealityError(str(error)) from error
    run_dir = Path(runs_root).resolve() / record["run_id"]
    phase("verifying")
    verified = verify_run(run_dir, destination)
    return {
        "schema": RESULT_SCHEMA,
        "job": CHECK,
        **verified,
        "model_change": model_change(destination, verified.get("setup", {})),
        "record": str(run_dir),
        "recipe": str(destination),
        "recipe_created": created,
        "recipe_contract": "editable configuration; only model_selection.json may change",
        "effective_recipe_digests": effective_digests,
        "model_digest_contract": MODEL_DIGEST_CONTRACT,
        "blind_spots": list(BLIND_SPOTS),
        "rerun": f"hwb try {CHECK} --recipe {destination}",
    }


def _model_line(change: dict[str, Any]) -> str:
    recorded, baseline = change.get("recorded"), change.get("recipe_baseline")
    if not recorded:
        return "Model digest: not observed in this run"
    if not baseline:
        return (f"Model digest: recorded {recorded[:12]}; the Recipe has no baseline yet "
                "(the model runtime was not readable when it was created)")
    if change.get("matches_baseline"):
        return f"Model digest: recorded {recorded[:12]}; matches the Recipe's baseline"
    return (f"Model digest: recorded {recorded[:12]}; the Recipe's baseline is {baseline[:12]}. "
            "The model changed since the Recipe was made, so this result applies to the "
            "recorded digest. Re-prove before relying on earlier results; set baseline_digest "
            f"in {EDITABLE_RECIPE_FILE} to adopt the new model.")


def _build_line(change: dict[str, Any]) -> str | None:
    """llama.cpp Recipes also record the server build; Ollama Recipes have none."""
    build = change.get("runtime_build")
    if not isinstance(build, dict):
        return None
    recorded, baseline = build.get("recorded"), build.get("recipe_baseline")
    if not recorded:
        return "llama.cpp build: not readable before this run"
    if build.get("matches_baseline") or not baseline:
        return f"llama.cpp build: {recorded}"
    return (f"llama.cpp build: {recorded}; the Recipe's baseline is {baseline}. "
            "Re-prove before relying on earlier results; set baseline_runtime_build "
            f"in {EDITABLE_RECIPE_FILE} to adopt it.")


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
    print(f"Recipe integrity: {result['recipe_contract']}")
    print(_model_line(result.get("model_change") or {}))
    build_line = _build_line(result.get("model_change") or {})
    if build_line:
        print(build_line)
    print(f"Rerun: {result['rerun']}")
    print("\nExplicit blind spots")
    for item in result["blind_spots"]:
        print(f"  - {item}")
