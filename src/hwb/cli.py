"""hwb — run, show, ls, verify, diff.

`diff` compares two runs structurally and reports what it masked. It has no
scorer and needs none: the subject is the harness, so the checkable thing is
a relation between two runs, not a judgment of either one's output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import (blast as blastmod, catch as catchmod, commands,
               confine as confmod,
               conform, diff as diffmod, efficacy as effmod,
               effects as effectsmod, features,
               fidelity as fidmod, replay as replaymod, runner,
               interrupt as intmod, sensitivity as sensmod,
               spec as specmod, steady as steadymod,
               sweep as sweepmod)

DEFAULT_ROOT = os.environ.get("HWB_RUNS", "runs")
DEFAULT_SWEEPS = os.environ.get("HWB_SWEEPS", "sweeps")
DEFAULT_BLASTS = os.environ.get("HWB_BLASTS", "blasts")
DEFAULT_CATCHES = os.environ.get("HWB_CATCHES", "catches")
DEFAULT_SENS = os.environ.get("HWB_SENSITIVITY", "sensitivity")
DEFAULT_EFFICACY = os.environ.get("HWB_EFFICACY", "efficacy")
DEFAULT_REPLAYS = os.environ.get("HWB_REPLAYS", "replays")
DEFAULT_STEADIES = os.environ.get("HWB_STEADIES", "steadies")
DEFAULT_EFFECTS = os.environ.get("HWB_EFFECTS", "effects")
DEFAULT_INTERRUPTS = os.environ.get("HWB_INTERRUPTS", "interrupts")


def _fail(msg: str) -> int:
    sys.stderr.write("hwb: %s\n" % msg)
    return 2


def cmd_run(args) -> int:
    try:
        sp = specmod.load(args.spec)
    except specmod.SpecError as e:
        return _fail(str(e))
    try:
        loaded = features.resolve(sp)
    except features.FeatureError as e:
        return _fail(str(e))

    try:
        record = runner.execute(sp, loaded, args.root)
    except runner.HarnessError as e:
        return _fail(str(e))

    broken = [f["name"] for f in record["features"] if f["status"] != "ok"]
    sys.stdout.write("%s  %s  %d step(s)  %s\n" % (
        record["run_id"], record["run_class"], len(record["steps"]),
        record["status"]))
    if broken:
        sys.stdout.write("  features failed: %s\n" % ", ".join(broken))
    for key, val in sorted(record["extras"].items()):
        summary = val.get("summary")
        if summary:
            sys.stdout.write("  %-10s %s\n" % (key, summary))
    return 0          # a harness that worked exits 0, whatever the steps did


def _run_dirs(root: str) -> List[str]:
    if not os.path.isdir(root):
        return []
    # Interrupted directories are evidence too.  The old record.json filter
    # made the exact failures ``hwb interrupt`` measures invisible to ls.
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)))


def _load_record(root: str, run_id: str):
    p = os.path.join(root, run_id, "record.json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_ls(args) -> int:
    rows = _run_dirs(args.root)
    if not rows:
        sys.stdout.write("no runs under %s\n" % args.root)
        return 0
    sys.stdout.write("%-30s %-13s %-11s %-6s %s\n" %
                     ("RUN", "CLASS", "STATE", "STEPS", "FEATURES"))
    for run_id in rows:
        life = intmod.inspect_state(os.path.join(args.root, run_id))
        r = life.get("record") or {}
        feats = ",".join(f["name"] for f in r.get("features", [])) or "-"
        steps = str(len(r["steps"])) if isinstance(r.get("steps"), list) else "-"
        sys.stdout.write("%-30s %-13s %-11s %-6s %s\n" % (
            run_id, r.get("run_class", "-"), life["state"], steps, feats))
    return 0


def cmd_show(args) -> int:
    d = os.path.join(args.root, args.run_id)
    if not os.path.isdir(d):
        return _fail("no such run: %s" % args.run_id)
    life = intmod.inspect_state(d)
    r = life.get("record")
    if args.json:
        # Keep stdout parseable.  Prefixing a lifecycle line here would turn a
        # valid JSON interface into prose; incomplete paths without a record
        # receive a derived lifecycle object instead.
        payload = r if r is not None else {
            "lifecycle": life["state"], "reasons": life["reasons"],
            "inventory": life["inventory"], "run_id": args.run_id,
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        if life["state"] != intmod.COMPLETE:
            sys.stderr.write("hwb: lifecycle %s; show is non-passing\n"
                             % life["state"])
        return 0 if life["state"] == intmod.COMPLETE else 1
    sys.stdout.write("lifecycle %s\n" % life["state"])
    if life["state"] != intmod.COMPLETE:
        for reason in life["reasons"]:
            sys.stdout.write("  %s\n" % reason)
        sys.stdout.write("  retained: %s\n" %
                         (", ".join(life["inventory"]) or "(empty directory)"))
    if r is None:
        return 1
    sys.stdout.write("run       %s\n" % r["run_id"])
    sys.stdout.write("class     %s\n" % r["run_class"])
    sys.stdout.write("status    %s\n" % r["status"])
    sys.stdout.write("spec      %s\n" % r["spec_digest"])
    sys.stdout.write("window    %s -> %s\n" % (r["started_at"], r["ended_at"]))
    sys.stdout.write("features  %s\n" % (", ".join(
        "%s@%s(%s)" % (f["name"], f["version"], f["status"])
        for f in r["features"]) or "-"))
    path = os.path.join(args.root, args.run_id, "attempts.jsonl")
    sys.stdout.write("\nattempts\n")
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            a = json.loads(line)
            # Provenance is the point of recording it: an attempt whose cause
            # is not shown is an attempt whose cause you will not check.
            # Absent (older records) prints nothing rather than a false "-",
            # because unrecorded is not the same as uncaused.
            why = ""
            if a.get("caused_by"):
                why = "  <- " + " ".join(
                    "%s:%s" % (f["feature"], f["i"]) for f in a["caused_by"])
            sys.stdout.write("  step %-4s n=%-3s exit=%-5s %sms%s\n" % (
                a["step_id"], a["n"], a["exit"], a["duration_ms"], why))

    timings = r.get("seam_timings") or {}
    if timings:
        sys.stdout.write("\nseam dispatch\n")
        for feat in sorted(timings):
            for seam in sorted(timings[feat]):
                cell = timings[feat][seam]
                sys.stdout.write("  %-10s %-16s %3d call(s)  %.3f ms\n" % (
                    feat, seam, cell["calls"], cell["total_ms"]))

    if r["extras"]:
        sys.stdout.write("\nextras\n")
        for k, v in sorted(r["extras"].items()):
            sys.stdout.write("  %s: %s\n" % (k, json.dumps(v, sort_keys=True)[:400]))
    return 0 if life["state"] == intmod.COMPLETE else 1


def cmd_verify(args) -> int:
    d = os.path.join(args.root, args.run_id)
    if not os.path.isdir(d):
        return _fail("no such run: %s" % args.run_id)
    life = intmod.inspect_state(d)
    res = life.get("integrity") or runner.verify(d)
    sys.stdout.write("%s  %s\n" % (args.run_id, life["state"]))
    for f in res["drifted"]:
        sys.stdout.write("  edited:  %s\n" % f)
    for f in res["missing"]:
        sys.stdout.write("  missing: %s\n" % f)
    for f in res.get("untracked", []):
        sys.stdout.write("  untracked: %s\n" % f)
    for f in res.get("unsupported", []):
        sys.stdout.write("  unsupported path type: %s\n" % f)
    if res.get("error"):
        sys.stdout.write("  integrity: invalid -- %s\n" % res["error"])

    # Integrity and conformance answer different questions: one asks whether
    # the bytes changed since they were written, the other whether what was
    # written satisfies the invariants at all. A record can be untampered
    # and still malformed.
    conforms = life["state"] in (intmod.RECOVERABLE, intmod.COMPLETE)
    if conforms:
        sys.stdout.write("  conforms: yes\n")
    else:
        sys.stdout.write("  conforms: NO -- %s\n" % "; ".join(life["reasons"]))
    return 0 if life["state"] == intmod.COMPLETE else 1


def cmd_interrupt(args) -> int:
    try:
        man = intmod.campaign(args.spec, args.root, args.interrupts,
                              timeout_seconds=args.child_timeout_seconds)
    except intmod.InterruptError as e:
        return _fail(str(e))

    sys.stdout.write("%s  %s\n" % (man["campaign_id"], man["verdict"]))
    sys.stdout.write("checkpoint protocol: %s\n\n" %
                     man["checkpoint_protocol"])
    sys.stdout.write("%-30s %-12s %-12s %-10s %s\n" %
                     ("CHECKPOINT", "EXPECTED", "OBSERVED", "CHILD", "SIGNAL"))
    for row in man["checkpoints"]:
        child = row["child"]
        sys.stdout.write("%-30s %-12s %-12s %-10s %s\n" % (
            row["checkpoint"], row["expected_state"], row["observed_state"],
            child["result"], child["signal"] or "-"))
        for violation in row["violations"]:
            sys.stdout.write("  VIOLATION: %s\n" % violation)
    sys.stdout.write("\nunobserved by this bounded campaign:\n")
    for item in man["unobserved"]:
        sys.stdout.write("  - %s\n" % item)
    return 0 if man["verdict"] == intmod.PASSED else 1


def cmd_diff(args) -> int:
    try:
        a = diffmod.load_run(args.root, args.a)
        b = diffmod.load_run(args.root, args.b)
        res = diffmod.compare(a, b)
    except diffmod.Incomparable as e:
        # Not a difference -- a refusal. Distinct exit code so a script
        # cannot read "these runs must not be compared" as "they differ".
        sys.stderr.write("hwb: refusing to compare -- %s\n" % e)
        sys.stderr.write("    a drifted run is still readable; it just "
                         "cannot be paired with a baseline it no longer "
                         "shares inputs with.\n")
        return 3

    sys.stdout.write("A  %s\nB  %s\n\n" % (args.a, args.b))

    # Two axes, reported separately and never collapsed: whether the HARNESS
    # behaved the same, and whether the WORK came out the same. `equivalent`
    # requires both, so the verdict can no longer say two runs are the same
    # when their outputs are not -- which is how a pair producing six
    # different sentences once passed as a determinism check.
    if res["harness_equivalent"]:
        # The mask is only referenced when it is actually printed. Under
        # --quiet there is no "mask below" to point at, and the old wording
        # pointed at it anyway.
        sys.stdout.write("harness: equivalent%s\n"
                         % ("" if args.quiet else " (under the mask below)"))
    else:
        sys.stdout.write("harness: %d difference(s)\n" % len(res["differences"]))
        for d in res["differences"]:
            sys.stdout.write("  %s\n" % d)

    if not res["output_known"]:
        sys.stdout.write("output:  UNKNOWN -- not compared\n")
        for d in res["output_differences"]:
            sys.stdout.write("  %s\n" % d)
    elif res["output_differences"]:
        sys.stdout.write("output:  %d step output(s) DIFFER\n"
                         % len(res["output_differences"]))
        for d in res["output_differences"]:
            sys.stdout.write("  %s\n" % d)
    else:
        sys.stdout.write("output:  identical\n")

    sys.stdout.write("\n%s\n" % ("equivalent" if res["equivalent"]
                                else "NOT equivalent"))

    if res["cost"] and not args.quiet:
        sys.stdout.write("\ncost (informational, not a difference)\n")
        for row in res["cost"]:
            sys.stdout.write("  %s\n" % row)

    if not args.quiet:
        sys.stdout.write("\nmasked (the noise floor of this comparison)\n")
        for m in res["masked"]:
            sys.stdout.write("  %s\n" % m)
    return 0 if res["equivalent"] else 1


def _load_sweep(sweeps_root: str, sweep_id: str):
    p = os.path.join(sweeps_root, sweep_id, "sweep.json")
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def cmd_sweep(args) -> int:
    try:
        man = sweepmod.run_sweep(args.spec, args.root, args.sweeps, args.mode)
    except sweepmod.SweepError as e:
        return _fail(str(e))
    except (specmod.SpecError, features.FeatureError) as e:
        return _fail(str(e))

    ran = [r for r in man["configurations"] if r.get("run_id")]
    skipped = [r for r in man["configurations"] if r.get("skipped")]
    sys.stdout.write("%s  mode=%s  %d ran, %d skipped\n" % (
        man["sweep_id"], man["mode"], len(ran), len(skipped)))
    for r in man["configurations"]:
        label = ",".join(r["config"]) or "(none)"
        if r.get("run_id"):
            # "ran" is not "worked": a config whose steps never started is
            # not evidence, and saying so here is what stops the analysis
            # downstream reasoning over nothing.
            flag = "" if r.get("executed", True) else "   [STEPS DID NOT RUN]"
            sys.stdout.write("  %-28s %s%s\n" % (label, r["run_id"], flag))
        elif r.get("skipped"):
            # Unrunnable by design is a result, not an error: it is the
            # load-time capability check refusing an invalid condition.
            sys.stdout.write("  %-28s skipped: %s\n" % (label, r["skipped"]))
        else:
            sys.stdout.write("  %-28s ERROR: %s\n" % (label, r.get("error")))
    sys.stdout.write("\nsweep %s\n" % os.path.join(args.sweeps, man["sweep_id"]))
    return 0


def cmd_interfere(args) -> int:
    man = _load_sweep(args.sweeps, args.sweep_id)
    if man is None:
        return _fail("no such sweep: %s" % args.sweep_id)
    res = sweepmod.interference(man, args.root)

    sys.stdout.write("relation: %s\n" % res["relation"])
    sys.stdout.write("checked:  %d pair(s)\n\n" % res["pairs_checked"])
    if not res["pairs_checked"]:
        sys.stdout.write("nothing to check -- a sweep needs singletons and "
                         "pairs (try --mode pairs)\n")
        return 0
    if res.get("unusable"):
        sys.stdout.write("excluded %d run(s) whose steps never executed:\n"
                         % len(res["unusable"]))
        for u in res["unusable"]:
            sys.stdout.write("  %s  %s\n"
                             % (",".join(u["config"]) or "(none)", u["run_id"]))
        sys.stdout.write("\n")
    if not res["findings"]:
        sys.stdout.write("no interference: every feature's namespace was "
                         "invariant under attaching another\n")
    else:
        sys.stdout.write("%d interference finding(s)\n" % len(res["findings"]))
        for f in res["findings"]:
            sys.stdout.write("  extras[%s] moved when %s attached\n"
                             % (f["feature"], f["perturbed_by"]))
            sys.stdout.write("    fields: %s\n" % ", ".join(f["fields"]))
            sys.stdout.write("    %s (alone) vs %s\n"
                             % (f["alone"], f["with_other"]))
    sys.stdout.write("\nmasked: %s\n" % "; ".join(res["masked"]))
    return 1 if res["findings"] else 0


def cmd_blast(args) -> int:
    try:
        man = blastmod.campaign(args.spec, args.root, args.blasts,
                                args.seam_timeout_ms)
    except blastmod.BlastError as e:
        return _fail(str(e))
    except (specmod.SpecError, features.FeatureError) as e:
        return _fail(str(e))

    res = blastmod.summarise(man)
    sys.stdout.write("%s  baseline %s  %d injection(s)\n\n"
                     % (man["campaign_id"], man["baseline_run"], res["total"]))
    sys.stdout.write("%-9s %-15s %-11s %-8s %s\n" %
                     ("FEATURE", "SEAM", "FAULT", "POWER", "SURVIVED"))
    for r in man["injections"]:
        if r.get("skipped"):
            continue
        bits = []
        for k, label in (("completed", "run"), ("conforms", "record"),
                         ("others_intact", "others"),
                         ("steps_retained", "steps")):
            bits.append(("%s" % label) if r.get(k) else ("!%s" % label))
        sys.stdout.write("%-9s %-15s %-11s %-8s %s   [%s]\n" % (
            r["feature"], r["seam"], r["fault"], r["power"],
            " ".join(bits), r.get("feature_status", "?")))

    sys.stdout.write("\n")
    if not res["findings"]:
        sys.stdout.write("per-power failure semantics held for every "
                         "injection\n")
    else:
        sys.stdout.write("%d violation(s) of the powers taxonomy\n"
                         % len(res["findings"]))
        for f in res["findings"]:
            sys.stdout.write("  %s/%s/%s broke: %s\n" % (
                f["feature"], f["seam"], f["fault"], ", ".join(f["violated"])))
            if f.get("others_moved"):
                sys.stdout.write("    disturbed: %s\n"
                                 % ", ".join(f["others_moved"]))
    return 1 if res["findings"] else 0


def cmd_catch(args) -> int:
    try:
        man = catchmod.campaign(args.spec, args.root, args.catches)
    except catchmod.CatchError as e:
        return _fail(str(e))
    except (specmod.SpecError, features.FeatureError) as e:
        return _fail(str(e))

    res = catchmod.summarise(man)
    sys.stdout.write("%s\n\nfault model (declared, because a catch rate "
                     "without one is meaningless)\n" % man["campaign_id"])
    for name, m in sorted(man["fault_model"].items()):
        sys.stdout.write("  %-18s expect %-8s %s\n"
                         % (name, m["expected"], m["why"]))

    sys.stdout.write("\n%-18s %-24s %-9s %s\n"
                     % ("MUTATION", "INPUT", "EXPECT", "DETECTED BY"))
    for r in man["results"]:
        sys.stdout.write("%-18s %-24s %-9s %s\n" % (
            r["mutation"], r["input"][:24], r.get("expected", "-"),
            r.get("detected_by") or "-"))

    sys.stdout.write("\ncaught %s   false alarms %d   correctly ignored %d\n"
                     % (res["catch_rate"] or "n/a", res["false_alarms"],
                        res["correctly_ignored"]))
    for n in res["notes"]:
        sys.stdout.write("  %s: %s on %s -- %s\n"
                         % (n["verdict"], n["mutation"], n["input"], n["why"]))
    return 1 if (res["missed"] or res["false_alarms"]) else 0


def cmd_fidelity(args) -> int:
    d = os.path.join(args.root, args.run_id)
    if not os.path.isdir(d):
        return _fail("no such run: %s" % args.run_id)
    res = fidmod.assess(d)
    c = res["counts"]
    sys.stdout.write("%s\n\n" % args.run_id)
    mark = {fidmod.ANSWERED: "yes", fidmod.PARTIAL: "part", fidmod.UNANSWERED: "NO"}
    for r in res["questions"]:
        sys.stdout.write("%-4s %-38s %s\n"
                         % (mark[r["verdict"]], r["question"], r["detail"]))
    sys.stdout.write("\n%d answered, %d partial, %d unanswered (of %d)\n"
                     % (c[fidmod.ANSWERED], c[fidmod.PARTIAL],
                        c[fidmod.UNANSWERED], res["total"]))
    sys.stdout.write("answerability only -- whether an answer is USEFUL is the "
                     "human half of this check\n")
    return 0


def cmd_sensitivity(args) -> int:
    try:
        man = sensmod.campaign(args.root, args.run_id, args.sensitivity)
    except sensmod.SensitivityError as e:
        return _fail(str(e))
    res = sensmod.summarise(man)

    sys.stdout.write("%s  subject %s\n\n" % (man["campaign_id"], args.run_id))
    sys.stdout.write("each probe builds a violation ONE checker must reject\n")
    sys.stdout.write("detail names the exact production boundary exercised\n\n")
    sys.stdout.write("%-28s %-9s %-10s %s\n"
                     % ("PROBE", "CHECKER", "VERDICT", "DETAIL"))
    for r in man["probes"]:
        tag = r["probe"] + (" *" if r["control"] else "")
        sys.stdout.write("%-28s %-9s %-10s %s\n"
                         % (tag, r["checker"], r["verdict"], r["detail"][:70]))
    sys.stdout.write("\n* = positive control\n")

    # The control is reported FIRST and separately, because a failed control
    # invalidates the table rather than adding a row to it.
    if not res["control_ok"]:
        sys.stdout.write("\nCONTROL FAILED -- the probe harness is broken; "
                         "no verdict above can be believed\n")
        return 1

    sys.stdout.write("\ndetected %d/%d\n" % (res["detected"], res["total"]))
    for r in res["errored"]:
        sys.stdout.write("  errored: %s -- %s\n" % (r["probe"], r["detail"]))
    for r in res["unprobed"]:
        sys.stdout.write("  UNPROBED: %s -- %s\n"
                         % (r["checker"], r["detail"]))
    for r in res["missed"]:
        sys.stdout.write("\n  BLIND: %s did not reject %s\n"
                         % (r["checker"], r["probe"]))
        sys.stdout.write("         %s\n" % r["detail"])
        sys.stdout.write("         why it must: %s\n" % r["why"])
    if res["blind_checkers"]:
        sys.stdout.write("\nfor these checkers, a passing verdict and a "
                         "verdict it cannot reach are the same output: %s\n"
                         % ", ".join(res["blind_checkers"]))
    sys.stdout.write("checker coverage: %s\n" % res["checker_coverage"])
    return 1 if (res["missed"] or res["errored"] or res["unprobed"]) else 0


def cmd_efficacy(args) -> int:
    try:
        man = effmod.campaign(args.spec, args.root, args.efficacy,
                              seam_timeout_ms=args.seam_timeout_ms)
    except effmod.UnstableBaseline as e:
        # Reported as a refusal, not as zero kills. "Nothing was killed" and
        # "nothing could be measured" must never print the same way.
        sys.stdout.write("BASELINE UNSTABLE -- no mutant can be interpreted\n")
        sys.stdout.write("  %s\n" % e)
        return 2
    except effmod.EfficacyError as e:
        return _fail(str(e))

    res = effmod.summarise(man)
    st = man["stability"]
    sys.stdout.write("%s  control %s == %s\n\n"
                     % (man["campaign_id"], st["run_a"][-9:], st["run_b"][-9:]))
    sys.stdout.write("each mutant is a WELL-FORMED opposite; the run must differ\n\n")
    sys.stdout.write("%-10s %-11s %-9s %-16s %-10s %s\n"
                     % ("FEATURE", "INTENT", "POWER", "SEAM", "VERDICT",
                        "DETAIL"))
    for r in man["mutants"]:
        sys.stdout.write("%-10s %-11s %-9s %-16s %-10s %s\n"
                         % (r["feature"], r.get("intent") or "-", r["power"],
                            r.get("seam", "-"), r["verdict"],
                            r["detail"][:60]))

    sys.stdout.write("\nkilled %d/%d tested" % (len(res["killed"]), res["tested"]))
    if res["by_design"]:
        sys.stdout.write("   (%d instrument feature(s), inert by design)"
                         % len(res["by_design"]))
    sys.stdout.write("\n")
    # Printed apart from the count, and after it, because this one is a
    # finding rather than a footnote: a feature claiming to do work that
    # cannot say what it decides is unmeasured, not exempt.
    for r in res["undeclared"]:
        sys.stdout.write("\n  UNMEASURED: %s declares no decision and is not "
                         "an instrument\n" % r["feature"])

    for r in res["malformed"]:
        sys.stdout.write("\n  MALFORMED: %s -- %s\n" % (r["feature"], r["detail"]))
        sys.stdout.write("             a mutant that breaks tests Family 2, "
                         "not this one; the inversion needs fixing\n")
    for r in res["inert"]:
        sys.stdout.write("\n  INERT: %s survived inversion\n" % r["feature"])
        sys.stdout.write("         it decided the OPPOSITE of %s and the run "
                         "came out the same\n" % r["decision"])
        sys.stdout.write("         nothing downstream consults this feature "
                         "in this configuration\n")
    return 1 if (res["inert"] or res["malformed"]) else 0


def cmd_steady(args) -> int:
    try:
        man = steadymod.campaign(args.spec, args.root, args.steadies,
                                 repeats=args.repeats,
                                 allowance=args.allow or [])
    except steadymod.SteadyError as e:
        return _fail(str(e))

    res = steadymod.summarise(man)
    sys.stdout.write("%s  %s\n" % (man["campaign_id"], res["verdict"]))
    sys.stdout.write("runs: %s\n" % ", ".join(man["run_ids"]))
    sys.stdout.write("allowance: %s\n" % (", ".join(man["allowance"]) or "(none)"))
    for row in man["comparisons"]:
        sys.stdout.write("\n%s vs %s  %s\n"
                         % (row["run_a"], row["run_b"], row["verdict"]))
        for axis in row["moving_axes"]:
            tag = "allowed" if axis not in row["unallowed_axes"] else "MOVED"
            sys.stdout.write("  %-7s %s\n" % (tag, axis))
        if row["verdict"] == steadymod.UNINTERPRETABLE:
            sys.stdout.write("  %s\n" % row["detail"])
    if man["setup_error"]:
        sys.stdout.write("\nSETUP ERROR: %s\n" % man["setup_error"])
    if res["verdict"] == steadymod.STABLE:
        return 0
    if res["verdict"] == steadymod.UNSTABLE:
        return 1
    return 2


def _effect_endpoint(cell) -> str:
    if cell is None:
        return "absent"
    detail = cell["type"]
    if cell.get("digest"):
        detail += " " + cell["digest"]
    return detail


def cmd_effects(args) -> int:
    try:
        man = effectsmod.campaign(args.spec, args.root, args.effects_store,
                                  args.watch, args.allow or [])
    except effectsmod.EffectsError as e:
        return _fail(str(e))

    sys.stdout.write("%s  %s\n" % (man["campaign_id"], man["verdict"]))
    sys.stdout.write("run: %s\n" % (man["run_id"] or "(none)"))
    sys.stdout.write("sensor: %s\n" % man["sensor"]["name"])
    sys.stdout.write("watch: %s\n" % ", ".join(
        row["path"] for row in man["watched_roots"]))
    sys.stdout.write("allow: %s\n" % (", ".join(
        row["path"] for row in man["allowed_paths"]) or "(none)"))

    for row in man["changes"]:
        tag = "ALLOWED" if row["allowed"] else "BREACH"
        sys.stdout.write("\n%-7s %-15s %s\n"
                         % (tag, row["change"], row["path"]))
        sys.stdout.write("        before: %s\n" %
                         _effect_endpoint(row["before"]))
        sys.stdout.write("        after:  %s\n" %
                         _effect_endpoint(row["after"]))

    if man["setup_error"]:
        sys.stdout.write("\nSETUP ERROR: %s\n" % man["setup_error"])
    if man["instrument_error"]:
        sys.stdout.write("\nINSTRUMENT ERROR: %s\n" % man["instrument_error"])
    if man["sensor"]["unobserved_special_paths"]:
        sys.stdout.write("\nUNINTERPRETABLE special path(s): %s\n" % ", ".join(
            man["sensor"]["unobserved_special_paths"]))

    sys.stdout.write("\nunobserved by this sensor:\n")
    for item in man["sensor"]["unobserved"]:
        sys.stdout.write("  - %s\n" % item)
    if man["verdict"] == effectsmod.WITHIN_ENVELOPE:
        sys.stdout.write("\nWITHIN ENVELOPE under the declared endpoint "
                         "sensor -- not a global clean verdict\n")
        return 0
    return 1 if man["verdict"] == effectsmod.BREACH else 2


def cmd_replay(args) -> int:
    try:
        man = replaymod.replay(args.root, args.run_id, args.replays,
                               source_dir=args.in_dir)
    except replaymod.ReplayError as e:
        return _fail(str(e))

    sys.stdout.write("%s\n  original %s\n  replay   %s\n\n"
                     % (man["campaign_id"], man["original_run"],
                        man["replay_run"]))
    sys.stdout.write("%s\n" % man["verdict"])
    if man["refused"]:
        sys.stdout.write("  comparison refused: %s\n" % man["refused"])
    for d in man["differences"]:
        sys.stdout.write("  %s\n" % d)
    if man["verdict"] == replaymod.STATEFUL_ORIGIN:
        sys.stdout.write("  two replays (%s, %s) agree with each other and "
                         "not with the original,\n"
                         "  so the original is the outlier: it created state "
                         "that now exists\n"
                         % (man["replay_run"][-9:],
                            (man["second_replay_run"] or "?")[-9:]))
    if man["undeclared_step_files"]:
        sys.stdout.write("\nUNDECLARED: a step needs %s, which the spec does "
                         "not list in inputs --\n  no content-digest feature "
                         "can see it change\n"
                         % ", ".join(man["undeclared_step_files"]))

    # Printed on success as well as failure. Where the workload came from is
    # part of what a `matched` verdict means: one that needed a human to say
    # where the files were is weaker evidence than one the record located by
    # itself, and a reader who only sees "matched" cannot tell them apart.
    if man["workload_dir_supplied_by_hand"]:
        how = "supplied with --in"
    elif man["workload_dir_recoverable_from_record"]:
        how = "recovered from the record's spec_path"
    else:
        how = "defaulted to cwd -- the record could not supply it"
    sys.stdout.write("\nworkload: %s\n  (%s)\n" % (man["workload_dir"], how))
    return 0 if man["verdict"] == replaymod.MATCHED else 1


def cmd_order(args) -> int:
    man = _load_sweep(args.sweeps, args.sweep_id)
    if man is None:
        return _fail("no such sweep: %s" % args.sweep_id)
    if man.get("mode") != "permutations":
        return _fail("sweep %s ran in mode %r; order needs --mode permutations"
                     % (args.sweep_id, man.get("mode")))
    res = sweepmod.order_significance(man, args.root)

    sys.stdout.write("relation: %s\n" % res["relation"])
    if res["baseline"] is None:
        sys.stdout.write("\nnothing to check -- %s\n"
                         % "; ".join(res["unusable"]))
        return 0
    sys.stdout.write("baseline: %s  (the declared order)\n"
                     % ",".join(res["baseline"]["order"]))
    sys.stdout.write("compared: %d permutation(s)\n\n" % res["compared"])

    if not res["findings"]:
        sys.stdout.write("order is NOT significant -- every permutation "
                         "produced the same run\n")
        # Scope stated on the passing path, where it is easiest to overread.
        sys.stdout.write("scope: %s. A set with one wrap feature cannot show "
                         "an ordering\n       effect no matter how many "
                         "permutations run.\n" % res["scope"])
        return 0

    sys.stdout.write("order IS significant -- %d of %d permutation(s) differ\n"
                     % (len(res["findings"]), res["compared"]))
    sys.stdout.write("two runs whose feature lists differ only in sequence are "
                     "NOT the same configuration\n")
    for f in res["findings"]:
        sys.stdout.write("\n  %s  (vs %s)\n"
                         % (",".join(f["order"]), ",".join(f["against"])))
        for d in f["differences"]:
            sys.stdout.write("    %s\n" % d)
    return 1


def cmd_confine(args) -> int:
    d = os.path.join(args.root, args.run_id)
    try:
        res = confmod.assess(d)
    except confmod.ConfineError as e:
        return _fail(str(e))

    sys.stdout.write("%s\n\n" % args.run_id)
    sys.stdout.write("did each feature use only its declared record-power channel?\n")
    sys.stdout.write("scope: record extras; filesystem/process/network effects unmeasured\n\n")
    sys.stdout.write("%-12s %-10s %-12s %s\n"
                     % ("FEATURE", "POWER", "VERDICT", "DETAIL"))
    for r in res["features"]:
        sys.stdout.write("%-12s %-10s %-12s %s\n"
                         % (r["feature"], r["power"], r["verdict"],
                            r["detail"][:60]))

    sys.stdout.write("\n%d clean, %d breached, %d unmeasured\n"
                     % (len(res["clean"]), len(res["breached"]),
                        len(res["unmeasured"])))
    for r in res["breached"]:
        sys.stdout.write("\n  BREACH: %s declares %r and %s\n"
                         % (r["feature"], r["power"], r["detail"]))
        for b in r["breaches"][:4]:
            sys.stdout.write("          at %s%s -> extras[%s]\n"
                             % (b["seam"],
                                (" step %s" % b["step"]) if b["step"] else "",
                                b["namespace"]))
    if res["unmeasured"]:
        sys.stdout.write("\nunmeasured is NOT clean -- %s\n"
                         % ", ".join(r["feature"] for r in res["unmeasured"]))
    return 1 if res["breached"] else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hwb", description="run things, record what happened")
    p.add_argument("--root", default=DEFAULT_ROOT, help="run store (default: ./runs)")
    p.add_argument("--sweeps", default=DEFAULT_SWEEPS,
                   help="sweep store (default: ./sweeps; disjoint from run "
                        "store)")
    p.add_argument("--blasts", default=DEFAULT_BLASTS,
                   help="blast-campaign store (default: ./blasts; disjoint "
                        "from run store)")
    p.add_argument("--catches", default=DEFAULT_CATCHES,
                   help="catch-campaign store (default: ./catches; disjoint "
                        "from run store)")
    p.add_argument("--sensitivity", default=DEFAULT_SENS,
                   help="sensitivity-campaign store (default: ./sensitivity; "
                        "disjoint from run store)")
    p.add_argument("--efficacy", default=DEFAULT_EFFICACY,
                   help="efficacy-campaign store (default: ./efficacy; "
                        "disjoint from run store)")
    p.add_argument("--replays", default=DEFAULT_REPLAYS,
                   help="replay store (default: ./replays; disjoint from run "
                        "store)")
    p.add_argument("--steadies", default=DEFAULT_STEADIES,
                   help="steady-campaign store (default: ./steadies; "
                        "disjoint from run store)")
    p.add_argument("--effects-store", default=DEFAULT_EFFECTS,
                   help="effects-campaign store (default: ./effects; "
                        "disjoint from run store)")
    p.add_argument("--interrupts", default=DEFAULT_INTERRUPTS,
                   help="interruption-campaign store (default: ./interrupts; "
                        "disjoint from run store)")
    sub = p.add_subparsers(dest="cmd")

    registered = []

    def command(name: str):
        """Register only commands present in the shared public registry."""
        registered.append(name)
        return sub.add_parser(name, help=commands.metadata(name)["help"])

    r = command("run")
    r.add_argument("spec", help="path to a spec JSON file")
    r.set_defaults(func=cmd_run)

    l = command("ls")
    l.set_defaults(func=cmd_ls)

    s = command("show")
    s.add_argument("run_id", help="run id (see `hwb ls`)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_show)

    v = command("verify")
    v.add_argument("run_id", help="run id (see `hwb ls`)")
    v.set_defaults(func=cmd_verify)

    d = command("diff")
    d.add_argument("a", metavar="run_a", help="run id (see `hwb ls`)")
    d.add_argument("b", metavar="run_b", help="run id (see `hwb ls`)")
    d.add_argument("--quiet", action="store_true",
                   help="verdict only; omit cost and the mask")
    d.set_defaults(func=cmd_diff)

    sw = command("sweep")
    sw.add_argument("spec", help="path to a spec JSON file")
    sw.add_argument("--mode", default="pairs", choices=sweepmod.MODES,
                    help="which subsets to run (default: pairs)")
    sw.set_defaults(func=cmd_sweep)

    it = command("interfere")
    it.add_argument("sweep_id", help="sweep id (printed by `hwb sweep`)")
    it.set_defaults(func=cmd_interfere)

    bl = command("blast")
    bl.add_argument("spec", help="path to a spec JSON file")
    bl.add_argument("--seam-timeout-ms", type=int, default=400,
                    help="bound for the `hang` fault (default: 400)")
    bl.set_defaults(func=cmd_blast)

    ca = command("catch")
    ca.add_argument("spec", help="path to a spec JSON file")
    ca.set_defaults(func=cmd_catch)

    fi = command("fidelity")
    fi.add_argument("run_id", help="run id (see `hwb ls`)")
    fi.set_defaults(func=cmd_fidelity)

    se = command("sensitivity")
    se.add_argument("run_id", help="run id (see `hwb ls`)")
    se.set_defaults(func=cmd_sensitivity)

    ef = command("efficacy")
    ef.add_argument("spec", help="path to a spec JSON file")
    ef.add_argument("--seam-timeout-ms", type=int, default=400)
    ef.set_defaults(func=cmd_efficacy)

    st = command("steady")
    st.add_argument("spec", help="path to a spec JSON file")
    st.add_argument("--repeats", type=int, default=steadymod.DEFAULT_REPEATS,
                    help="unchanged runs to compare (default: 3; minimum: 2)")
    st.add_argument("--allow", action="append", default=[], metavar="AXIS",
                    help="permit one exact moving axis (repeatable; default: none)")
    st.set_defaults(func=cmd_steady)

    fx = command("effects")
    fx.add_argument("spec", help="path to a spec JSON file")
    fx.add_argument("--watch", action="append", required=True, metavar="SUBDIR",
                    help="existing spec-relative subdirectory to snapshot "
                         "(repeatable; no default)")
    fx.add_argument("--allow", action="append", default=[], metavar="PATH",
                    help="spec-relative allowed path/prefix inside a watch "
                         "(repeatable; default: none)")
    fx.set_defaults(func=cmd_effects)

    ix = command("interrupt")
    ix.add_argument("spec", help="path to a spec JSON file")
    ix.add_argument("--child-timeout-seconds", type=float,
                    default=intmod.DEFAULT_TIMEOUT_SECONDS,
                    help="bound for each child/checkpoint (default: 30)")
    ix.set_defaults(func=cmd_interrupt)

    od = command("order")
    od.add_argument("sweep_id", help="sweep id (printed by `hwb sweep`)")
    od.set_defaults(func=cmd_order)

    cf = command("confine")
    cf.add_argument("run_id", help="run id (see `hwb ls`)")
    cf.set_defaults(func=cmd_confine)

    rp = command("replay")
    rp.add_argument("run_id", help="run id (see `hwb ls`)")
    rp.add_argument("--in", dest="in_dir", default=None,
                    help="directory the workload's declared inputs live in "
                         "(the record cannot supply this; defaults to cwd)")
    rp.set_defaults(func=cmd_replay)
    expected = set(commands.cli_commands())
    if set(registered) != expected:
        missing = sorted(expected - set(registered))
        extra = sorted(set(registered) - expected)
        raise RuntimeError("CLI registry mismatch (missing=%r, extra=%r)"
                           % (missing, extra))
    return p


# Positionals that name something already in a store, not a file on disk.
# `diff` uses a/b because it takes two of them.
ID_ARGS = ("run_id", "sweep_id", "a", "b")


def misplaced_spec(args) -> Optional[str]:
    """Was a spec handed to a command that wanted an id?

    The commands split -- `run`, `sweep`, `blast`, `catch`, `steady`,
    `effects`, `interrupt`, `efficacy` take a
    SPEC; the rest take an ID produced by one of those -- and nothing said so
    at the point of confusion. Handing a spec to `confine` reported "no
    record at runs/bare.json", which is true and tells you nothing: the path
    is wrong because the ARGUMENT KIND is wrong, and the message described
    the symptom one layer below the mistake.

    Checked centrally rather than in each module, because each module
    receives an id and cannot see that a spec was ever typed -- by then the
    value is half of a path. Detected by the file existing, not by the
    suffix: a run id is never a path that resolves.
    """
    for name in ID_ARGS:
        val = getattr(args, name, None)
        if isinstance(val, str) and os.path.isfile(val):
            return ("%s takes a run id, not a spec file (got %s)\n"
                    "       specs are taken by: run, sweep, blast, catch, "
                    "steady, effects, interrupt, efficacy\n"
                    "       run one first, then pass the id it prints "
                    "(`hwb ls`)" % (args.cmd, val))
    return None


def invalid_store_id(args) -> Optional[str]:
    """Reject identifiers that could resolve outside their store.

    These are opaque ids produced by hwb, never paths.  Keep this beside the
    central argument-kind guard so every id-taking command gets the same
    portable component rule before its implementation joins the value to a
    store root.
    """
    for name in ID_ARGS:
        val = getattr(args, name, None)
        if val is None:
            continue
        try:
            specmod._safe_component(val, name.replace("_", " "))
        except specmod.SpecError as e:
            return str(e)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 0
    bad = misplaced_spec(args)
    if bad:
        sys.stderr.write("hwb: %s\n" % bad)
        return 2
    bad = invalid_store_id(args)
    if bad:
        return _fail(bad)
    return args.func(args)
