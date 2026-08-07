"""Did each feature stay inside the power it declared?

Family 8. The manifest is what every other family trusts: blast picks its
fault library from `power`, interference excuses a consumer because of
`requires`, and the seam table refuses a power at the wrong seam. All of
that assumes the declaration describes what the feature actually does.

Nothing checked it. `extras_view()` hands every hook the live record, so an
`observe` feature -- whose return value is discarded by contract, and which
therefore appears to have no way into the record at all -- can write to any
namespace it likes. The declared power was an honour system one layer below
where it was being enforced.

This is NIST SP 800-192's property confinement checking, transposed:
"ensures that there is no exceptional permission allowed in addition to the
specified safety requirement." The permission here is the power, and the
exceptional one is the reach-through.

THE RULES, per power:

    observe    no write of any kind. Its return is ignored by contract, so
               any extras change under its hook arrived through ctx.
    annotate   may write its OWN namespace, and only by RETURNING a dict.
               A direct write to its own namespace bypasses the declared
               channel; a write to anyone else's is coupling nothing
               declares, which is precisely what `requires`/`provides` exist
               to make visible.
    wrap       no write either. A wrap's power is over EXECUTION -- how many
               times the step runs -- not over the record, and it has no
               declared channel into extras at all.

               This was unmeasured at first, on the grounds that a snapshot
               around a wrap cannot separate its writes from those of the
               features nested inside it. That was true of a snapshot around
               the WHOLE call and not of one around the wrap's own segments:
               the dispatcher defines the `counted()` closure, so it knows
               exactly when control passes downward. Measured between those
               boundaries and never across them -- see seams._wrap_one.

READS A RECORD, WRITES NOTHING. The evidence was collected during the run
(a reach-through and a declared write are identical bytes afterwards), so
this is a pure reader over `features[].breaches`.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

CLEAN = "clean"
BREACHED = "BREACHED"
UNMEASURED = "unmeasured"

# What each power is permitted to change, by hand, through ctx.
PERMITTED_BY_HAND: Dict[str, str] = {
    "observe": "nothing",
    "annotate": "nothing (its own namespace is written by RETURNING a dict)",
    "wrap": "nothing (its power is over execution, not over the record)",
}


class ConfineError(Exception):
    """The run could not be assessed."""


def assess(run_dir: str) -> Dict[str, Any]:
    path = os.path.join(run_dir, "record.json")
    if not os.path.isfile(path):
        raise ConfineError("no record at %s" % run_dir)
    with open(path, "r", encoding="utf-8") as fh:
        record = json.load(fh)

    rows: List[Dict[str, Any]] = []
    for f in record.get("features", []):
        power = f.get("power")
        # Absent means the run predates this check. NOT recorded is not the
        # same as nothing happened, and reporting an old run as `clean` would
        # be exactly the fail-open shape this project keeps finding.
        if "breaches" not in f:
            rows.append({"feature": f["name"], "power": power,
                         "verdict": UNMEASURED, "detail": "run predates "
                         "confinement recording -- not measured, not clean",
                         "breaches": []})
            continue
        br = f.get("breaches") or []
        if not br:
            rows.append({"feature": f["name"], "power": power,
                         "verdict": CLEAN, "detail": "wrote only through its "
                         "declared channel", "breaches": []})
            continue

        foreign = sorted({b["namespace"] for b in br if b["kind"] == "foreign"})
        own = sorted({b["namespace"] for b in br if b["kind"] == "own"})
        bits = []
        if foreign:
            bits.append("reached into %s" % ", ".join(foreign))
        if own:
            bits.append("wrote its own namespace by hand instead of returning")
        rows.append({"feature": f["name"], "power": power,
                     "verdict": BREACHED, "detail": "; ".join(bits),
                     "breaches": br})

    return {
        "run_id": record.get("run_id"),
        "features": rows,
        "breached": [r for r in rows if r["verdict"] == BREACHED],
        "unmeasured": [r for r in rows if r["verdict"] == UNMEASURED],
        "clean": [r for r in rows if r["verdict"] == CLEAN],
    }
