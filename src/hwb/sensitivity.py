"""Show every checker firing against a deliberate violation.

Family 9. The other families measure the SYSTEM; this one measures the
INSTRUMENT. Its question is the one the first campaign wrote down and then
applied to only some of the tools:

    "Without that, 'no interference' and 'cannot detect interference' are
    the same output."

A checker whose passing verdict is silence -- `equivalent`, `clean`,
`conforms: yes` -- cannot be distinguished from a checker that has stopped
looking. The only thing that separates them is a case the checker MUST
reject. So each probe below constructs a violation, runs one checker
against it, and records whether the checker noticed.

WHY THIS IS A FAMILY AND NOT A HABIT. The interference relation got a
`meddler` feature because someone remembered. `diff` did not, and `diff`
turned out to be blind to step output for its entire existence. A practice
applied from memory covers the tools you are already thinking about, which
are never the ones you have trusted for months. Enumerating the checkers
makes the coverage checkable instead of attentional.

EVERY PROBE RUNS ON A COPY. The catch campaign's third defect was that it
damaged the workload it measured -- restoring a deleted input without its
executable bit -- and the damage read as a step failure rather than as the
instrument breaking. Nothing here touches the real store: each probe copies
the run into the campaign directory and mutates the copy.

A POSITIVE CONTROL IS INCLUDED DELIBERATELY (`diff_exit_code`). If every
probe reports "detected", that is also what a broken probe harness reports
-- one that mutates nothing and asks a checker about two identical files.
The control is a violation `diff` is already known to catch, so a run where
the control fails means the harness is broken and no other row on the table
can be believed.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from typing import Any, Callable, Dict, List, Tuple

from .canon import canon_bytes
from .runner import _stamp

# Verdict vocabulary. `detected` is the only passing value; the others say
# how it failed, because "the checker missed it" and "the probe could not be
# set up" are different findings and must not average together.
DETECTED = "detected"
MISSED = "MISSED"
ERRORED = "errored"


class SensitivityError(Exception):
    """The campaign could not be set up."""


def _copy_run(runs_root: str, run_id: str, dst: str) -> str:
    src = os.path.join(runs_root, run_id)
    if not os.path.isdir(src):
        raise SensitivityError("no such run: %s" % run_id)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _first_stdout(run_dir: str) -> str:
    """The first attempt's stdout file, in deterministic order.

    Sorted rather than `os.walk` order so the same run always yields the same
    probe target -- an instrument that picks a different victim each run
    produces results that cannot be compared across campaigns.
    """
    hits = []
    steps = os.path.join(run_dir, "steps")
    if not os.path.isdir(steps):
        return ""
    for sid in sorted(os.listdir(steps)):
        adir = os.path.join(steps, sid, "attempts")
        if not os.path.isdir(adir):
            continue
        for n in sorted(os.listdir(adir), key=lambda x: (len(x), x)):
            p = os.path.join(adir, n, "stdout.bin")
            if os.path.isfile(p):
                hits.append(p)
    return hits[0] if hits else ""


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "wb") as fh:
        fh.write(canon_bytes(obj))


# ------------------------------------------------------------------- probes
#
# Each probe returns (verdict, detail). A probe is responsible for building
# its own violation and for calling exactly one checker, so a row on the
# table names one tool and one question.


def _probe_diff_output(runs_root: str, run_id: str, work: str) -> Tuple[str, str]:
    """Two runs of one configuration whose step OUTPUT differs.

    This is the case the second measurement campaign found live: two runs
    that produced six different sentences reported `equivalent`, because the
    projection masks `steps` wholesale and `attempt.stdout_bytes` besides.
    The bytes are preserved in the record -- fidelity confirms it -- so this
    is a comparison gap, not a recording gap.
    """
    from . import diff as diffmod

    a = _copy_run(runs_root, run_id, os.path.join(work, "A"))
    b = _copy_run(runs_root, run_id, os.path.join(work, "B"))

    target = _first_stdout(b)
    if not target:
        return ERRORED, "run has no stored stdout to mutate"
    with open(target, "ab") as fh:
        fh.write(b"\nMUTATED BY THE SENSITIVITY PROBE\n")

    try:
        res = diffmod.compare(diffmod.load_run(work, "A"),
                              diffmod.load_run(work, "B"))
    except diffmod.Incomparable as e:
        # A refusal IS a detection: the tool declined to call them equal.
        return DETECTED, "refused to compare: %s" % e
    if res["equivalent"]:
        return MISSED, ("reported `equivalent` for runs whose stored output "
                        "differs by %d bytes" % os.path.getsize(target))
    # Name the AXIS that caught it. A probe reporting `detected` with no
    # detail is one step away from a probe reporting `detected` for the
    # wrong reason, which is the thing this family exists to prevent.
    return DETECTED, ("output: " + "; ".join(res["output_differences"])
                      if res["output_differences"]
                      else "harness: " + "; ".join(res["differences"]))[:120]


def _probe_diff_exit_code(runs_root: str, run_id: str, work: str) -> Tuple[str, str]:
    """POSITIVE CONTROL. A difference `diff` is known to project.

    Exits are in the comparable view, so this must be caught. If it is not,
    the probe harness is broken and every other verdict on the table is
    uninterpretable -- which is exactly the failure a control exists to
    separate from a real finding.
    """
    from . import diff as diffmod

    _copy_run(runs_root, run_id, os.path.join(work, "A"))
    b = _copy_run(runs_root, run_id, os.path.join(work, "B"))

    ap = os.path.join(b, "attempts.jsonl")
    if not os.path.isfile(ap):
        return ERRORED, "run has no attempts.jsonl"
    with open(ap, "r", encoding="utf-8") as fh:
        lines = [json.loads(l) for l in fh if l.strip()]
    if not lines:
        return ERRORED, "run recorded no attempts"
    lines[0]["exit"] = (lines[0].get("exit") or 0) + 99
    with open(ap, "w", encoding="utf-8") as fh:
        for l in lines:
            fh.write(json.dumps(l, sort_keys=True) + "\n")

    try:
        res = diffmod.compare(diffmod.load_run(work, "A"),
                              diffmod.load_run(work, "B"))
    except diffmod.Incomparable as e:
        return DETECTED, "refused to compare: %s" % e
    if res["equivalent"]:
        return MISSED, "reported `equivalent` for runs with different exit codes"
    return DETECTED, "; ".join(res["differences"])[:120]


def _probe_verify_tamper(runs_root: str, run_id: str, work: str) -> Tuple[str, str]:
    """A record edited after the fact. Integrity must notice the bytes moved."""
    from . import runner

    d = _copy_run(runs_root, run_id, os.path.join(work, "T"))
    rp = os.path.join(d, "record.json")
    rec = _read_json(rp)
    rec["run_class"] = "tampered-by-the-sensitivity-probe"
    _write_json(rp, rec)

    res = runner.verify(d)
    if res["state"] == "clean":
        return MISSED, "reported `clean` for a record edited after close"
    return DETECTED, "%s (drifted: %s)" % (
        res["state"], ", ".join(res["drifted"]) or "-")


def _probe_conform_fabricated_attempt(runs_root: str, run_id: str,
                                      work: str) -> Tuple[str, str]:
    """An attempt claiming work that left no bytes behind.

    The store-agreement check exists for exactly this, and its first version
    returned early when `steps/` was absent -- so a fully fabricated attempt
    stream passed. That was the campaign's first defect. This probe is what
    would have caught it without waiting for a three-step spec to expose it.
    """
    from . import conform, diff as diffmod

    d = _copy_run(runs_root, run_id, os.path.join(work, "F"))
    ap = os.path.join(d, "attempts.jsonl")
    if not os.path.isfile(ap):
        return ERRORED, "run has no attempts.jsonl"
    with open(ap, "r", encoding="utf-8") as fh:
        lines = [json.loads(l) for l in fh if l.strip()]
    if not lines:
        return ERRORED, "run recorded no attempts"
    ghost = dict(lines[-1])
    ghost["step_id"] = "ghost-step"
    ghost["n"] = 0
    with open(ap, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(ghost, sort_keys=True) + "\n")

    record, attempts, _ = diffmod.load_run(work, "F")
    try:
        conform.validate_record(record, attempts, run_dir=d)
    except conform.NonConforming as e:
        return DETECTED, str(e)[:120]
    except Exception as e:                                   # noqa: BLE001
        return ERRORED, "%s: %s" % (type(e).__name__, e)
    return MISSED, ("reported `conforms` for an attempt stream containing a "
                    "step that never executed")


def _probe_conform_swapped_digest(runs_root: str, run_id: str,
                                  work: str) -> Tuple[str, str]:
    """A record whose spec_digest no longer matches the spec beside it.

    The run preserves its own spec precisely so this claim is checkable
    rather than merely asserted. If nothing checks it, preserving it bought
    provenance theatre.
    """
    from . import conform, diff as diffmod

    d = _copy_run(runs_root, run_id, os.path.join(work, "S"))
    rp = os.path.join(d, "record.json")
    rec = _read_json(rp)
    if not rec.get("spec_digest"):
        return ERRORED, "record carries no spec_digest"
    rec["spec_digest"] = "sha256:" + "0" * 64
    _write_json(rp, rec)

    record, attempts, _ = diffmod.load_run(work, "S")
    try:
        conform.validate_record(record, attempts, run_dir=d)
    except conform.NonConforming as e:
        return DETECTED, str(e)[:120]
    except Exception as e:                                   # noqa: BLE001
        return ERRORED, "%s: %s" % (type(e).__name__, e)
    return MISSED, "reported `conforms` for a record whose spec_digest is false"


# name -> (checker, probe, control?, why this violation must be caught)
PROBES: Dict[str, Tuple[str, Callable, bool, str]] = {
    "diff_exit_code": (
        "diff", _probe_diff_exit_code, True,
        "CONTROL -- a difference diff is known to project; if this misses, "
        "the probe harness is broken and no other row can be read"),
    "diff_output": (
        "diff", _probe_diff_output, False,
        "two runs whose stored step output differs are not equivalent, and "
        "diff is used as a determinism check"),
    "verify_tamper": (
        "verify", _probe_verify_tamper, False,
        "a record edited after close must not read as clean"),
    "conform_fabricated_attempt": (
        "conform", _probe_conform_fabricated_attempt, False,
        "an attempt with no bytes behind it must not satisfy the store check"),
    "conform_swapped_digest": (
        "conform", _probe_conform_swapped_digest, False,
        "a spec_digest that does not match the preserved spec is a false claim"),
}


def campaign(runs_root: str, run_id: str, sens_root: str) -> Dict[str, Any]:
    campaign_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(sens_root, campaign_id)
    os.makedirs(cdir)

    rows: List[Dict[str, Any]] = []
    for name in sorted(PROBES):
        checker, fn, is_control, why = PROBES[name]
        work = os.path.join(cdir, name)
        os.makedirs(work)
        try:
            verdict, detail = fn(runs_root, run_id, work)
        except SensitivityError as e:
            verdict, detail = ERRORED, str(e)
        except Exception as e:                               # noqa: BLE001
            verdict, detail = ERRORED, "%s: %s" % (type(e).__name__, e)
        rows.append({"probe": name, "checker": checker, "control": is_control,
                     "why": why, "verdict": verdict, "detail": detail})

    manifest = {
        "schema": "hwbsensitivity/v0.1",
        "campaign_id": campaign_id,
        "subject_run": run_id,
        "runs_root": os.path.abspath(runs_root),
        "probes": rows,
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest


def summarise(manifest: Dict[str, Any]) -> Dict[str, Any]:
    rows = manifest["probes"]
    control_ok = all(r["verdict"] == DETECTED for r in rows if r["control"])
    missed = [r for r in rows if r["verdict"] == MISSED and not r["control"]]
    errored = [r for r in rows if r["verdict"] == ERRORED]
    detected = [r for r in rows if r["verdict"] == DETECTED and not r["control"]]
    return {
        "control_ok": control_ok,
        "detected": len(detected),
        "missed": missed,
        "errored": errored,
        # Blind checkers, deduplicated: the actionable unit is the TOOL, not
        # the probe. Two probes missing on one tool is one blind tool.
        "blind_checkers": sorted({r["checker"] for r in missed}),
        "total": len([r for r in rows if not r["control"]]),
    }
