"""Run one spec under many feature configurations, then relate the records.

This is the piece that turns the workbench from a recorder into an
instrument. `diff` can compare two runs; nothing generated the runs worth
comparing, which is how eleven runs came to be recorded and none examined.

Two halves, deliberately separate:

  sweep        execute a spec under N feature configurations
  interference assert a metamorphic relation across those records

The relation is MR-1: **extras[A] is invariant under attaching any B that A
does not require.** Features write only into their own namespace and talk
solely through the record, so attaching an unrelated feature must not move
A's data. That is the architecture's central claim and the one the plan
admits it has no mechanical control for -- import analysis cannot see
coupling that travels through the record.

No scorer, no oracle: the check is a relation between runs, never a judgment
of either one's output.
"""
from __future__ import annotations

import itertools
import json
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .canon import canon_bytes
from .diff import identity_of, strip_identity
from .runner import _stamp

MODES = ("singletons", "pairs", "powerset", "permutations")

# Family 11. Feature ORDER is an experimental variable distinct from feature
# SET, and the other modes vary only the set. `wrap_chain` composes in
# declared order -- "[sample, retry] means retry(sample(step))" -- and
# `caused_by` exists precisely to tell those apart, so the ordering question
# was recordable long before anything varied it.
#
# n! is why this is capped rather than merely discouraged: 5 features is 120
# runs and 6 is 720. The cap is low on purpose, because order can only matter
# among features that actually share a seam, and that set is small.
PERMUTATION_CAP = 4

# The exhaustive mode is capped, not because 2^N is wrong but because it is
# the wrong SHAPE of cost. NIST's combinatorial data: across studied domains
# every failure was triggered by at most 4-to-6-way interaction, and no fault
# in thousands of reports involved more than six variables. `pairs` catches
# the large majority at a fraction of the runs, so an unbounded powerset is a
# trap rather than a thoroughness.
POWERSET_CAP = 6


class SweepError(Exception):
    """The sweep could not be set up. Distinct from a configuration failing."""


def configurations(names: List[str], mode: str) -> List[Tuple[str, ...]]:
    """The feature subsets to execute, always including the empty set.

    Zero features is the control: the base must stay correct without any
    feature, permanently, and every relation is measured against it.
    """
    if mode not in MODES:
        raise SweepError("mode must be one of %s, got %r" % (MODES, mode))
    if mode == "powerset" and len(names) > POWERSET_CAP:
        raise SweepError(
            "powerset over %d features is %d configurations; use 'pairs' "
            "(pairwise coverage catches the large majority of interaction "
            "faults at a fraction of the cost)" % (len(names), 2 ** len(names)))

    if mode == "permutations":
        if len(names) > PERMUTATION_CAP:
            raise SweepError(
                "permutations over %d features is %d configurations; order "
                "can only matter among features sharing a seam, so narrow the "
                "spec to those" % (len(names), _factorial(len(names))))
        # The DECLARED order first: it is the baseline every other ordering
        # is measured against, and it must be the same run, not a re-derived
        # equivalent.
        perms = [tuple(names)]
        perms.extend(p for p in itertools.permutations(names)
                     if p != tuple(names))
        return perms

    out: List[Tuple[str, ...]] = [()]
    if mode == "powerset":
        for r in range(1, len(names) + 1):
            out.extend(itertools.combinations(names, r))
        return out

    out.extend((n,) for n in names)                       # singletons
    if mode == "pairs":
        out.extend(itertools.combinations(names, 2))
    return out


def _exits(runs_root: str, run_id: str) -> List[Any]:
    p = os.path.join(runs_root, run_id, "attempts.jsonl")
    if not os.path.isfile(p):
        return []
    with open(p, "r", encoding="utf-8") as fh:
        return [json.loads(l)["exit"] for l in fh if l.strip()]


def _factorial(n: int) -> int:
    out = 1
    for i in range(2, n + 1):
        out *= i
    return out


def _derive(raw: Dict[str, Any], keep: Tuple[str, ...]) -> Dict[str, Any]:
    """The base spec with its feature list narrowed, everything else intact.

    Per-feature `config` and `action` are carried through: a sweep varies
    WHICH features attach, never how they are configured, or the condition
    would move in two dimensions at once.

    Features come out in `keep`'s ORDER, not the spec's. For the subset modes
    that is a no-op -- `itertools.combinations` emits in input order, which is
    the spec's -- but it is what lets the permutations mode vary order at all.
    Declared order is load-bearing: `wrap_chain` composes in it.
    """
    body = dict(raw)
    by_name = {f.get("name"): f for f in raw.get("features", [])}
    body["features"] = [by_name[n] for n in keep if n in by_name]
    return body


def run_sweep(spec_path: str, runs_root: str, sweeps_root: str,
              mode: str = "pairs") -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod, stores

    try:
        stores.require_disjoint(runs_root, sweeps_root, "sweep store")
    except stores.StoreOverlapError as e:
        raise SweepError(str(e))

    base = specmod.load(spec_path)
    names = [f.name for f in base.features]
    if not names:
        raise SweepError("spec declares no features; a sweep needs something "
                         "to vary")

    configs = configurations(names, mode)
    sweep_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    sweep_dir = os.path.join(sweeps_root, sweep_id)
    os.makedirs(sweep_dir)

    # Derived specs MUST live beside the base spec. Steps run with
    # `cwd=spec.dir` and `steps[].inputs` resolve against it, so writing them
    # into the sweep directory broke every relative path -- and broke it
    # SILENTLY: the runs still "completed", the sweep still reported them as
    # ran, and the interference analysis produced confident findings over
    # eight runs in which nothing had executed. Found by checking the exit
    # codes rather than the summary line.
    spec_dir = base.dir

    # Features resolve from $HWB_FEATURES, else <spec dir>/features. Pinned
    # explicitly for the sweep and restored afterwards, so the resolved root
    # is identical for every configuration and is recorded in the manifest --
    # a sweep whose feature root moved between runs would vary two things.
    root = featmod.features_root(base.dir, base.features_root)
    prev = os.environ.get("HWB_FEATURES")
    os.environ["HWB_FEATURES"] = root

    rows: List[Dict[str, Any]] = []
    written: List[str] = []
    try:
        for i, keep in enumerate(configs):
            name = ".hwbsweep-%s-c%02d" % (sweep_id, i)
            path = os.path.join(spec_dir, name + ".json")
            with open(path, "wb") as fh:
                fh.write(canon_bytes(_derive(base.raw, keep)))
            written.append(path)

            row: Dict[str, Any] = {"config": list(keep), "spec": name + ".json"}
            try:
                sp = specmod.load(path)
                loaded = featmod.resolve(sp)
            except (specmod.SpecError, featmod.FeatureError) as e:
                # Not a failure of the sweep. A subset that breaks a declared
                # capability edge is UNRUNNABLE BY DESIGN -- `receipt` without
                # `freeze` has no content-digest -- and recording why is the
                # point: it is the load-time refusal doing its job.
                row["skipped"] = str(e)
                rows.append(row)
                continue
            try:
                rec = runner.execute(sp, loaded, runs_root)
            except runner.HarnessError as e:
                row["error"] = str(e)
                rows.append(row)
                continue
            row["run_id"] = rec["run_id"]
            # A configuration whose steps never executed is not evidence.
            # Recorded per row so the summary cannot claim a run that did
            # nothing, and so the relation can refuse to reason over it.
            exits = _exits(runs_root, rec["run_id"])
            row["attempts"] = len(exits)
            row["executed"] = bool(exits) and all(e is not None for e in exits)
            rows.append(row)
    finally:
        # The derived specs are scratch: each run preserves its own copy, so
        # nothing is lost by removing them, and leaving them would litter the
        # user's directory with a file per configuration.
        for p in written:
            try:
                os.remove(p)
            except OSError:
                pass
            lock = os.path.splitext(p)[0] + ".freeze.lock"
            if os.path.isfile(lock):
                os.remove(lock)
        if prev is None:
            os.environ.pop("HWB_FEATURES", None)
        else:
            os.environ["HWB_FEATURES"] = prev

    manifest = {
        "schema": "hwbsweep/v0.1",
        "sweep_id": sweep_id,
        "mode": mode,
        "base_spec": os.path.abspath(spec_path),
        "base_spec_digest": base.digest,
        "features_root": root,
        "configurations": rows,
    }
    with open(os.path.join(sweep_dir, "sweep.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest


# ------------------------------------------------------------- interference

def _fields(a: Any, b: Any, prefix: str = "") -> List[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: List[str] = []
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                out.extend(_fields(a.get(k), b.get(k), "%s.%s" % (prefix, k)
                                   if prefix else k))
        return out or ([prefix] if prefix else [])
    return [prefix] if prefix else ["(value)"]


def interference(manifest: Dict[str, Any], runs_root: str) -> Dict[str, Any]:
    """MR-1 over a sweep: did attaching B move A's namespace?

    Compares each singleton {A} against every pair {A,B} that ran. A feature
    that A *requires* never yields a lone {A} run, so it is skipped rather
    than reported -- an unrunnable subset is not evidence of independence.
    """
    from .diff import load_run

    by_config: Dict[Tuple[str, ...], str] = {}
    unusable: List[Dict[str, Any]] = []
    for row in manifest["configurations"]:
        if not row.get("run_id"):
            continue
        # `executed` absent means the sweep predates the check -- treated as
        # usable rather than assumed broken, per the absence discipline.
        if row.get("executed") is False:
            unusable.append({"config": row["config"], "run_id": row["run_id"]})
            continue
        by_config[tuple(row["config"])] = row["run_id"]

    cache: Dict[str, Dict[str, Any]] = {}

    def rec(run_id: str) -> Dict[str, Any]:
        if run_id not in cache:
            cache[run_id] = load_run(runs_root, run_id)[0]
        return cache[run_id]

    findings: List[Dict[str, Any]] = []
    checked = 0
    for config, run_id in sorted(by_config.items()):
        if len(config) != 1:
            continue
        a = config[0]
        solo = rec(run_id)
        base_extras = strip_identity(solo["extras"].get(a), identity_of(solo))

        for other, other_run in sorted(by_config.items()):
            if len(other) != 2 or a not in other:
                continue
            b = other[0] if other[1] == a else other[1]
            paired = rec(other_run)
            with_b = strip_identity(paired["extras"].get(a), identity_of(paired))
            checked += 1
            if base_extras != with_b:
                findings.append({
                    "feature": a, "perturbed_by": b,
                    "fields": _fields(base_extras, with_b),
                    "alone": run_id, "with_other": other_run,
                })

    return {
        "relation": "extras[A] is invariant under attaching B",
        "pairs_checked": checked,
        "findings": findings,
        "unusable": unusable,
        "masked": ["run_id, spec_digest and the derived spec's filename, "
                   "wherever those values appear"],
    }


# ------------------------------------------------------------------- order


def order_significance(manifest: Dict[str, Any],
                       runs_root: str) -> Dict[str, Any]:
    """Family 11. Does the DECLARED ORDER of the features change the run?

    The relation: with the feature SET held constant, permuting the order
    should either change the run in a way the record explains, or not change
    it at all. Both answers are useful and they are different claims:

      not significant  order is a free choice here, and a reader comparing
                       two runs need not care that their feature lists were
                       written in different sequences
      significant      order is part of the experimental condition, and two
                       runs whose feature lists differ only in sequence are
                       NOT the same configuration

    This is the question `caused_by` was added for. Attempt provenance made
    `retry(sample(step))` and `sample(retry(step))` distinguishable in the
    record; nothing until now varied the order that produces them, so the
    field existed with no experiment behind it.

    A refusal to compare counts as significant: it is the strongest available
    statement that the pair are not interchangeable.
    """
    from .diff import Incomparable, compare, load_run

    rows = [r for r in manifest["configurations"]
            if r.get("run_id") and r.get("executed")]
    if len(rows) < 2:
        return {"relation": "the run is invariant under feature ORDER",
                "baseline": None, "compared": 0, "findings": [],
                "unusable": ["fewer than two configurations executed"]}

    base = rows[0]
    findings: List[Dict[str, Any]] = []
    for row in rows[1:]:
        try:
            res = compare(load_run(runs_root, base["run_id"]),
                          load_run(runs_root, row["run_id"]))
            differences = res["differences"]
        except Incomparable as e:
            differences = ["comparison refused: %s" % e]
        # The spec digest necessarily differs: reordering the feature list
        # reorders the spec's bytes, and that IS the manipulation. Counting
        # it would report every permutation as an ordering effect and the
        # family would be unable to report anything else -- the same confound
        # blast hit when it counted the injured feature's own namespace as
        # blast damage, and the same one `interfere` hit when the derived
        # spec's filename leaked into the comparison.
        differences = [d for d in differences
                       if not d.startswith("spec: DIFFERENT specs")]
        if differences:
            findings.append({
                "order": list(row["config"]),
                "against": list(base["config"]),
                "run_id": row["run_id"],
                "differences": differences[:6],
            })

    return {
        "relation": "the run is invariant under feature ORDER",
        "baseline": {"order": list(base["config"]), "run_id": base["run_id"]},
        "compared": len(rows) - 1,
        "findings": findings,
        # Named rather than implied: "no finding" here means order was free
        # for THIS feature set on THIS workload, never that order is free in
        # general. A set with one wrap feature cannot show an ordering effect
        # at `around_step` no matter how many permutations are run.
        "scope": "this feature set, this workload",
        "masked": ["spec_digest -- reordering the feature list reorders the "
                   "spec's bytes, so this is the variable, not an effect"],
        "unusable": [],
    }
