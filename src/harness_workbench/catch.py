"""What does a detector catch that would otherwise pass silently?

Family 4. Mutation testing pointed at the workload rather than the code:
perturb a declared input, run, and see whether any feature reports drift.

**A catch rate is meaningless without a stated fault model**, so the model is
declared here rather than implied. `freeze` has reported drift zero times
across every real run, and that is not evidence it is useless -- it is
evidence no fault was ever injected. Reporting 0/0 as a catch rate would be
the lie.

Three deliberate inclusions, each answering a pitfall the mutation-testing
literature names:

  EQUIVALENT MUTANTS. `trailing_newline` changes the bytes and not the
  meaning. `freeze` catches it -- correctly by its own definition, since its
  notion of "changed" is byte equality -- but at the level you care about it
  is a false alarm. Byte inequality is strictly stronger than "the experiment
  is now incomparable", and that gap is the interesting measurement, not the
  catch rate.

  CIRCULARITY. `append_byte` is the fault `freeze` was designed for.
  Observing that it catches it proves the implementation runs, not that the
  design earns itself. Marked as such so it cannot be read as a result.

  BOUNDED OBSERVATION, which is knowable in advance and worth stating rather
  than discovering later. `freeze` digests only `spec.all_inputs()`, so a file
  nobody declared is outside this detector -- as are the model weights, the
  interpreter, the environment and the clock. `drifted: false` means no drift
  in the declared set, not that every dependency was observed.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from .canon import canon_bytes
from .runner import _stamp


class CatchError(Exception):
    """The campaign could not be set up."""


def _append_byte(path: str) -> None:
    with open(path, "ab") as fh:
        fh.write(b"x")


def _trailing_newline(path: str) -> None:
    with open(path, "ab") as fh:
        fh.write(b"\n")


def _touch_only(path: str) -> None:
    st = os.stat(path)
    os.utime(path, (st.st_atime + 3600, st.st_mtime + 3600))


def _delete(path: str) -> None:
    os.remove(path)


# name -> (mutate, expected, why)
#   expected "caught"   -- a detector that misses this is failing
#   expected "ignored"  -- a detector that reports this is crying wolf
MUTATIONS: Dict[str, Tuple[Callable[[str], None], str, str]] = {
    "append_byte": (_append_byte, "caught",
                    "content changed -- the fault freeze was designed for, so "
                    "catching it proves the code runs, not that the design earns itself"),
    "trailing_newline": (_trailing_newline, "caught",
                         "bytes changed, meaning did not -- the equivalent-mutant "
                         "case; a true positive by byte equality and a false alarm "
                         "at the level you care about"),
    "touch_only": (_touch_only, "ignored",
                   "mtime moved, content identical -- digesting content rather "
                   "than metadata is what makes this ignorable"),
    "delete": (_delete, "caught",
               "a declared input that vanished must not read as unchanged"),
}


def _drift_reported(record: Dict[str, Any]) -> Optional[str]:
    for name, blob in (record.get("extras") or {}).items():
        if isinstance(blob, dict) and blob.get("drifted"):
            return name
    return None


def campaign(spec_path: str, runs_root: str, catches_root: str
             ) -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod, stores

    try:
        stores.require_disjoint(runs_root, catches_root,
                                "catch-campaign store")
    except stores.StoreOverlapError as e:
        raise CatchError(str(e))

    base = specmod.load(spec_path)
    inputs = base.all_inputs()
    if not inputs:
        raise CatchError("spec declares no step inputs; nothing to perturb")

    cid = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(catches_root, cid)
    os.makedirs(cdir)
    feat_root = featmod.features_root(base.dir, base.features_root)

    prev = os.environ.get("HWB_FEATURES")
    os.environ["HWB_FEATURES"] = feat_root
    rows: List[Dict[str, Any]] = []
    try:
        # The clean run establishes the baseline the detector compares
        # against. Unlike the blast campaign this deliberately reuses ONE
        # spec across every mutation, because a persistent baseline is
        # exactly what is being tested.
        clean = runner.execute(base, featmod.resolve(base), runs_root)
        rows.append({"mutation": "(none)", "input": "-",
                     "expected": "ignored", "run_id": clean["run_id"],
                     "detected_by": _drift_reported(clean)})

        for rel in inputs:
            full = os.path.join(base.dir, rel)
            if not os.path.isfile(full):
                continue
            for name, (mutate, expected, why) in sorted(MUTATIONS.items()):
                rows.append(_one(base, runs_root, feat_root, full, rel,
                                 name, mutate, expected, why))

        rows.append(_blind_spot(base, runs_root, feat_root))
    finally:
        if prev is None:
            os.environ.pop("HWB_FEATURES", None)
        else:
            os.environ["HWB_FEATURES"] = prev

    manifest = {
        "schema": "hwbcatch/v0.1",
        "campaign_id": cid,
        "base_spec": os.path.abspath(spec_path),
        "fault_model": {n: {"expected": e, "why": w}
                        for n, (_, e, w) in MUTATIONS.items()},
        "results": rows,
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest


def _one(base, runs_root, feat_root, full, rel, name, mutate, expected, why):
    from . import features as featmod, runner, spec as specmod

    row: Dict[str, Any] = {"mutation": name, "input": rel,
                           "expected": expected, "why": why}
    with open(full, "rb") as fh:
        original = fh.read()
    st = os.stat(full)
    mode = st.st_mode
    try:
        mutate(full)
        sp = specmod.load(base.path)
        rec = runner.execute(sp, featmod.resolve(sp), runs_root)
        row["run_id"] = rec["run_id"]
        row["detected_by"] = _drift_reported(rec)
    except Exception as e:                       # noqa: BLE001
        row["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        # Restore byte-for-byte INCLUDING mode and mtime, or the next
        # mutation starts from a workload the previous one moved.
        #
        # The MODE is not cosmetic. `delete` removes the file and the rewrite
        # recreates it with default permissions, stripping the executable
        # bit -- so a campaign against a spec whose step is `./probe.sh`
        # silently breaks every subsequent run with permission denied, and
        # the breakage reads as a step failure rather than as damage the
        # measurement did. Found by running catch against the real spec and
        # checking `git status` afterwards.
        with open(full, "wb") as fh:
            fh.write(original)
        os.chmod(full, mode)
        os.utime(full, (st.st_atime, st.st_mtime))
    return row


def _blind_spot(base, runs_root, feat_root) -> Dict[str, Any]:
    """Perturb a file the spec never declared.

    Structurally uncatchable: `freeze` digests `spec.all_inputs()` and
    nothing else. Included because a limit you have measured is worth more
    than one you assumed, and because this is the shape of the faults that
    actually escape -- the residual defect, not the one the detector was
    built for.
    """
    from . import features as featmod, runner, spec as specmod

    row: Dict[str, Any] = {
        "mutation": "undeclared_file", "input": "(not in steps[].inputs)",
        "expected": "ignored",
        "why": "structurally invisible: only declared inputs are digested, so "
               "the model weights, interpreter, environment and clock are all "
               "outside the detector's reach"}
    stray = os.path.join(base.dir, "undeclared-side-input.txt")
    try:
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("changed after the baseline\n")
        sp = specmod.load(base.path)
        rec = runner.execute(sp, featmod.resolve(sp), runs_root)
        row["run_id"] = rec["run_id"]
        row["detected_by"] = _drift_reported(rec)
    except Exception as e:                       # noqa: BLE001
        row["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if os.path.isfile(stray):
            os.remove(stray)
    return row


def summarise(manifest: Dict[str, Any]) -> Dict[str, Any]:
    caught_ok = missed = false_alarm = ignored_ok = 0
    notes: List[Dict[str, Any]] = []
    for r in manifest["results"]:
        if r["mutation"] == "(none)":
            continue
        detected = bool(r.get("detected_by"))
        if r["expected"] == "caught":
            if detected:
                caught_ok += 1
            else:
                missed += 1
                notes.append({**r, "verdict": "MISSED"})
        else:
            if detected:
                false_alarm += 1
                notes.append({**r, "verdict": "FALSE ALARM"})
            else:
                ignored_ok += 1

    total_should = caught_ok + missed
    return {
        "caught": caught_ok, "missed": missed,
        "false_alarms": false_alarm, "correctly_ignored": ignored_ok,
        # Reported as a fraction with its denominator, never as a bare rate:
        # a rate with no fault model behind it is the thing this module
        # exists to avoid.
        "catch_rate": (None if not total_should
                       else "%d/%d" % (caught_ok, total_should)),
        "notes": notes,
    }
