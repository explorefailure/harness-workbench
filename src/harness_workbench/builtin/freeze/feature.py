"""Digest declared inputs; record drift against a baseline.

Annotates, does NOT block. The protection against invalid comparison lives
at comparison time -- a drifted run is still a run you can learn from, it
just cannot be paired with the baseline.

Baseline lives beside the spec, named after it: `<spec>.freeze.lock`.
First run creates it, so there is no separate bootstrap command.

Scoped PER SPEC, not per directory -- found by running two specs from one
folder and watching every input report drift against the other spec's
baseline. Any feature with persistent state has this problem.

DETACHING THIS FEATURE DOES NOT DELETE ITS BASELINE, and that is deliberate:
a baseline whose job is to outlive runs cannot be cleaned up on detach
without destroying the comparison it exists for. The residue is real, so it
is made VISIBLE instead -- every record names the baseline file and carries
its digest, so a run always says which baseline it was judged against and an
orphaned or stale lock is detectable from the record alone.
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
            import hashlib
            h = hashlib.sha256()
            with open(full, "rb") as fh:
                for c in iter(lambda: fh.read(65536), b""):
                    h.update(c)
            now[rel] = "sha256:" + h.hexdigest()
        else:
            now[rel] = "missing"

    if not os.path.isfile(lock):
        with open(lock, "w", encoding="utf-8") as fh:
            json.dump({"digests": now}, fh, indent=2, sort_keys=True)
        return {"baseline": "created", "drifted": False,
                "baseline_file": lock_rel, "baseline_digest": _digest(lock),
                "digests": now, "summary": "baseline created (%d input(s))" % len(now)}

    with open(lock, "r", encoding="utf-8") as fh:
        base = json.load(fh)["digests"]
    changed = sorted(k for k in set(base) | set(now) if base.get(k) != now.get(k))
    if changed:
        sys.stderr.write("[freeze] DRIFT: %s\n" % ", ".join(changed))
    return {"baseline": "compared", "drifted": bool(changed), "changed": changed,
            "baseline_file": lock_rel, "baseline_digest": _digest(lock),
            "digests": now,
            "summary": ("drifted: %s" % ", ".join(changed)) if changed else "inputs match baseline"}
