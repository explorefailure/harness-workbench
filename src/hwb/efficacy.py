"""Invert a feature's decision and require the run to come out different.

Family 7. The five containment families ask whether a feature MISBEHAVES.
This one asks whether it DOES ANYTHING -- and it is the only family that can
tell a working gate from a gate wired to permit everything, because those
two produce identical output under every other check here.

    blast     injects a FAULT and asserts the run SURVIVED it
    efficacy  injects a well-formed OPPOSITE and asserts the run DIFFERED

Same mutant generator, opposite assertion. A mutant that survives is not a
feature that passed; it is a feature nothing downstream consults.

PRIOR ART, deliberately borrowed rather than invented. NIST SP 800-192's
rule coverage checking mutates a rule's permission to its negation and reads
"the safety requirements are still satisfied" as proof the requirements do
not cover that rule. Martin & Xie's Change-Rule-Effect operator does the
same to a policy's Permit/Deny, and is valuable because it "should never
create equivalent mutants unless a rule is unreachable" -- so a surviving
mutant is always a finding. The kill condition in both is a pure
differential between two configurations, which is why this needs no scorer
and no labels, exactly like the other families.

WHY THE BASELINE IS WARMED FIRST. A feature with persistent state has two
code paths: the one that initialises the state and the one that decides
against it. Give every run a fresh scratch spec -- which is what the blast
campaign correctly does, to stop one run's leftovers reaching another -- and
a stateful feature takes the INITIALISING path every time, so its decision
is never reached and the campaign measures a bootstrap. This campaign
therefore shares one derived spec across every run and burns a warm-up run
to establish the state, so the baseline and the mutants all reach the
deciding branch.

That fix introduces its own risk, so it is checked rather than trusted: the
baseline is run TWICE and the two must agree before any mutant is
interpreted. Two runs of one configuration that differ mean the
configuration is not stable, and a "kill" measured against an unstable
baseline is the stateful-baseline confound wearing a result's clothes. When
that check fails the campaign refuses to report kills at all -- a refusal is
worth more than a table of numbers nobody should act on.
"""
from __future__ import annotations

import os
import shutil
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .blast import _cleanup, _derived_spec
from .canon import canon_bytes
from .runner import _stamp

KILLED = "killed"
SURVIVED = "SURVIVED"
MALFORMED = "malformed"
SKIPPED = "skipped"


class EfficacyError(Exception):
    """The campaign could not be set up."""


class UnstableBaseline(EfficacyError):
    """Two runs of the control disagreed. Nothing here can be measured."""


def _mutant_tree(feat_root: str, target: str, dst: str,
                 inverts: Dict[str, str]) -> None:
    """Every feature copied; the target's hook replaced by its inversion.

    The inversion source is the feature author's own file, not something
    generated here. The base cannot know what the opposite of an arbitrary
    feature means, and a generated guess would make the family measure the
    guess rather than the feature.
    """
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(feat_root):
        src = os.path.join(feat_root, name)
        if not os.path.isdir(src):
            continue
        shutil.copytree(src, os.path.join(dst, name),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    tgt = os.path.join(dst, target)
    with open(os.path.join(tgt, inverts["source"]), "r", encoding="utf-8") as fh:
        body = fh.read()
    with open(os.path.join(tgt, "feature.py"), "w", encoding="utf-8") as fh:
        fh.write(body)


def _run(spath: str, feat_root: str, runs_root: str) -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod

    prev = os.environ.get("HWB_FEATURES")
    os.environ["HWB_FEATURES"] = feat_root
    try:
        sp = specmod.load(spath)
        return runner.execute(sp, featmod.resolve(sp), runs_root)
    finally:
        if prev is None:
            os.environ.pop("HWB_FEATURES", None)
        else:
            os.environ["HWB_FEATURES"] = prev


def _differs(runs_root: str, a_id: str, b_id: str,
             include_feature_digests: bool = False) -> Tuple[bool, str]:
    """Did the run come out different? A refusal counts as a difference.

    `Incomparable` is the strongest available signal that the verdict moved:
    inverting a drift detector makes the pair refuse to compare at all, which
    is a bigger observable change than any field-level delta.
    """
    from . import steady

    # The A/A control enables feature digests and therefore shares steady's
    # exact baseline relation. Mutant comparisons leave them disabled because
    # efficacy deliberately changes one feature's source tree -- that digest
    # is the manipulation itself, not a kill.
    pair = steady.compare_pair(
        runs_root, a_id, b_id,
        include_feature_digests=include_feature_digests)
    if pair["verdict"] == steady.UNINTERPRETABLE:
        return True, "comparison refused: %s" % pair["detail"]
    if pair["verdict"] == steady.STABLE:
        return False, "equivalent under the mask"
    # BOTH halves of the comparison, and the second was missing. `differences`
    # holds harness fields only; step output content is reported separately as
    # `output_differences`. A feature whose decision shows up ONLY in the
    # captured bytes -- which is the entire point of an output-mutating
    # feature -- was therefore killed with an empty reason, and a kill nobody
    # can read is one step from a kill nobody believes.
    why = list(pair["harness_differences"])
    if pair["output_differences"]:
        why.append("%d step output(s) differ: %s"
                   % (len(pair["output_differences"]),
                      pair["output_differences"][0]))
    return True, "; ".join(why)[:160]


def _wellformed(record: Dict[str, Any]) -> Tuple[bool, str]:
    """Was the mutant a semantic opposite, or just breakage?

    A mutant that crashed or disabled its own feature is a FAULT, and faults
    are Family 2's experiment. Reporting one as `killed` would be the worst
    outcome available: a broken inversion masquerading as proof the feature
    is load-bearing.

    CONFORMANCE IS DELIBERATELY NOT CHECKED HERE, and an earlier version of
    this function got that wrong. It treated a non-conforming record as
    malformed -- but `conform` is DOWNSTREAM, so a record that stops
    conforming when a feature's decision is inverted is the strongest kill
    available: an invariant noticed. Counting it as malformed would have
    reported the best possible result as an unusable one, and would have hit
    exactly the features whose decisions are checked hardest. `receipt`,
    whose digest binding `conform._self_attestation` verifies, is the case
    that surfaced it.
    """
    if record["status"] != "completed":
        return False, "inverted run did not complete (%s)" % record["status"]
    feats = {f["name"]: f["status"] for f in record.get("features", [])}
    broken = sorted(n for n, s in feats.items() if s != "ok")
    if broken:
        return False, "inverted run disabled feature(s): %s" % ", ".join(broken)
    return True, ""


def _conforms(runs_root: str, run_id: str) -> Tuple[bool, str]:
    """Does the inverted run still satisfy the invariants?

    A `no` here is a kill by the strictest checker in the system.
    """
    from . import conform, diff as diffmod

    try:
        rec, ats, _ = diffmod.load_run(runs_root, run_id)
        conform.validate_record(rec, ats,
                                run_dir=os.path.join(runs_root, run_id))
    except Exception as e:                                   # noqa: BLE001
        return False, str(e)
    return True, ""


def campaign(spec_path: str, runs_root: str, eff_root: str,
             seam_timeout_ms: int = 400) -> Dict[str, Any]:
    from . import features as featmod, spec as specmod, stores

    try:
        stores.require_disjoint(runs_root, eff_root,
                                "efficacy-campaign store")
    except stores.StoreOverlapError as e:
        raise EfficacyError(str(e))

    base = specmod.load(spec_path)
    if not base.features:
        raise EfficacyError("spec declares no features; nothing to invert")

    campaign_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(eff_root, campaign_id)
    os.makedirs(cdir)
    feat_root = featmod.features_root(base.dir, base.features_root)

    manifests = {}
    for ref in base.features:
        manifests[ref.name] = featmod.read_manifest(
            os.path.join(feat_root, ref.name))

    # ONE spec, shared by every run in the campaign, so stateful features
    # reach their deciding branch. Cleaned up once at the end rather than per
    # run -- removing it between runs would reset exactly the state this
    # protocol exists to establish.
    spath = _derived_spec(base, cdir, "shared", seam_timeout_ms, prefix="efficacy")

    rows: List[Dict[str, Any]] = []
    stability: Dict[str, Any] = {}
    try:
        _run(spath, feat_root, runs_root)                    # warm-up
        base_a = _run(spath, feat_root, runs_root)
        base_b = _run(spath, feat_root, runs_root)

        moved, why = _differs(runs_root, base_a["run_id"], base_b["run_id"],
                              include_feature_digests=True)
        stability = {"run_a": base_a["run_id"], "run_b": base_b["run_id"],
                     "stable": not moved, "detail": why}
        if moved:
            raise UnstableBaseline(
                "two runs of the control differ (%s) -- a kill measured "
                "against this baseline would be the stateful-baseline "
                "confound, not a result" % why)

        for ref in base.features:
            m = manifests[ref.name]
            row: Dict[str, Any] = {"feature": ref.name, "power": m.power,
                                   "intent": m.intent}
            if not m.inverts:
                # Two different facts that used to print identically. An
                # instrument feature has no obligation to be load-bearing --
                # `timing` exists to prove seam dispatch and inertness is its
                # design. Anything else with no inversion is a gap in the
                # measurement wearing the same words.
                row.update({
                    "verdict": SKIPPED,
                    "detail": ("instrument -- inertness is the design"
                               if m.intent == "instrument"
                               else "declares no inversion")})
                rows.append(row)
                continue
            row["seam"] = m.inverts["seam"]
            row["decision"] = m.inverts["decision"]

            stage = os.path.join(cdir, "features-%s-inverted" % ref.name)
            _mutant_tree(feat_root, ref.name, stage, m.inverts)
            mutant = _run(spath, feat_root=stage, runs_root=runs_root)
            row["run_id"] = mutant["run_id"]

            ok, why_bad = _wellformed(mutant)
            if not ok:
                row.update({"verdict": MALFORMED, "detail": why_bad})
                rows.append(row)
                continue

            # Two independent ways for the inversion to be noticed, reported
            # separately because they mean different things: `conform` says an
            # INVARIANT caught it, `diff` says the run merely came out
            # different. A feature killed only by diff is load-bearing; one
            # killed by conform is load-bearing AND guarded.
            conforms, why_not = _conforms(runs_root, mutant["run_id"])
            moved, why = _differs(runs_root, base_b["run_id"], mutant["run_id"])
            if not conforms:
                row.update({"verdict": KILLED, "killed_by": "conform",
                            "detail": "an invariant rejected it: %s" % why_not[:120]})
            elif moved:
                row.update({"verdict": KILLED, "killed_by": "diff",
                            "detail": why})
            else:
                row.update({"verdict": SURVIVED, "killed_by": None,
                            "detail": why})
            rows.append(row)
    finally:
        _cleanup(spath)

    manifest = {
        "schema": "hwbefficacy/v0.1",
        "campaign_id": campaign_id,
        "base_spec": os.path.abspath(spec_path),
        "stability": stability,
        "mutants": rows,
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest


def summarise(manifest: Dict[str, Any]) -> Dict[str, Any]:
    rows = manifest["mutants"]
    tested = [r for r in rows if r["verdict"] in (KILLED, SURVIVED)]
    return {
        "killed": [r for r in tested if r["verdict"] == KILLED],
        # The finding. A feature whose opposite changes nothing is inert in
        # this configuration -- which is a claim about the configuration as
        # much as about the feature, and the wording says so.
        "inert": [r for r in tested if r["verdict"] == SURVIVED],
        "malformed": [r for r in rows if r["verdict"] == MALFORMED],
        # Split, because only one of these is a hole. `by_design` is a
        # feature that says it exercises the harness rather than serving the
        # run; `undeclared` is one that claims to do work and cannot say
        # what would change if it decided otherwise.
        "by_design": [r for r in rows if r["verdict"] == SKIPPED
                      and r.get("intent") == "instrument"],
        "undeclared": [r for r in rows if r["verdict"] == SKIPPED
                       and r.get("intent") != "instrument"],
        "tested": len(tested),
        "total": len(rows),
    }
