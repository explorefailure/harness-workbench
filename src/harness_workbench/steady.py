"""Can an unchanged configuration supply a trustworthy differential baseline?

Family 12. Every differential campaign assumes that rerunning the control
does not move the thing it later attributes to a manipulation. `efficacy`
already checked that relation locally; this module makes it a standalone
preflight and gives the relation one implementation.

Three runs by default, all preserved as ordinary run evidence. The first is
the baseline and every later run is compared against it on BOTH axes exposed
by `diff`: harness structure and stored step output. There is no averaging.
One unallowed moving axis makes the campaign unstable; one comparison refusal
or unavailable output makes the whole result uninterpretable.
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, List, Optional

from .canon import canon_bytes
from .runner import _stamp

STABLE = "stable"
UNSTABLE = "UNSTABLE"
UNINTERPRETABLE = "uninterpretable"
SETUP_ERROR = "setup_error"
DEFAULT_REPEATS = 3


class SteadyError(Exception):
    """The campaign could not be set up, so no stability verdict exists."""


def _harness_axes(lines: List[str]) -> List[str]:
    axes: List[str] = []
    for line in lines:
        if line.startswith("spec:"):
            axes.append("harness:spec")
        elif line.startswith("features: only"):
            axes.append("harness:features")
        elif line.startswith("features: not ok"):
            axes.append("harness:feature_status")
        elif line.startswith("features_source:"):
            axes.append("harness:features_source")
        elif line.startswith("features[") and "].digest:" in line:
            name = line.split("[", 1)[1].split("]", 1)[0]
            axes.append("harness:features[%s].digest" % name)
        elif line.startswith("status:"):
            axes.append("harness:status")
        elif line.startswith("env["):
            axes.append("harness:" + line.split(":", 1)[0])
        elif line.startswith("step "):
            match = re.match(r"step ([^:]+): (.*)", line)
            if not match:
                axes.append("harness:" + line.split(":", 1)[0])
                continue
            step, detail = match.groups()
            if detail.startswith("only in"):
                leaf = "presence"
            elif "attempt(s)" in detail:
                leaf = "attempt_count"
            elif detail.startswith("exits"):
                leaf = "exits"
            elif detail.startswith("attempt CAUSE"):
                leaf = "causes"
            else:
                leaf = "other"
            axes.append("harness:steps[%s].%s" % (step, leaf))
        elif line.startswith("extras["):
            name = line.split("[", 1)[1].split("]", 1)[0]
            fields = (line.split("differs at ", 1)[1]
                      if "differs at " in line else "(value)")
            for field in fields.split(", "):
                axes.append("harness:extras[%s].%s" % (name, field))
        else:
            axes.append("harness:" + line.split(":", 1)[0])
    return sorted(set(axes))


def _output_axes(lines: List[str]) -> List[str]:
    axes = []
    for line in lines:
        if line.startswith("output digests unavailable"):
            axes.append("output:availability")
        else:
            axes.append("output:" + line.split(":", 1)[0])
    return sorted(set(axes))


def _feature_digest_differences(a: Dict[str, Any],
                                b: Dict[str, Any]) -> List[str]:
    da = {f["name"]: f.get("digest") for f in a.get("features", [])}
    db = {f["name"]: f.get("digest") for f in b.get("features", [])}
    return ["features[%s].digest: %s vs %s" % (name, da.get(name), db.get(name))
            for name in sorted(set(da) & set(db)) if da[name] != db[name]]


def compare_pair(runs_root: str, run_a: str, run_b: str,
                 allowance: Optional[List[str]] = None,
                 include_feature_digests: bool = True) -> Dict[str, Any]:
    """Compare one A/A pair on the exact axes downstream differentials use."""
    from . import diff as diffmod

    allowed = sorted(set(allowance or []))
    row: Dict[str, Any] = {"run_a": run_a, "run_b": run_b,
                           "allowance": allowed}
    try:
        loaded_a = diffmod.load_run(runs_root, run_a)
        loaded_b = diffmod.load_run(runs_root, run_b)
        result = diffmod.compare(loaded_a, loaded_b)
    except diffmod.Incomparable as e:
        row.update({"verdict": UNINTERPRETABLE, "detail": str(e),
                    "harness_differences": [], "output_differences": [],
                    "moving_axes": [], "unallowed_axes": []})
        return row

    harness = list(result["differences"])
    if loaded_a[0].get("features_source") != loaded_b[0].get("features_source"):
        harness.append("features_source: %s vs %s" % (
            loaded_a[0].get("features_source"),
            loaded_b[0].get("features_source")))
    if include_feature_digests:
        harness.extend(_feature_digest_differences(loaded_a[0], loaded_b[0]))
    output = list(result["output_differences"])
    axes = sorted(set(_harness_axes(harness) + _output_axes(output)))
    unallowed = sorted(axis for axis in axes if axis not in allowed)

    if not result["output_known"]:
        verdict = UNINTERPRETABLE
        detail = "stored output was unavailable, so equality cannot be judged"
    elif unallowed:
        verdict = UNSTABLE
        detail = "%d unallowed moving axis/axes" % len(unallowed)
    else:
        verdict = STABLE
        detail = ("no axes moved" if not axes
                  else "%d moving axis/axes covered by the declared allowance"
                  % len(axes))
    row.update({"verdict": verdict, "detail": detail,
                "harness_differences": harness,
                "output_differences": output,
                "moving_axes": axes, "unallowed_axes": unallowed})
    return row


def classify(comparisons: List[Dict[str, Any]],
             setup_error: Optional[str] = None) -> str:
    """Fail closed, never average pair verdicts into a rate."""
    if setup_error:
        return SETUP_ERROR
    if not comparisons:
        return UNINTERPRETABLE
    if any(row["verdict"] == UNINTERPRETABLE for row in comparisons):
        return UNINTERPRETABLE
    if any(row["verdict"] == UNSTABLE for row in comparisons):
        return UNSTABLE
    return STABLE


def campaign(spec_path: str, runs_root: str, steadies_root: str,
             repeats: int = DEFAULT_REPEATS,
             allowance: Optional[List[str]] = None) -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod, stores

    if repeats < 2:
        raise SteadyError("repeats must be at least 2, got %d" % repeats)
    try:
        stores.require_disjoint(runs_root, steadies_root,
                                "steady-campaign store")
    except stores.StoreOverlapError as e:
        raise SteadyError(str(e))
    try:
        base = specmod.load(spec_path)
    except specmod.SpecError as e:
        raise SteadyError(str(e))

    campaign_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(steadies_root, campaign_id)
    os.makedirs(cdir)
    run_ids: List[str] = []
    setup_error: Optional[str] = None

    for i in range(repeats):
        try:
            # Reload and resolve every time. Reusing Loaded objects would let
            # one run's feature failure state leak into the next control.
            current = specmod.load(spec_path)
            record = runner.execute(current, featmod.resolve(current), runs_root)
            run_ids.append(record["run_id"])
        except (specmod.SpecError, featmod.FeatureError,
                runner.HarnessError) as e:
            setup_error = "repeat %d could not execute: %s" % (i + 1, e)
            break

    comparisons = []
    if not setup_error and len(run_ids) == repeats:
        comparisons = [compare_pair(runs_root, run_ids[0], other, allowance)
                       for other in run_ids[1:]]

    verdict = classify(comparisons, setup_error)
    manifest = {
        "schema": "hwbsteady/v0.1",
        "campaign_id": campaign_id,
        "base_spec": os.path.abspath(spec_path),
        "base_spec_digest": base.digest,
        "features_root": featmod.features_root(
            base.dir, getattr(base, "features_root", None)),
        "repeats_requested": repeats,
        "run_ids": run_ids,
        "allowance": sorted(set(allowance or [])),
        "comparisons": comparisons,
        "verdict": verdict,
        "setup_error": setup_error,
        "moving_axes": sorted({axis for row in comparisons
                                for axis in row["moving_axes"]}),
        "unallowed_axes": sorted({axis for row in comparisons
                                   for axis in row["unallowed_axes"]}),
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest


def summarise(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Small public reducer, also probed by sensitivity."""
    return {
        "verdict": manifest["verdict"],
        "runs": len(manifest.get("run_ids") or []),
        "comparisons": len(manifest.get("comparisons") or []),
        "moving_axes": list(manifest.get("moving_axes") or []),
        "unallowed_axes": list(manifest.get("unallowed_axes") or []),
        "setup_error": manifest.get("setup_error"),
    }
