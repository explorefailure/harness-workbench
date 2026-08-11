"""Re-execute a recorded run from its own preserved artifacts.

Family 10. Every run already stores the spec it ran and the source of every
feature that was attached, and `fidelity` reports the reproducibility
question as ANSWERED on that basis. But answerability is not reproduction --
fidelity says so itself, in its own closing line -- and until something
actually re-executes a run, "the spec is preserved beside the record" is a
claim with no consumer.

WHAT THIS FOUND ON ITS FIRST RUN. The record does NOT name the directory it
executed in. Steps resolve `argv` and `inputs` against the spec's directory,
so a preserved spec whose step reads `prompts/q1.txt` cannot be replayed
without knowing where that path was rooted. The spec is preserved; the frame
it resolved against is not. So `--in` must be supplied by a human, and this
module records that it was supplied rather than recovered -- a replay that
needed outside information is not evidence the record is sufficient.

THE SANDBOX. Replaying in the original directory would overwrite the state
the original run left, and a family that damages what it measures is the
catch campaign's third defect wearing a new name. So the declared inputs are
copied -- with their modes, because a lost executable bit is exactly how
that defect presented -- into a scratch directory, together with the spec
under its ORIGINAL basename.

The basename matters and is not cosmetic. A feature with persistent state
keys it to the spec stem, so replaying under a fresh name would put every
such feature on its initialising path and guarantee a difference that says
nothing about reproducibility. State files keyed to the stem are carried
across for the same reason.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .canon import canon_bytes, digest_file
from .runner import _stamp

MATCHED = "matched"
DIVERGED = "DIVERGED"
# The original run is the odd one out: it INITIALISED state that now exists,
# so no later execution can reproduce it. Two replays agreeing with each
# other and disagreeing with the original is the evidence for that, and it
# needs no knowledge of which feature was stateful.
STATEFUL_ORIGIN = "unreplayable (origin initialised state)"


class ReplayError(Exception):
    """The run could not be replayed. Not a divergence -- a refusal."""


def _load(runs_root: str, run_id: str) -> Tuple[Dict[str, Any], str]:
    d = os.path.join(runs_root, run_id)
    rp = os.path.join(d, "record.json")
    if not os.path.isfile(rp):
        raise ReplayError("no such run: %s" % run_id)
    with open(rp, "r", encoding="utf-8") as fh:
        return json.load(fh), d


def _recorded_digests(record: Dict[str, Any]) -> Dict[str, str]:
    """Input digests any feature recorded, flattened. Same shape `diff` uses."""
    out: Dict[str, str] = {}
    for blob in (record.get("extras") or {}).values():
        if isinstance(blob, dict) and isinstance(blob.get("digests"), dict):
            out.update(blob["digests"])
    return out


def _sandbox(spec_raw: Dict[str, Any], spec_name: str, source_dir: str,
             dst: str) -> Tuple[List[str], List[str]]:
    """The declared inputs, copied with their modes, plus the spec.

    Returns (copied, undeclared). `undeclared` is the finding: files a step's
    argv needs that the spec never listed as inputs. `inputs` governs what a
    feature may digest, so an executable missing from it is invisible to
    `freeze` -- the script that produced the run can be swapped without any
    digest moving. That is the same shape as an undeclared environment knob,
    one layer down, and replay is what surfaces it because a sandbox built
    only from the declared list simply cannot run the step.
    """
    os.makedirs(dst, exist_ok=True)
    copied: List[str] = []
    declared = set()
    for step in spec_raw.get("steps", []):
        declared.update(step.get("inputs") or [])

    # argv entries that name a real file here but were never declared.
    undeclared = []
    for step in spec_raw.get("steps", []):
        for tok in step.get("argv") or []:
            rel = tok[2:] if tok.startswith("./") else tok
            if rel in declared or os.path.isabs(rel) or "/" in rel.strip("./"):
                continue
            if os.path.isfile(os.path.join(source_dir, rel)):
                undeclared.append(rel)

    rels = sorted(declared | set(undeclared))
    for rel in sorted(set(rels)):
        src = os.path.join(source_dir, rel)
        if not os.path.isfile(src):
            continue
        tgt = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(tgt), exist_ok=True)
        shutil.copy2(src, tgt)                 # copy2 preserves the mode
        copied.append(rel)

    stem = os.path.splitext(spec_name)[0]
    for fn in sorted(os.listdir(source_dir)):
        # State a feature keyed to this spec's stem. Carried across so a
        # stateful feature reaches its DECIDING path on replay instead of its
        # initialising one; without this every such feature diverges and the
        # divergence is about the sandbox, not the record.
        if fn.startswith(stem + ".") and fn != spec_name and \
                os.path.isfile(os.path.join(source_dir, fn)):
            shutil.copy2(os.path.join(source_dir, fn), os.path.join(dst, fn))
            copied.append(fn)

    with open(os.path.join(dst, spec_name), "wb") as fh:
        fh.write(canon_bytes(spec_raw))
    return copied, sorted(set(undeclared))


def replay(runs_root: str, run_id: str, replays_root: str,
           source_dir: Optional[str] = None) -> Dict[str, Any]:
    from . import (diff as diffmod, features as featmod, runner,
                   spec as specmod, stores)

    try:
        stores.require_disjoint(runs_root, replays_root, "replay store")
    except stores.StoreOverlapError as e:
        raise ReplayError(str(e))

    record, rdir = _load(runs_root, run_id)

    preserved_spec = os.path.join(rdir, "spec.json")
    preserved_feats = os.path.join(rdir, "features")
    if not os.path.isfile(preserved_spec):
        raise ReplayError(
            "run %s did not preserve its spec -- it predates preservation, so "
            "there is nothing to replay from" % run_id)
    if not os.path.isdir(preserved_feats):
        raise ReplayError(
            "run %s did not preserve its feature source" % run_id)

    with open(preserved_spec, "r", encoding="utf-8") as fh:
        spec_raw = json.load(fh)

    # Where the workload lived. `spec_path` is recorded now, so this is
    # normally recovered rather than supplied -- but an explicit `--in` still
    # wins, because a run replayed on another machine or from a moved
    # checkout has a recorded path that no longer exists.
    recorded = record.get("spec_path")
    recoverable = bool(recorded) and os.path.isdir(os.path.dirname(recorded))
    supplied = source_dir is not None
    if source_dir is None and recoverable:
        source_dir = os.path.dirname(recorded)
    source_dir = os.path.abspath(source_dir or os.getcwd())
    if not os.path.isdir(source_dir):
        raise ReplayError("workload directory does not exist: %s" % source_dir)

    campaign_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(replays_root, campaign_id)
    box = os.path.join(cdir, "workload")
    os.makedirs(cdir)

    # The original spec's basename, recovered from the freeze-style state
    # files if possible and otherwise `spec.json`. Only used for naming.
    spec_name = (os.path.basename(recorded) if recorded
                 else _original_spec_name(record) or "spec.json")
    copied, undeclared = _sandbox(spec_raw, spec_name, source_dir, box)

    # Replaying against inputs that are not the recorded ones is not a
    # replay. The digests the ORIGINAL run recorded are the evidence, and
    # refusing here keeps a divergence from being misread as irreproducible
    # when the real cause is a changed workload.
    want = _recorded_digests(record)
    drifted = []
    for rel, dig in sorted(want.items()):
        p = os.path.join(box, rel)
        if not os.path.isfile(p):
            drifted.append("%s (missing)" % rel)
        elif digest_file(p) != dig:
            drifted.append(rel)
    if drifted:
        raise ReplayError(
            "the inputs under %s are not the ones this run recorded (%s) -- "
            "replaying against different inputs measures the workload, not "
            "the record" % (source_dir, ", ".join(drifted)))

    def _execute() -> Dict[str, Any]:
        prev = os.environ.get("HWB_FEATURES")
        os.environ["HWB_FEATURES"] = preserved_feats
        try:
            sp = specmod.load(os.path.join(box, spec_name))
            return runner.execute(sp, featmod.resolve(sp), runs_root)
        except (specmod.SpecError, featmod.FeatureError,
                runner.HarnessError) as e:
            raise ReplayError("replay failed to execute: %s" % e)
        finally:
            if prev is None:
                os.environ.pop("HWB_FEATURES", None)
            else:
                os.environ["HWB_FEATURES"] = prev

    def _cmp(a_id: str, b_id: str) -> Tuple[List[str], Optional[str]]:
        try:
            res = diffmod.compare(diffmod.load_run(runs_root, a_id),
                                  diffmod.load_run(runs_root, b_id))
            return res["differences"], None
        except diffmod.Incomparable as e:
            return [], str(e)

    fresh = _execute()
    differences, refused = _cmp(run_id, fresh["run_id"])

    # A divergence has two very different causes and they must not print the
    # same way: the harness is nondeterministic, or the ORIGINAL run is
    # simply not reproducible because it created state that now exists. A
    # second replay separates them with evidence rather than by assumption --
    # if the two replays agree with each other, the original is the outlier.
    second: Optional[str] = None
    verdict = MATCHED if (not differences and refused is None) else DIVERGED
    if verdict == DIVERGED:
        again = _execute()
        second = again["run_id"]
        d2, r2 = _cmp(fresh["run_id"], again["run_id"])
        if not d2 and r2 is None:
            verdict = STATEFUL_ORIGIN
    manifest = {
        "schema": "hwbreplay/v0.1",
        "campaign_id": campaign_id,
        "original_run": run_id,
        "replay_run": fresh["run_id"],
        "second_replay_run": second,
        "verdict": verdict,
        "differences": differences,
        "refused": refused,
        # Files a step needed that the spec never declared. Invisible to any
        # content-digest feature, so the run's inputs could change without a
        # single digest moving.
        "undeclared_step_files": undeclared,
        "workload_dir": source_dir,
        # The honest bit. A `matched` verdict here still required a human to
        # say where the workload lived.
        "workload_dir_supplied_by_hand": supplied,
        "workload_dir_recoverable_from_record": recoverable,
        "inputs_copied": copied,
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest


def _original_spec_name(record: Dict[str, Any]) -> Optional[str]:
    """Recover the spec's filename from what features recorded about it.

    A feature keying state to the spec stem necessarily names that file, so
    the basename survives even though the DIRECTORY does not. Read
    generically by shape rather than by feature name, for the same reason
    `diff` reads drift by shape: hardcoding a feature here would put
    knowledge of one feature into the base.
    """
    for blob in (record.get("extras") or {}).values():
        if not isinstance(blob, dict):
            continue
        bf = blob.get("baseline_file")
        if isinstance(bf, str) and "." in bf:
            stem = os.path.basename(bf).split(".")[0]
            return stem + ".json"
    return None
