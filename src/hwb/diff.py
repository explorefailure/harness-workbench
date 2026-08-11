"""Comparison: a projection plus a relation, not a score.

Two runs are compared by projecting each record down to what is meant to be
stable and diffing that. What gets dropped -- the MASK -- is the noise floor
of every comparison built on this, so it is reported alongside the result
rather than applied silently. A comparison that does not say what it ignored
cannot be audited.

The projection is computed here and never stored: same rule as any other
derived view.

There is deliberately NO scorer. Judging whether a model's answer was good
needs an oracle this project does not have and does not need -- the subject
is the harness. What is checkable without an oracle is a RELATION between
two runs: same features and same inputs should give the same shape, and
attaching a feature should not disturb another's namespace. Semantic
comparison stays feature territory: a scorer writes verdicts into its own
extras namespace and `diff` surfaces them when present.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .canon import digest_file

# Fields dropped before comparing. Identity and clock only -- anything whose
# value is expected to differ between two correct runs of the same thing.
#
# NOT the declared environment. `env` was masked wholesale, so two runs of one
# spec -- one pinned, one at temperature 1.0 with no seed -- compared as
# EQUIVALENT despite being different experiments that produced different
# output. Declaring a knob exists precisely so it cannot change the experiment
# invisibly; masking it at comparison time reintroduced the failure one step
# later. Declared values are part of the condition, like the feature set.
# Undeclared NAMES stay masked: ~43 of them, identical in every run, noise.
MASK_RECORD = ("run_id", "started_at", "ended_at", "seam_timings",
               "spec_digest", "features", "extras", "steps",
               "schema", "seam_contract", "env", "replicates",
               # Where the run sat, not what it was. Inputs are pinned by
               # digest, and a genuine divergence there is caught by
               # `_digest_conflict`, which refuses rather than compares.
               "spec_path",
               # Which route supplied the features. WHAT ran is
               # compared via features[].digest, which is not
               # masked; two runs using identical feature code
               # from different routes are the same experiment.
               "features_source")
MASK_ATTEMPT = ("started", "duration_ms", "stdout_bytes", "stderr_bytes",
                "stdout_digest", "stderr_digest")
MASK_REPORTED = ("run_id", "started_at", "ended_at", "attempt.started",
                 "attempt.duration_ms",
                 "attempt.stdout_bytes / stderr_bytes (the SIZE; the "
                 "CONTENT is compared, reported separately as output)",
                 "attempt.stdout_digest / stderr_digest (the CONTENT is "
                 "read from artifacts and reported separately as output)",
                 "env.undeclared_names",
                 "seam_timings (reported separately as cost)",
                 "identity values inside extras: run_id, spec_digest, the "
                 "spec filename, and any self-attested digest over them")


class Incomparable(Exception):
    """The pair must not be compared. Not a difference -- a refusal."""


def output_digests(run_dir: str) -> Optional[Dict[str, str]]:
    """Digest per stored step output, hashed from the BYTES ON DISK.

    Deliberately NOT read from `integrity.json`, though that file already
    holds a sha256 of everything and reusing it would be cheaper. An
    integrity entry is a CLAIM about the bytes, written at close; the bytes
    are the evidence. Comparing two claims cannot see output that changed
    after the claim was made, and a comparison that reports `equivalent`
    for two runs whose stored bytes differ is the exact defect this function
    exists to fix -- reintroducing it one layer down would be worse than
    leaving it, because the fix would look present.

    The project has settled this before: `conform._preserved_spec` re-hashes
    the preserved spec rather than trusting `spec_digest`, on the grounds
    that "without the spec in the store the digest is unfalsifiable." Same
    rule, same reason.

    (Integrity's job is different and unaffected: `verify` asks whether the
    bytes moved since close. This asks whether two runs produced the same
    bytes. Both questions are real; neither answers the other.)

    None means UNKNOWN -- there is no `steps/` directory at all. Absent must
    never read as "the outputs matched".
    """
    steps = os.path.join(run_dir, "steps")
    if not os.path.isdir(steps):
        return None
    out: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(steps):
        dirnames[:] = sorted(dirnames)
        for fn in sorted(filenames):
            if fn not in ("stdout.bin", "stderr.bin"):
                continue
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, run_dir)] = digest_file(full)
    return out


def load_run(root: str, run_id: str) -> Tuple[Dict[str, Any],
                                              List[Dict[str, Any]],
                                              Optional[Dict[str, str]]]:
    """(record, attempts, output digests).

    The third element exists because `diff` was blind to step output for the
    whole of its existence: `MASK_RECORD` dropped `steps` wholesale and
    `MASK_ATTEMPT` dropped `stdout_bytes`, so two runs that produced six
    different sentences compared as `equivalent`. The bytes were preserved
    all along -- `fidelity` confirmed it -- so this was a comparison gap, not
    a recording gap.

    Loaded HERE rather than passed in by each caller on purpose. An opt-in
    parameter would be honoured by whoever remembered, which is how the gap
    arose: the rejection-test discipline was applied to `interfere` and not
    to `diff`.
    """
    d = os.path.join(root, run_id)
    rp = os.path.join(d, "record.json")
    if not os.path.isfile(rp):
        raise Incomparable("no such run: %s" % run_id)
    with open(rp, "r", encoding="utf-8") as fh:
        record = json.load(fh)
    attempts: List[Dict[str, Any]] = []
    ap = os.path.join(d, "attempts.jsonl")
    if os.path.isfile(ap):
        with open(ap, "r", encoding="utf-8") as fh:
            attempts = [json.loads(l) for l in fh if l.strip()]
    return record, attempts, output_digests(d)


def _causal(a: Dict[str, Any]) -> Optional[List[str]]:
    """The shape of an attempt's cause, ordinals dropped.

    Absent means provenance was NOT RECORDED, which is not the same as no
    nesting -- runs written before `caused_by` existed carry no stack, and
    must not be reported as matching a run that genuinely had no wraps.
    """
    if "caused_by" not in a:
        return None
    return [f["feature"] for f in a["caused_by"]]


def project(record: Dict[str, Any], attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The comparable view of a run."""
    by_step: Dict[str, Dict[str, Any]] = {}
    for a in attempts:
        s = by_step.setdefault(a["step_id"], {"attempts": 0, "exits": [],
                                              "causes": []})
        s["attempts"] += 1
        s["exits"].append(a.get("exit"))
        s["causes"].append(_causal(a))
    return {
        "run_class": record.get("run_class"),
        "status": record.get("status"),
        "features": sorted(f["name"] for f in record.get("features", [])),
        "feature_status": {f["name"]: f["status"]
                           for f in record.get("features", [])},
        "steps": [s["id"] for s in record.get("steps", [])],
        "argv": {s["id"]: s["argv"] for s in record.get("steps", [])},
        "failed_steps": record.get("failed_steps", []),
        "declared_env": (record.get("env") or {}).get("declared") or {},
        "by_step": by_step,
        "extras_keys": sorted(record.get("extras", {}).keys()),
    }


def _drift(record: Dict[str, Any]) -> Optional[str]:
    """Whether a content-digest feature reported input drift.

    Read by record key rather than by feature name: any feature that reports
    `drifted` is honoured, so a replacement implementation still protects
    the comparison.
    """
    for name, blob in (record.get("extras") or {}).items():
        if isinstance(blob, dict) and blob.get("drifted"):
            return "%s: %s" % (name, blob.get("summary") or "inputs drifted")
    return None


def _digests(record: Dict[str, Any]) -> Dict[str, str]:
    """Every input digest any feature recorded, flattened."""
    out: Dict[str, str] = {}
    for blob in (record.get("extras") or {}).values():
        if isinstance(blob, dict) and isinstance(blob.get("digests"), dict):
            out.update(blob["digests"])
    return out


def _digest_conflict(rec_a: Dict[str, Any], rec_b: Dict[str, Any]) -> List[str]:
    """Inputs both runs digested, where they disagree.

    Only paths present in BOTH are compared: a path one run never declared
    is a spec difference, which is reported as a difference rather than a
    refusal.
    """
    da, db = _digests(rec_a), _digests(rec_b)
    return sorted(k for k in set(da) & set(db) if da[k] != db[k])


def _output_delta(out_a: Optional[Dict[str, str]],
                  out_b: Optional[Dict[str, str]]) -> Tuple[List[str], bool]:
    """How the two runs' stored step outputs differ. (lines, known)

    Reported as its OWN AXIS rather than folded into the structural
    differences, because they answer different questions: whether the
    HARNESS behaved the same, and whether the WORK came out the same. The
    subject of this project is the harness, so collapsing the two would make
    every unpinned pair read as "different" with no way to see that the
    harness half matched -- `sample(n=3)` against a live model differs on
    every draw, and that is the workload, not the machinery.

    But it is NOT masked either, which is the fix. `equivalent` now requires
    both axes clean, so the verdict can no longer claim two runs are the
    same when their outputs are not.
    """
    if out_a is None or out_b is None:
        return (["output digests unavailable for %s -- not compared, and "
                 "NOT the same as matching"
                 % ("A" if out_a is None else "B")], False)
    lines = []
    for rel in sorted(set(out_a) | set(out_b)):
        da, db = out_a.get(rel), out_b.get(rel)
        if da == db:
            continue
        if da is None or db is None:
            lines.append("%s: only in %s" % (rel, "B" if da is None else "A"))
        else:
            lines.append("%s: %s vs %s" % (rel, da[7:19], db[7:19]))
    return lines, True


def compare(a: Tuple[Dict, List, Optional[Dict]],
            b: Tuple[Dict, List, Optional[Dict]]) -> Dict[str, Any]:
    rec_a, at_a, out_a = a
    rec_b, at_b, out_b = b

    # Drift protection lives HERE, not in a gate. `freeze` annotates rather
    # than blocking, so a drifted run is kept and remains readable -- it
    # simply must not be set against a baseline it no longer shares inputs
    # with. Refusing is the point: a delta computed across drifted inputs
    # looks like a finding and is an artefact.
    for label, rec in (("A", rec_a), ("B", rec_b)):
        d = _drift(rec)
        if d:
            raise Incomparable("%s reported input drift -- %s" % (label, d))

    # A clean drift flag is NOT sufficient, proven against real runs: two
    # determinism runs both reported "inputs match baseline" while their
    # digests for ollama_probe.py differed, because the baseline had been
    # RECREATED between them. Each run told the truth about its own
    # baseline; the pair still ran different code. `drifted` is a feature's
    # opinion about mutable state, the digests are the evidence, so the
    # evidence decides.
    changed = _digest_conflict(rec_a, rec_b)
    if changed:
        raise Incomparable(
            "inputs differ between the pair (%s) -- both runs may report no "
            "drift if the baseline was recreated between them" % ", ".join(changed))

    pa, pb = project(rec_a, at_a), project(rec_b, at_b)
    diffs: List[str] = []

    if rec_a.get("spec_digest") != rec_b.get("spec_digest"):
        diffs.append("spec: DIFFERENT specs (%s vs %s)" % (
            rec_a.get("spec_digest", "?")[:19],
            rec_b.get("spec_digest", "?")[:19]))

    only_a = [f for f in pa["features"] if f not in pb["features"]]
    only_b = [f for f in pb["features"] if f not in pa["features"]]
    if only_a:
        diffs.append("features: only in A -- %s" % ", ".join(only_a))
    if only_b:
        diffs.append("features: only in B -- %s" % ", ".join(only_b))

    broken = {k: v for p in (pa, pb)
              for k, v in p["feature_status"].items() if v != "ok"}
    if broken:
        diffs.append("features: not ok -- %s" % json.dumps(broken, sort_keys=True))

    if pa["status"] != pb["status"]:
        diffs.append("status: %s vs %s" % (pa["status"], pb["status"]))

    ea, eb = pa["declared_env"], pb["declared_env"]
    for k in sorted(set(ea) | set(eb)):
        if ea.get(k) != eb.get(k):
            diffs.append("env[%s]: %r vs %r" % (k, ea.get(k), eb.get(k)))

    for sid in sorted(set(pa["by_step"]) | set(pb["by_step"])):
        sa = pa["by_step"].get(sid)
        sb = pb["by_step"].get(sid)
        if sa is None or sb is None:
            diffs.append("step %s: only in %s" % (sid, "A" if sb is None else "B"))
            continue
        if sa["attempts"] != sb["attempts"]:
            diffs.append("step %s: %d attempt(s) vs %d"
                         % (sid, sa["attempts"], sb["attempts"]))
        if sa["exits"] != sb["exits"]:
            diffs.append("step %s: exits %s vs %s"
                         % (sid, sa["exits"], sb["exits"]))
        if sa["causes"] != sb["causes"]:
            # The case caused_by exists for: identical counts and identical
            # exits, different composition. Invisible before provenance.
            diffs.append("step %s: attempt CAUSE differs -- %s vs %s"
                         % (sid, _fmt_causes(sa["causes"]),
                            _fmt_causes(sb["causes"])))

    ka, kb = set(pa["extras_keys"]), set(pb["extras_keys"])
    shared = sorted(ka & kb)
    for k in shared:
        # The SAME identity mask the interference relation uses. The two
        # shared a projection and masked differently: `diff` reported
        # `baseline_file` as a difference where `interfere` masked it, so two
        # runs of one configuration looked non-identical because their spec
        # files had different names. One mask, one place, both consumers.
        va = strip_identity(rec_a["extras"].get(k), identity_of(rec_a))
        vb = strip_identity(rec_b["extras"].get(k), identity_of(rec_b))
        if va != vb:
            # Name the fields, not just the feature. "extras[freeze] differs"
            # is unactionable; knowing it differs at `baseline` and `summary`
            # but NOT at `digests` is the difference between a stateful
            # first-run artefact and actual input drift.
            diffs.append("extras[%s]: differs at %s" % (k, _fields(va, vb)))

    out_diffs, out_known = _output_delta(out_a, out_b)
    return {
        # BOTH axes must be clean. Structural sameness alone used to be
        # reported as `equivalent`, which is how a pair producing six
        # different sentences passed a determinism check.
        "equivalent": not diffs and not out_diffs,
        "harness_equivalent": not diffs,
        "differences": diffs,
        "output_differences": out_diffs,
        "output_known": out_known,
        "shared_extras": shared,
        "cost": _cost(rec_a, rec_b),
        "masked": list(MASK_REPORTED),
    }


def _fields(va: Any, vb: Any) -> str:
    """Which keys of two extras blobs disagree."""
    if not isinstance(va, dict) or not isinstance(vb, dict):
        return "(value)"
    keys = sorted(set(va) | set(vb))
    bad = [k for k in keys if va.get(k) != vb.get(k)]
    return ", ".join(bad) if bad else "(value)"



# ------------------------------------------------- the identity mask
# Lives here because it is part of the comparable projection, and
# because keeping a private copy in each consumer is how the same
# confound was reintroduced: interference masked identity, the blast
# campaign did not, and every one of its 15 injections reported
# 'disturbed another feature' when nothing had been disturbed.

def identity_of(record: Dict[str, Any]) -> set:
    """Values that necessarily differ between two runs of a sweep.

    A sweep varies the spec in order to vary the feature set, so anything a
    feature derives from the SPEC'S NAME varies with it -- `freeze` keys its
    baseline to the spec stem, so `baseline_file` differed in every pair and
    was reported as interference. That is a confound of the instrument, not a
    property of the system: the ablation literature's warning about
    misalignment between the ablation design and the thing being ablated.
    """
    ids = {record["run_id"], record["spec_digest"]}
    extras = record.get("extras") or {}
    for blob in extras.values():
        if isinstance(blob, dict):
            f = blob.get("baseline_file")
            if isinstance(f, str):
                ids.add(f)
    # A SELF-ATTESTED DIGEST over a payload containing identity is itself
    # identity, and masking the payload cannot mask it -- the hash was taken
    # before the mask existed. `receipt` binds run_id and spec_digest, so its
    # digest necessarily differs between any two runs; without this it reads
    # as a disturbance in every comparison.
    for f in record.get("features", []):
        decl = f.get("self_attests")
        if not isinstance(decl, dict):
            continue
        blob = extras.get(f["name"])
        if isinstance(blob, dict):
            v = blob.get(decl.get("digest"))
            if isinstance(v, str):
                ids.add(v)
    return ids


def strip_identity(obj: Any, identities: set) -> Any:
    """Blank values that are this run's identity, wherever they appear.

    Masked BY VALUE, not by key name. `receipt` embeds run_id and
    spec_digest inside its payload, and those provably differ between any
    two runs -- reporting them as interference would drown the signal. But
    masking every field NAMED run_id would also hide a feature that wrongly
    copied someone else's, so only the actual identity strings are removed.
    """
    if isinstance(obj, str):
        return "<identity>" if obj in identities else obj
    if isinstance(obj, list):
        return [strip_identity(v, identities) for v in obj]
    if isinstance(obj, dict):
        return {k: strip_identity(v, identities) for k, v in obj.items()}
    return obj



def _fmt_causes(causes: List[Optional[List[str]]]) -> str:
    out = []
    for c in causes:
        out.append("-" if c is None else "(%s)" % ">".join(c))
    return " ".join(out) or "-"


def _cost(rec_a: Dict[str, Any], rec_b: Dict[str, Any]) -> List[str]:
    """Seam dispatch cost, reported but NOT counted as a difference.

    Timing varies run to run, so treating it as a diff would make every
    comparison fail. It is the answer to a different question -- what a
    feature cost -- so it is surfaced beside the verdict, never inside it.
    """
    ta = rec_a.get("seam_timings") or {}
    tb = rec_b.get("seam_timings") or {}
    rows = []
    for feat in sorted(set(ta) | set(tb)):
        for seam in sorted(set(ta.get(feat, {})) | set(tb.get(feat, {}))):
            va = ta.get(feat, {}).get(seam, {}).get("total_ms")
            vb = tb.get(feat, {}).get(seam, {}).get("total_ms")
            rows.append("%-10s %-16s %8s -> %-8s" % (
                feat, seam,
                "-" if va is None else "%.3fms" % va,
                "-" if vb is None else "%.3fms" % vb))
    return rows
