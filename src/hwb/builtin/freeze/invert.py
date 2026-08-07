"""freeze, with its verdict inverted. Family 7's mutant.

The ONLY change is the answer to the question freeze exists to answer:
"did the declared inputs drift from the baseline?" Clean inputs report as
drifted; drifted inputs report as clean.

WELL-FORMED ON PURPOSE. This is a semantic mutant, not a fault. It returns
the same keys with the same types, digests the same files, writes the same
baseline, and cannot raise where the real feature would not. That is what
separates Family 7 from Family 2: blast asks whether the harness survives a
BROKEN feature, and efficacy asks whether anything downstream would notice a
feature that quietly decided the opposite. A mutant that crashes proves
nothing about the second question -- it just re-runs the first.

This mirrors the Change-Rule-Effect operator from the access-control
mutation literature, which inverts a rule's Permit/Deny and is valued for
producing almost no equivalent mutants: a surviving CRE mutant is a real
finding rather than noise, because a rule whose effect can be flipped
without consequence is a rule nothing consults.
"""
import hashlib, json, os, sys


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(65536), b""):
            h.update(c)
    return "sha256:" + h.hexdigest()


def on_spec_loaded(spec, ctx):
    stem = os.path.splitext(os.path.basename(spec.path))[0]
    lock = os.path.join(spec.dir, "%s.freeze.lock" % stem)
    lock_rel = os.path.relpath(lock, spec.dir)
    now = {}
    for rel in spec.all_inputs():
        full = os.path.join(spec.dir, rel)
        if os.path.isfile(full):
            h = hashlib.sha256()
            with open(full, "rb") as fh:
                for c in iter(lambda: fh.read(65536), b""):
                    h.update(c)
            now[rel] = "sha256:" + h.hexdigest()
        else:
            now[rel] = "missing"

    # The bootstrap path is left ALONE. There is no prior baseline here, so
    # there is no verdict to invert -- inverting it would mutate the
    # initialisation rather than the decision, and the campaign would be
    # measuring which code path ran. Family 7's protocol warms the baseline
    # first precisely so the mutant reaches the branch below.
    if not os.path.isfile(lock):
        with open(lock, "w", encoding="utf-8") as fh:
            json.dump({"digests": now}, fh, indent=2, sort_keys=True)
        return {"baseline": "created", "drifted": False,
                "baseline_file": lock_rel, "baseline_digest": _digest(lock),
                "digests": now,
                "summary": "baseline created (%d input(s))" % len(now)}

    with open(lock, "r", encoding="utf-8") as fh:
        base = json.load(fh)["digests"]
    changed = sorted(k for k in set(base) | set(now) if base.get(k) != now.get(k))

    # THE INVERSION, and the whole of it.
    drifted = not bool(changed)
    if drifted:
        sys.stderr.write("[freeze] DRIFT: (inverted verdict)\n")
    return {"baseline": "compared", "drifted": drifted,
            "changed": [] if changed else sorted(now),
            "baseline_file": lock_rel, "baseline_digest": _digest(lock),
            "digests": now,
            "summary": ("drifted: %s" % ", ".join(sorted(now))) if drifted
                       else "inputs match baseline"}
