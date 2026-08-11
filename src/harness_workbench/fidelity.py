"""Can a question be answered from the record alone?

Family 5. The plan asserts *"the record is readable without the tool"* --
which is a claim about a reader, not a property of a file, and therefore
unfalsifiable until someone fixes the reader and the questions.

So: a fixed question set, each with a resolver that touches ONLY the run
directory and never the harness that produced it. A question is answerable,
partial, or not answerable. The score is the shape of the record's coverage,
not a grade.

Two things this deliberately does not do. It does not judge whether an
answer is *useful* -- that is the human half, and pretending otherwise would
turn a rubric into a number nobody should trust. And it does not treat a
missing field as a failure of the run: records written before a field
existed answer fewer questions, which is a fact about the record's age and
is reported as such.

The value is that fidelity otherwise degrades in silence. Nothing fails when
a record stops being sufficient; you find out at the moment you need the
answer and it is not there.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

ANSWERED = "answered"
PARTIAL = "partial"
UNANSWERED = "unanswered"


def _steps(rec, ats, d):
    if not rec.get("steps"):
        return UNANSWERED, "no steps recorded"
    return ANSWERED, "%d step(s): %s" % (
        len(rec["steps"]), ", ".join("%s %s" % (s["id"], " ".join(s["argv"]))
                                     for s in rec["steps"])[:120])


def _outcome(rec, ats, d):
    if not ats:
        return UNANSWERED, "no attempts recorded"
    exits = [a.get("exit") for a in ats]
    return ANSWERED, "%d attempt(s), exits %s" % (len(exits), sorted(set(map(str, exits))))


def _why_attempt(rec, ats, d):
    """The question a flat counter cannot answer."""
    if not ats:
        return UNANSWERED, "no attempts"
    if any("caused_by" in a for a in ats):
        return ANSWERED, "attempts name the wrap feature that caused them"
    wraps = [f["name"] for f in rec.get("features", []) if f.get("power") == "wrap"]
    if not wraps:
        return ANSWERED, "no wrap feature attached; each step ran once"
    return UNANSWERED, ("provenance not recorded, and %s could have caused "
                        "repeats -- absence here is unknown, not 'once'"
                        % ", ".join(wraps))


def _outputs(rec, ats, d):
    root = os.path.join(d, "steps")
    if not os.path.isdir(root):
        return UNANSWERED, "no raw output preserved"
    n = sum(1 for dp, _, fs in os.walk(root) for f in fs if f == "stdout.bin")
    if n < len(ats):
        return PARTIAL, "%d of %d attempts have raw output" % (n, len(ats))
    return ANSWERED, "%d attempt output(s) preserved as raw bytes" % n


def _condition(rec, ats, d):
    feats = rec.get("features")
    if feats is None:
        return UNANSWERED, "the record does not name its configuration"
    if not feats:
        return ANSWERED, "no features attached (the control condition)"
    missing = [f["name"] for f in feats if not f.get("digest")]
    if missing:
        return PARTIAL, "features named but not fingerprinted: %s" % ", ".join(missing)
    return ANSWERED, "%d feature(s), each at a recorded version and digest" % len(feats)


def _which_code(rec, ats, d):
    """Recorded is not the same as verifiable."""
    root = os.path.join(d, "features")
    if not rec.get("features"):
        return ANSWERED, "no feature code was involved"
    if not os.path.isdir(root):
        return PARTIAL, ("digests recorded but the source was not preserved -- "
                         "the claim cannot be checked against anything")
    have = set(os.listdir(root))
    want = {f["name"] for f in rec["features"]}
    if want - have:
        return PARTIAL, "source missing for %s" % ", ".join(sorted(want - have))
    return ANSWERED, "every feature's source is preserved beside the record"


def _reproducible(rec, ats, d):
    if not os.path.isfile(os.path.join(d, "spec.json")):
        return PARTIAL, ("spec_digest recorded but the spec was not preserved; "
                         "what ran is not recoverable from the run alone")
    return ANSWERED, "the spec that ran is preserved beside the record"


def _environment(rec, ats, d):
    env = rec.get("env") or {}
    if "declared" not in env:
        return UNANSWERED, "no environment captured"
    decl = env["declared"]
    if not decl:
        return PARTIAL, ("nothing declared, so %d variable name(s) were "
                         "recorded without values -- the run's environment is "
                         "named but not known"
                         % len(env.get("undeclared_names") or []))
    unset = sorted(k for k, v in decl.items() if v is None)
    if unset:
        return PARTIAL, ("%d of %d declared variable(s) were unset (%s) -- "
                         "declared but not in effect"
                         % (len(unset), len(decl), ", ".join(unset)))
    return ANSWERED, "%d declared variable(s) captured with values" % len(decl)


def _cost(rec, ats, d):
    t = rec.get("seam_timings")
    if not t:
        if not rec.get("features"):
            return ANSWERED, "no features, so no dispatch cost to attribute"
        return UNANSWERED, "no per-seam timing recorded"
    return ANSWERED, "dispatch cost recorded for %d feature(s)" % len(t)


def _who_wrote(rec, ats, d):
    extras = rec.get("extras")
    if extras is None:
        return UNANSWERED, "no extras recorded"
    names = {f["name"] for f in rec.get("features", [])}
    stray = [k for k in extras if k not in names]
    if stray:
        return PARTIAL, "keys belonging to no attached feature: %s" % ", ".join(stray)
    return ANSWERED, "every extras key maps to an attached feature"


def _integrity(rec, ats, d):
    if not os.path.isfile(os.path.join(d, "integrity.json")):
        return UNANSWERED, "no integrity baseline; post-hoc edits undetectable"
    return ANSWERED, "a digest per file was written at close"


QUESTIONS: List[Tuple[str, str, Callable]] = [
    ("what_ran", "What commands ran, in what order?", _steps),
    ("what_happened", "How did they end?", _outcome),
    ("why_this_attempt", "Why did this attempt happen?", _why_attempt),
    ("what_was_produced", "What did each attempt output?", _outputs),
    ("what_condition", "Which features were attached?", _condition),
    ("which_code", "Which feature code actually executed?", _which_code),
    ("could_i_reproduce", "Could this run be reproduced?", _reproducible),
    ("what_environment", "What environment did it run in?", _environment),
    ("what_did_it_cost", "What did the harness cost?", _cost),
    ("who_wrote_this", "Which feature wrote each field?", _who_wrote),
    ("was_it_edited", "Has the record been altered since?", _integrity),
]


def assess(run_dir: str) -> Dict[str, Any]:
    rp = os.path.join(run_dir, "record.json")
    if not os.path.isfile(rp):
        raise FileNotFoundError(rp)
    with open(rp, "r", encoding="utf-8") as fh:
        rec = json.load(fh)
    ats: List[Dict[str, Any]] = []
    ap = os.path.join(run_dir, "attempts.jsonl")
    if os.path.isfile(ap):
        with open(ap, "r", encoding="utf-8") as fh:
            ats = [json.loads(l) for l in fh if l.strip()]

    rows = []
    for key, question, fn in QUESTIONS:
        try:
            verdict, detail = fn(rec, ats, run_dir)
        except Exception as e:                    # noqa: BLE001
            verdict, detail = UNANSWERED, "resolver failed: %s" % e
        rows.append({"key": key, "question": question,
                     "verdict": verdict, "detail": detail})

    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in (ANSWERED, PARTIAL, UNANSWERED)}
    return {"run_dir": run_dir, "questions": rows, "counts": counts,
            "total": len(rows)}
