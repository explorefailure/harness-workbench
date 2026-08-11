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

EVERY RECORD PROBE RUNS ON A COPY. The catch campaign's third defect was that
it damaged the workload it measured -- restoring a deleted input without its
executable bit -- and the damage read as a step failure rather than as the
instrument breaking. Nothing here touches the real store: record readers get
a copied run, manifest reducers get a deliberately red in-memory manifest,
and replay gets a fresh isolated fixture under the campaign directory.

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
UNPROBED = "UNPROBED"

# The public verdict engines, declared independently of the probes. Adding a
# checker here without adding a probe cannot produce a quietly smaller green
# table: campaign() emits an UNPROBED row and the command fails. `sweep` is a
# configuration producer, not a verdict engine; `sensitivity` does not probe
# itself.
PUBLIC_VERDICT_ENGINES = (
    "blast",
    "catch",
    "confine",
    "conform",
    "diff",
    "efficacy",
    "effects",
    "fidelity",
    "interfere",
    "order",
    "replay",
    "steady",
    "verify",
)


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


def _probe_conform_artifact_mismatch(runs_root: str, run_id: str,
                                     work: str) -> Tuple[str, str]:
    """An attempt descriptor whose stored stdout no longer satisfies it."""
    from . import conform, diff as diffmod

    d = _copy_run(runs_root, run_id, os.path.join(work, "M"))
    target = _first_stdout(d)
    if not target:
        return ERRORED, "run has no stored stdout to mutate"
    with open(target, "ab") as fh:
        fh.write(b"\nARTIFACT MOVED AFTER ITS DESCRIPTOR\n")
    record, attempts, _ = diffmod.load_run(work, "M")
    try:
        conform.validate_record(record, attempts, run_dir=d)
    except conform.NonConforming as e:
        return DETECTED, str(e)[:120]
    except Exception as e:                                   # noqa: BLE001
        return ERRORED, "%s: %s" % (type(e).__name__, e)
    return MISSED, "reported `conforms` when stored output disagreed with its descriptor"


def _probe_confine_record_reach(runs_root: str, run_id: str,
                                work: str) -> Tuple[str, str]:
    """A feature recorded as reaching through into another namespace."""
    from . import confine

    d = _copy_run(runs_root, run_id, os.path.join(work, "C"))
    rp = os.path.join(d, "record.json")
    rec = _read_json(rp)
    if not rec.get("features"):
        return ERRORED, "run has no feature to mark as reaching through"
    feat = rec["features"][0]
    feat["breaches"] = [{"seam": "after_run", "step": None,
                         "namespace": "somebody-else", "kind": "foreign",
                         "power": feat.get("power")}]
    _write_json(rp, rec)
    res = confine.assess(d)
    if not res["breached"]:
        return MISSED, "reported no record-power breach for recorded foreign reach"
    return DETECTED, res["breached"][0]["detail"][:120]


def _probe_fidelity_missing_output(runs_root: str, run_id: str,
                                   work: str) -> Tuple[str, str]:
    """A run that lists an attempt but no longer carries all of its output."""
    from . import fidelity

    d = _copy_run(runs_root, run_id, os.path.join(work, "L"))
    target = _first_stdout(d)
    if not target:
        return ERRORED, "run has no stored stdout to remove"
    os.remove(target)
    res = fidelity.assess(d)
    row = next(r for r in res["questions"] if r["key"] == "what_was_produced")
    if row["verdict"] == fidelity.ANSWERED:
        return MISSED, "answered what was produced after stored output was removed"
    return DETECTED, "%s: %s" % (row["verdict"], row["detail"])


def _probe_interfere_namespace_move(runs_root: str, run_id: str,
                                    work: str) -> Tuple[str, str]:
    """A's record namespace moves when B is attached."""
    from . import sweep

    solo = _copy_run(runs_root, run_id, os.path.join(work, "solo"))
    pair = _copy_run(runs_root, run_id, os.path.join(work, "pair"))
    rp = os.path.join(solo, "record.json")
    rec = _read_json(rp)
    names = [f["name"] for f in rec.get("features", [])]
    if len(names) < 2:
        return ERRORED, "run needs at least two features for interference"
    a, b = names[:2]
    rec["features"] = [f for f in rec["features"] if f["name"] == a]
    rec["extras"] = {a: rec.get("extras", {}).get(a, {})}
    _write_json(rp, rec)

    pp = os.path.join(pair, "record.json")
    paired = _read_json(pp)
    blob = paired.setdefault("extras", {}).setdefault(a, {})
    if not isinstance(blob, dict):
        return ERRORED, "feature %s has a non-object extras namespace" % a
    blob["sensitivity_probe"] = "moved only when %s attached" % b
    _write_json(pp, paired)

    man = {"configurations": [
        {"config": [a], "run_id": "solo", "executed": True},
        {"config": [a, b], "run_id": "pair", "executed": True},
    ]}
    res = sweep.interference(man, work)
    if not res["findings"]:
        return MISSED, "reported no interference after extras[%s] moved" % a
    return DETECTED, "%s perturbed by %s at %s" % (
        a, b, ", ".join(res["findings"][0]["fields"]))


def _probe_order_changed_run(runs_root: str, run_id: str,
                             work: str) -> Tuple[str, str]:
    """Two declared orders of one feature set produce different attempts."""
    from . import sweep

    _copy_run(runs_root, run_id, os.path.join(work, "declared"))
    changed = _copy_run(runs_root, run_id, os.path.join(work, "reversed"))
    rec = _read_json(os.path.join(changed, "record.json"))
    names = [f["name"] for f in rec.get("features", [])]
    if len(names) < 2:
        return ERRORED, "run needs at least two features for order"
    ap = os.path.join(changed, "attempts.jsonl")
    with open(ap, "r", encoding="utf-8") as fh:
        attempts = [json.loads(line) for line in fh if line.strip()]
    if not attempts:
        return ERRORED, "run recorded no attempts"
    attempts[0]["exit"] = (attempts[0].get("exit") or 0) + 91
    with open(ap, "w", encoding="utf-8") as fh:
        for attempt in attempts:
            fh.write(json.dumps(attempt, sort_keys=True) + "\n")
    man = {"configurations": [
        {"config": names, "run_id": "declared", "executed": True},
        {"config": list(reversed(names)), "run_id": "reversed", "executed": True},
    ]}
    res = sweep.order_significance(man, work)
    if not res["findings"]:
        return MISSED, "reported order insignificant when the run changed"
    return DETECTED, res["findings"][0]["differences"][0][:120]


def _probe_blast_broken_survival(runs_root: str, run_id: str,
                                 work: str) -> Tuple[str, str]:
    """A fault whose damage violates every promised survival bit."""
    from . import blast

    man = {"injections": [{"feature": "probe", "fault": "raise",
                            "power": "annotate", "completed": False,
                            "conforms": False, "others_intact": False,
                            "steps_retained": False}]}
    res = blast.summarise(man)
    if not res["findings"]:
        return MISSED, "reported no blast finding when all survival bits failed"
    return DETECTED, "violated " + ", ".join(res["findings"][0]["violated"])


def _probe_catch_missed_declared_drift(runs_root: str, run_id: str,
                                       work: str) -> Tuple[str, str]:
    """A declared content mutation with no detector reporting drift."""
    from . import catch as catchmod

    man = {"results": [{"mutation": "append_byte", "input": "declared.txt",
                         "expected": "caught", "why": "known red",
                         "detected_by": None}]}
    res = catchmod.summarise(man)
    if not res["missed"]:
        return MISSED, "reported no miss for an undetected declared-input mutation"
    return DETECTED, res["notes"][0]["verdict"]


def _probe_efficacy_surviving_opposite(runs_root: str, run_id: str,
                                       work: str) -> Tuple[str, str]:
    """A well-formed opposite that leaves the measurable run unchanged."""
    from . import efficacy

    man = {"mutants": [{"feature": "probe", "power": "annotate",
                         "intent": "capability", "verdict": efficacy.SURVIVED,
                         "decision": "known red", "detail": "equivalent"}]}
    res = efficacy.summarise(man)
    if not res["inert"]:
        return MISSED, "reported no inert feature for a surviving opposite"
    return DETECTED, "surviving opposite classified inert"


def _probe_steady_moving_baseline(runs_root: str, run_id: str,
                                  work: str) -> Tuple[str, str]:
    """An unchanged-control series with an unallowed moving output axis."""
    from . import steady

    man = {"verdict": steady.UNSTABLE,
           "run_ids": ["A", "B", "C"],
           "comparisons": [{"verdict": steady.UNSTABLE}],
           "moving_axes": ["output:steps/01/attempts/0/stdout.bin"],
           "unallowed_axes": ["output:steps/01/attempts/0/stdout.bin"],
           "setup_error": None}
    res = steady.summarise(man)
    if res["verdict"] != steady.UNSTABLE or not res["unallowed_axes"]:
        return MISSED, "reduced a moving unallowed baseline without instability"
    return DETECTED, "unallowed output axis classified unstable"


def _probe_effects_out_of_envelope(runs_root: str, run_id: str,
                                   work: str) -> Tuple[str, str]:
    """An endpoint file creation outside the one allowed path prefix."""
    from . import effects

    state = os.path.join(work, "state")
    os.makedirs(state)
    before = {"state/allowed.txt": {
        "type": "regular", "mode": "0644", "bytes": 1,
        "digest": "sha256:" + "a" * 64}}
    after = dict(before)
    after["state/spill.txt"] = {
        "type": "regular", "mode": "0644", "bytes": 1,
        "digest": "sha256:" + "b" * 64}
    allowances = [{"path": "state/allowed.txt",
                   "absolute": os.path.join(state, "allowed.txt"),
                   "watch": "state"}]
    changes = effects.compare(before, after, allowances, work)
    verdict = effects.classify(changes, [])
    breaches = [row for row in changes if not row["allowed"]]
    if verdict != effects.BREACH or not breaches:
        return MISSED, "accepted an out-of-envelope endpoint file creation"
    return DETECTED, "%s %s" % (breaches[0]["change"], breaches[0]["path"])


def _probe_replay_changed_executable(runs_root: str, run_id: str,
                                     work: str) -> Tuple[str, str]:
    """Replay from unchanged declared inputs but a changed step executable.

    The executable is intentionally undeclared: replay names that bounded
    input gap and still must not call changed output a match.
    """
    from . import replay, runner, spec as specmod

    # A featureless fixture isolates the replay verdict from stateful feature
    # baselines. It deliberately changes only an undeclared executable while
    # leaving the declared input fixed, which is a gap replay already reports.
    source = os.path.join(work, "source")
    fixture_runs = os.path.join(work, "runs")
    os.makedirs(source)
    script = os.path.join(source, "probe.sh")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho ORIGINAL-REPLAY-OUTPUT\n")
    os.chmod(script, 0o755)
    with open(os.path.join(source, "in.txt"), "w", encoding="utf-8") as fh:
        fh.write("unchanged declared input\n")
    spec_path = os.path.join(source, "replay-probe.json")
    _write_json(spec_path, {
        "schema": "hwbspec/v0.1",
        "run_class": "discovery",
        "features": [],
        "steps": [{"id": "01", "argv": ["./probe.sh"],
                   "inputs": ["in.txt"]}],
    })
    original = runner.execute(specmod.load(spec_path), [], fixture_runs)
    # Replay requires a preserved feature tree even for the empty set. The
    # runner quite reasonably stores no tree when there was no source to copy.
    os.makedirs(os.path.join(fixture_runs, original["run_id"], "features"))
    with open(script, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho CHANGED-REPLAY-OUTPUT\n")
    os.chmod(script, 0o755)

    try:
        man = replay.replay(fixture_runs, original["run_id"],
                            os.path.join(work, "campaigns"), source_dir=source)
    except replay.ReplayError as e:
        return DETECTED, "replay refused changed execution: %s" % e
    if man["verdict"] == replay.MATCHED:
        return MISSED, "reported `matched` after the replay executable changed output"
    return DETECTED, man["verdict"]


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
    "conform_artifact_mismatch": (
        "conform", _probe_conform_artifact_mismatch, False,
        "attempt byte counts and digests must agree with final stored output"),
    "confine_record_reach": (
        "confine", _probe_confine_record_reach, False,
        "a recorded write outside the feature's declared record channel is a breach"),
    "fidelity_missing_output": (
        "fidelity", _probe_fidelity_missing_output, False,
        "a record missing attempt output cannot answer what was produced"),
    "interfere_namespace_move": (
        "interfere", _probe_interfere_namespace_move, False,
        "extras[A] moving only when B is attached violates the relation"),
    "order_changed_run": (
        "order", _probe_order_changed_run, False,
        "different runs under two declared orders make order significant"),
    "blast_broken_survival": (
        "blast", _probe_blast_broken_survival, False,
        "a fault that breaks promised survival bits is blast damage"),
    "catch_missed_declared_drift": (
        "catch", _probe_catch_missed_declared_drift, False,
        "a declared content mutation expected to be caught must not disappear"),
    "efficacy_surviving_opposite": (
        "efficacy", _probe_efficacy_surviving_opposite, False,
        "a well-formed opposite that changes nothing is an inert feature"),
    "steady_moving_baseline": (
        "steady", _probe_steady_moving_baseline, False,
        "an unchanged control with an unallowed moving axis is unstable"),
    "effects_out_of_envelope": (
        "effects", _probe_effects_out_of_envelope, False,
        "an endpoint change outside every allowed path is a breach"),
    "replay_changed_executable": (
        "replay", _probe_replay_changed_executable, False,
        "a replay whose execution produces different output must not match"),
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

    probed = {row["checker"] for row in rows}
    declared = set(PUBLIC_VERDICT_ENGINES)
    for row in rows:
        if row["checker"] not in declared:
            row["verdict"] = ERRORED
            row["detail"] = ("probe names undeclared verdict engine %r"
                             % row["checker"])
    for checker in sorted(declared - probed):
        rows.append({
            "probe": "unprobed_%s" % checker,
            "checker": checker,
            "control": False,
            "why": "every public verdict engine requires a known-red probe",
            "verdict": UNPROBED,
            "detail": "declared public verdict engine has no probe",
        })

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
    unprobed = [r for r in rows if r["verdict"] == UNPROBED]
    detected = [r for r in rows if r["verdict"] == DETECTED and not r["control"]]
    return {
        "control_ok": control_ok,
        "detected": len(detected),
        "missed": missed,
        "errored": errored,
        "unprobed": unprobed,
        # Blind checkers, deduplicated: the actionable unit is the TOOL, not
        # the probe. Two probes missing on one tool is one blind tool.
        "blind_checkers": sorted({r["checker"] for r in missed}),
        "total": len([r for r in rows if not r["control"]]),
        "checker_coverage": "%d/%d" % (
            len(set(PUBLIC_VERDICT_ENGINES) - {r["checker"] for r in unprobed}),
            len(PUBLIC_VERDICT_ENGINES)),
    }
