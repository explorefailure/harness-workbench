"""Inject a fault into one feature and measure what survived.

Family 2. The steady-state hypothesis, in the chaos-engineering sense: run
the spec clean, then run it again with exactly one feature broken, and look
for a difference in the measurable output. The record IS the measurable
output, which is what makes the protocol fit -- *"focus on the measurable
output of a system, rather than internal attributes of the system."*

One principle from that literature does NOT transfer. Blast-radius
MINIMISATION exists because chaos runs against production; this has no
production and no users, so the goal here is maximum exploration.

Four survival bits per injection, chosen because they are what Invariant 2
actually promises:

    completed        the run finished rather than dying
    conforms         the record still satisfies the invariants
    others_intact    no OTHER feature's namespace moved
    steps_retained   the step results survived

The taxonomy predicts the answers -- observe/annotate should disable the
feature and continue; wrap should fail the step and continue the run -- so
this is a check on whether the implementation matches the design. Say so
plainly rather than presenting a confirmed prediction as a discovery.

WHY THE FAULT LIBRARY GOES BEYOND `raise`. The veto research measured it:
catch-and-continue failed open on 6 of 7 adversarial behaviours, and the
killer case was a hook that returned None -- it never raises, so there is
nothing to catch. A library that only raises will report excellent
containment and be wrong. Injected faults are also known to be
unrepresentative of real residual defects (a large fault-injection study put
it as high as 72%), so these are deliberately shaped like plausible bugs
rather than like sabotage.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .canon import canon_bytes
from .diff import identity_of, strip_identity
from .runner import _stamp

# name -> (applies to powers, hook body). The body replaces the feature's
# real hook; `%(seam)s` is filled in with the seam name.
FAULTS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    # Loud. The only one most fault libraries bother with.
    "raise": (("observe", "annotate", "wrap"),
              "def %(seam)s(*a):\n    raise RuntimeError('injected fault')\n"),
    # Quiet. Returns nothing where data was expected: the run completes, the
    # feature is never marked failed, and its contribution silently vanishes.
    # This is the shape that defeats catch-and-continue.
    "silent": (("annotate",),
               "def %(seam)s(*a):\n    return None\n"),
    # Contract violation the core should name rather than absorb.
    "wrong_type": (("annotate",),
                   "def %(seam)s(*a):\n    return 'not a dict'\n"),
    # Coupling. Reaches through the record into another namespace -- zero
    # imports, invisible to static analysis.
    "meddle": (("observe", "annotate"),
               "def %(seam)s(*a):\n"
               "    ctx = a[-1]\n"
               "    for k, v in (ctx.get('extras') or {}).items():\n"
               "        if k != ctx.get('feature') and isinstance(v, dict):\n"
               "            v['injected'] = True\n"
               "    return {'meddled': True}\n"),
    # Time. Only measurable at all because seams became boundable -- before
    # that a hang left a husk with no record to compare.
    "hang": (("observe", "annotate"),
             "import time\n\ndef %(seam)s(*a):\n    time.sleep(30)\n"),
    # Wrap-specific: accepts control and never runs the step.
    "noop": (("wrap",),
             "def %(seam)s(step, run_step, ctx):\n    return None\n"),
}


class BlastError(Exception):
    """The campaign could not be set up."""


def applicable(power: str) -> List[str]:
    return sorted(n for n, (powers, _) in FAULTS.items() if power in powers)


def _mutant(src_root: str, dst_root: str, seam: str, fault: str) -> None:
    """A copy of the feature with one hook replaced.

    Mutating a COPY rather than patching the dispatcher keeps the injector
    out of the base: the harness under test is the real one, and the feature
    digest legitimately changes because it is genuinely different code.
    """
    shutil.copytree(src_root, dst_root,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    body = FAULTS[fault][1] % {"seam": seam}
    with open(os.path.join(dst_root, "feature.py"), "w", encoding="utf-8") as fh:
        fh.write(body)


def _survival(baseline: Dict[str, Any], injured: Dict[str, Any],
              target: str, runs_root: str) -> Dict[str, Any]:
    from .conform import NonConforming, validate_record
    from .diff import load_run

    bits: Dict[str, Any] = {}
    bits["completed"] = injured["status"] == "completed"

    try:
        rec, ats, _ = load_run(runs_root, injured["run_id"])
        validate_record(rec, ats, run_dir=os.path.join(runs_root, injured["run_id"]))
        bits["conforms"] = True
    except (NonConforming, Exception):        # noqa: BLE001
        bits["conforms"] = False

    # Every namespace EXCEPT the injured feature's own must be untouched.
    # Its own is expected to change -- that is the injection, not the blast.
    #
    # Masked first, and this is not optional: without it all 15 injections
    # reported "disturbed another feature" when nothing had been. `freeze`
    # keys baseline_file to the spec stem and `receipt` embeds run_id and
    # spec_digest, and a campaign necessarily runs each injection under its
    # own spec. Identity differing is the instrument moving, not the system.
    # A CONSUMER of the injured feature is expected to change. `receipt`
    # requires content-digest from `freeze`, so a broken freeze leaves it
    # nothing to bind -- that is the declared edge working, not blast
    # damage, and counting it as damage made five correct results look like
    # violations. Absent `requires` (older records) means unknown, so the
    # feature is still compared rather than silently excused.
    provided = set()
    consumers = set()
    for f in injured.get("features", []):
        if f["name"] == target:
            provided |= set(f.get("provides") or [])
    for f in injured.get("features", []):
        if f["name"] != target and set(f.get("requires") or []) & provided:
            consumers.add(f["name"])

    b_ids, i_ids = identity_of(baseline), identity_of(injured)
    moved = []
    for name, blob in (baseline.get("extras") or {}).items():
        if name == target or name in consumers:
            continue
        want = strip_identity(blob, b_ids)
        got = strip_identity((injured.get("extras") or {}).get(name), i_ids)
        if want != got:
            moved.append(name)
    bits["others_intact"] = not moved
    bits["others_moved"] = moved
    bits["consumers_excused"] = sorted(consumers)

    b_at = _attempts(runs_root, baseline["run_id"])
    i_at = _attempts(runs_root, injured["run_id"])
    bits["steps_retained"] = (
        [(a["step_id"], a["exit"]) for a in b_at]
        == [(a["step_id"], a["exit"]) for a in i_at])

    feat = [f for f in injured["features"] if f["name"] == target]
    bits["feature_status"] = feat[0]["status"] if feat else "absent"
    return bits


def _attempts(runs_root: str, run_id: str) -> List[Dict[str, Any]]:
    p = os.path.join(runs_root, run_id, "attempts.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, "r", encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def campaign(spec_path: str, runs_root: str, blasts_root: str,
             seam_timeout_ms: int = 400) -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod

    base = specmod.load(spec_path)
    if not base.features:
        raise BlastError("spec declares no features; nothing to injure")

    campaign_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(blasts_root, campaign_id)
    os.makedirs(cdir)
    feat_root = featmod.features_root(base.dir, base.features_root)

    # The steady state: same spec, same features, no injection. Every result
    # below is a deviation from this, so it must be established first -- and
    # it must be established UNDER THE SAME CONDITIONS as the injections, or
    # the comparison measures the setup instead of the fault.
    #
    # Concretely: `freeze` keys its baseline to the spec, so a campaign whose
    # control reused an existing lock reported "compared" while every
    # injection reported "created". That difference is the harness varying,
    # not the feature being disturbed, and it made all 15 injections look
    # like blast damage. The control therefore runs under its own derived
    # spec exactly as the injections do.
    baseline = _clean_run(base, feat_root, cdir, runs_root, seam_timeout_ms)

    manifests = {}
    for ref in base.features:
        manifests[ref.name] = featmod.read_manifest(
            os.path.join(feat_root, ref.name))

    rows: List[Dict[str, Any]] = []
    for ref in base.features:
        m = manifests[ref.name]
        for seam in m.seams:
            for fault in applicable(m.power):
                row = _one(base, ref.name, m, seam, fault, feat_root, cdir,
                           runs_root, baseline, seam_timeout_ms)
                rows.append(row)

    manifest = {
        "schema": "hwbblast/v0.1",
        "campaign_id": campaign_id,
        "base_spec": os.path.abspath(spec_path),
        "baseline_run": baseline["run_id"],
        "seam_timeout_ms": seam_timeout_ms,
        "injections": rows,
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest


def _derived_spec(base, cdir: str, tag: str, seam_timeout_ms: int,
                  prefix: str = "blast") -> str:
    """A spec identical to the base but uniquely named.

    Written beside the base spec, because steps run with `cwd=spec.dir` and
    inputs resolve against it. Named so that any feature keying state to the
    spec stem gets a fresh slate -- identical treatment for the control and
    every injection.

    `prefix` exists so a second campaign type gets its own scratch namespace
    rather than colliding with blast's. Each prefix needs its own
    `.hwb<prefix>-*` line in .gitignore: a concurrent `git add -A` sweeping
    one of these mid-run is how a scratch spec got committed once already.
    """
    body = dict(base.raw)
    body["seam_timeout_ms"] = seam_timeout_ms
    path = os.path.join(base.dir, ".hwb%s-%s.json" % (prefix, tag))
    with open(path, "wb") as fh:
        fh.write(canon_bytes(body))
    return path


def _cleanup(path: str) -> None:
    for p in (path, os.path.splitext(path)[0] + ".freeze.lock"):
        if os.path.isfile(p):
            os.remove(p)


def _clean_run(base, feat_root: str, cdir: str, runs_root: str,
               seam_timeout_ms: int, prefix: str = "blast") -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod

    path = _derived_spec(base, cdir, "control", seam_timeout_ms, prefix)
    prev = os.environ.get("HWB_FEATURES")
    os.environ["HWB_FEATURES"] = feat_root
    try:
        sp = specmod.load(path)
        return runner.execute(sp, featmod.resolve(sp), runs_root)
    finally:
        _cleanup(path)
        if prev is None:
            os.environ.pop("HWB_FEATURES", None)
        else:
            os.environ["HWB_FEATURES"] = prev


def _one(base, target: str, manifest, seam: str, fault: str, feat_root: str,
         cdir: str, runs_root: str, baseline: Dict[str, Any],
         seam_timeout_ms: int) -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod

    row: Dict[str, Any] = {"feature": target, "seam": seam, "fault": fault,
                           "power": manifest.power}

    stage = os.path.join(cdir, "features-%s-%s-%s" % (target, seam, fault))
    os.makedirs(stage)
    for name in os.listdir(feat_root):
        src = os.path.join(feat_root, name)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(stage, name)
        if name == target:
            _mutant(src, dst, seam, fault)
        else:
            shutil.copytree(src, dst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # A bound is required for `hang` to be measurable at all: unbounded, the
    # run leaves a husk with no record and there is nothing to compare.
    spath = _derived_spec(base, cdir, "%s-%s-%s" % (target, seam, fault),
                          seam_timeout_ms)

    prev = os.environ.get("HWB_FEATURES")
    os.environ["HWB_FEATURES"] = stage
    try:
        sp = specmod.load(spath)
        loaded = featmod.resolve(sp)
        injured = runner.execute(sp, loaded, runs_root)
        row["run_id"] = injured["run_id"]
        row.update(_survival(baseline, injured, target, runs_root))
    except (specmod.SpecError, featmod.FeatureError) as e:
        row["skipped"] = str(e)
    except runner.HarnessError as e:
        # The run itself died. That IS the blast radius: total.
        row["completed"] = False
        row["conforms"] = False
        row["others_intact"] = False
        row["steps_retained"] = False
        row["error"] = str(e)
    finally:
        _cleanup(spath)
        if prev is None:
            os.environ.pop("HWB_FEATURES", None)
        else:
            os.environ["HWB_FEATURES"] = prev
    return row


def summarise(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Where did the implementation diverge from what the taxonomy promises?

    observe/annotate: record it, disable the feature, continue the run.
    wrap:             fail the STEP, continue the run.

    Either way the run completes with a valid record and no other feature is
    disturbed -- so a violation of THAT is the finding, not the feature's own
    breakage, which is the injection working.
    """
    findings = []
    for r in manifest["injections"]:
        if r.get("skipped"):
            continue
        broken = [k for k in ("completed", "conforms", "others_intact")
                  if not r.get(k)]
        # A wrap fault legitimately changes step results; observe/annotate
        # must not, since neither can touch execution.
        if r["power"] != "wrap" and not r.get("steps_retained"):
            broken.append("steps_retained")
        if broken:
            findings.append({**r, "violated": broken})
    return {"total": len([r for r in manifest["injections"]
                          if not r.get("skipped")]),
            "findings": findings}
