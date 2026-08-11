"""Does a record actually satisfy the invariants it claims?

The two invariants are the project's load-bearing claim, and until this
module existed they were prose enforced by care. Both were amended twice in
one day and nothing would have caught a mistake. A rule you must remember is
the weakest control that still counts as one; this makes the checkable parts
checkable.

Deliberately NOT a schema validator. Unknown keys are always ignored -- that
is the additive-contract rule, and a strict schema would break it on the
next additive field. What is asserted here is only what the invariants
actually promise:

  Invariant 1  attempts are flat, append-only, never collapsed; the record
               names its own configuration; each feature writes under its
               own key and nowhere else.
  Invariant 2  every recorded feature declares a live power, and no feature
               claims a power its seam does not permit.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .seams import SEAM_POWERS, SEAMS
from .runner import ATTEMPT_ARTIFACT_CONTRACT

RECORD_SCHEMA = "hwbrun/v0.1"
# The project was renamed hb -> hwb on 2026-08-06 (Open Decision 2). The
# format did not change, only the prefix, so records written under the old
# name are read unchanged rather than regenerated -- they are genuine
# historical runs against a real model, and rewriting them to look newer
# than they are would destroy the provenance the whole design exists to keep.
READABLE_RECORD_SCHEMAS = (RECORD_SCHEMA, "hbrun/v0.1")
STATUSES = ("completed", "harness_error", "denied", "refused")
FEATURE_STATUSES = ("ok", "failed")
LIVE_POWERS = ("observe", "annotate", "wrap")

REQUIRED = ("schema", "run_id", "run_class", "spec_digest", "seam_contract",
            "started_at", "ended_at", "status", "features", "gates", "steps",
            "extras")


class NonConforming(Exception):
    """A record does not satisfy an invariant. Names which one."""


def validate_record(record: Dict[str, Any],
                    attempts: List[Dict[str, Any]],
                    run_dir) -> None:
    """`run_dir` is REQUIRED -- pass a path, or `None` to mean it deliberately.

    It used to default to None, and that default caused a real bug: `hb
    verify` omitted the argument and silently ran only the weak checks,
    reporting "conforms: yes" for a record whose spec had been altered. The
    safe path was opt-in and the unsafe one was free, so forgetting produced
    a quieter, more confident answer -- the worst possible failure shape for
    a checker.

    Making it required does not stop anyone choosing the weak mode; it stops
    anyone choosing it by accident. `None` is now typed on purpose.

    What only the store can decide:
      * COLLAPSE. Four attempts replaced by one carrying `runs: 4` is
        indistinguishable from a step that ran once -- the stream is
        internally consistent either way. One directory of raw bytes exists
        per attempt, so the store is the independent evidence.
      * WHETHER THE DIGESTS ARE TRUE. spec_digest and features[].digest are
        unfalsifiable without the preserved spec and feature source.
    """
    _shape(record)
    _configuration(record)
    _flat_attempts(record, attempts)
    _namespaces(record)
    _self_attestation(record)
    if run_dir is not None:
        _against_the_store(record, attempts, run_dir)
        _preserved_spec(record, run_dir)
        _preserved_features(record, run_dir)


def _bad(inv: str, msg: str) -> None:
    raise NonConforming("Invariant %s: %s" % (inv, msg))


def _shape(r: Dict[str, Any]) -> None:
    for k in REQUIRED:
        if k not in r:
            _bad("1", "record is missing required key %r" % k)
    if r["schema"] not in READABLE_RECORD_SCHEMAS:
        _bad("1", "schema is %r, expected one of %s"
             % (r["schema"], ", ".join(READABLE_RECORD_SCHEMAS)))
    if r["status"] not in STATUSES:
        _bad("1", "status %r is not one of %s" % (r["status"], STATUSES))
    for key in ("features", "gates", "steps"):
        if not isinstance(r[key], list):
            _bad("1", "%s must be a list, got %s" % (key, type(r[key]).__name__))
    if not isinstance(r["extras"], dict):
        _bad("1", "extras must be an object")


def _configuration(r: Dict[str, Any]) -> None:
    """The record names its own configuration -- resolved, not requested.

    Without this a run cannot say what condition produced it, which is the
    whole reason the feature set is recorded rather than assumed.
    """
    seen = set()
    for f in r["features"]:
        for k in ("name", "version", "digest", "power", "seams", "status",
                  "failed_at_step", "order"):
            if k not in f:
                _bad("1", "feature entry %r is missing %r" % (f.get("name"), k))
        if f["name"] in seen:
            _bad("1", "feature %r recorded twice" % f["name"])
        seen.add(f["name"])
        if f["status"] not in FEATURE_STATUSES:
            _bad("1", "feature %r has status %r, expected one of %s"
                 % (f["name"], f["status"], FEATURE_STATUSES))
        if f["power"] not in LIVE_POWERS:
            # `grant` is specified but dormant; a record containing one means
            # the load-time refusal was bypassed.
            _bad("2", "feature %r records power %r, which is not live (%s)"
                 % (f["name"], f["power"], ", ".join(LIVE_POWERS)))
        for seam in f["seams"]:
            if seam not in SEAMS:
                _bad("2", "feature %r names unknown seam %r" % (f["name"], seam))
            if f["power"] not in SEAM_POWERS[seam]:
                _bad("2", "feature %r claims %r at seam %r, which permits %s"
                     % (f["name"], f["power"], seam,
                        ", ".join(SEAM_POWERS[seam])))
        if f["status"] == "ok" and f["failed_at_step"] is not None:
            _bad("1", "feature %r is ok but names a failed step" % f["name"])


def _flat_attempts(r: Dict[str, Any], attempts: List[Dict[str, Any]]) -> None:
    """Flat, append-only, never collapsed.

    The failure this guards is real and silent: a future change that nests
    attempts under a parent, or collapses repeats into a count, produces a
    record that still looks fine and has thrown away the history every
    comparison depends on.
    """
    step_ids = {s["id"] for s in r["steps"]}
    counters: Dict[str, int] = {}
    for a in attempts:
        if not isinstance(a, dict):
            _bad("1", "attempt is %s, not an object -- attempts are flat"
                 % type(a).__name__)
        for k in ("step_id", "n", "started", "duration_ms"):
            if k not in a:
                _bad("1", "attempt is missing %r" % k)
        for k, v in a.items():
            if isinstance(v, list) and k != "caused_by":
                _bad("1", "attempt key %r holds a list -- attempts must not "
                          "nest sub-attempts" % k)
        if a["step_id"] not in step_ids:
            _bad("1", "attempt names step %r, which the record does not list"
                 % a["step_id"])
        expected = counters.get(a["step_id"], 0)
        if a["n"] != expected:
            _bad("1", "step %r attempt numbering jumped %d -> %d; append-only "
                      "means no gaps and no collapsing"
                 % (a["step_id"], expected, a["n"]))
        counters[a["step_id"]] = expected + 1
        _provenance(a)


def _provenance(a: Dict[str, Any]) -> None:
    if "caused_by" not in a:
        return                     # absent = not recorded; see the plan
    if not isinstance(a["caused_by"], list) or not a["caused_by"]:
        _bad("1", "caused_by must be a non-empty list when present")
    for frame in a["caused_by"]:
        if not isinstance(frame, dict) or "feature" not in frame or "i" not in frame:
            _bad("1", "caused_by frame must be {feature, i}, got %r" % (frame,))
        if not isinstance(frame["i"], int) or frame["i"] < 0:
            _bad("1", "caused_by ordinal must be a non-negative int, got %r"
                 % (frame["i"],))


def _against_the_store(record: Dict[str, Any],
                       attempts: List[Dict[str, Any]], run_dir: str) -> None:
    """The raw bytes are the ground truth the stream can be checked against.

    One attempt directory is created per execution, before the attempt line
    is written, so the store cannot have FEWER directories than honest
    attempts. More directories than lines means lines were dropped or
    collapsed; a line with no directory means a line was invented.
    """
    import os

    from .canon import digest_file

    # No early return when `steps/` is absent. It used to skip the whole
    # check, which meant a run where nothing executed was never compared
    # against the store at all -- so a fully fabricated attempt stream passed.
    # Found on a real 3-step spec: the 1-step toy case had no steps/ directory
    # and the check quietly did not run.
    steps_root = os.path.join(run_dir, "steps")

    on_disk = set()
    if os.path.isdir(steps_root):
        for step_id in sorted(os.listdir(steps_root)):
            adir = os.path.join(steps_root, step_id, "attempts")
            if not os.path.isdir(adir):
                continue
            for n in os.listdir(adir):
                if n.isdigit():
                    on_disk.add((step_id, int(n)))

    # An attempt that declares it did not execute is the one line legitimately
    # without bytes behind it. Exempted by its own declaration, never by the
    # checker guessing from an absent directory.
    in_stream = {(a["step_id"], a["n"]) for a in attempts
                 if a.get("executed", True)}

    dropped = sorted(on_disk - in_stream)
    if dropped:
        _bad("1", "the store holds %d attempt(s) the stream does not list "
                  "(%s) -- attempts are append-only and never collapsed"
             % (len(dropped), ", ".join("%s#%d" % d for d in dropped[:5])))

    invented = sorted(in_stream - on_disk)
    if invented:
        # Not a collapse -- the opposite. A line with no bytes behind it.
        _bad("1", "the stream lists %d attempt(s) with nothing in the store "
                  "(%s)" % (len(invented), ", ".join("%s#%d" % d for d in invented[:5])))

    sealed = record.get("attempt_artifact_contract")
    if sealed not in (None, ATTEMPT_ARTIFACT_CONTRACT):
        _bad("1", "attempt_artifact_contract is %r, expected %r"
             % (sealed, ATTEMPT_ARTIFACT_CONTRACT))

    for attempt in attempts:
        if not attempt.get("executed", True):
            continue
        adir = os.path.join(run_dir, "steps", str(attempt["step_id"]),
                            "attempts", str(attempt["n"]))
        for stream in ("stdout", "stderr"):
            path = os.path.join(adir, stream + ".bin")
            size_key = stream + "_bytes"
            digest_key = stream + "_digest"
            if not os.path.isfile(path):
                _bad("1", "%s#%s has no stored %s.bin"
                     % (attempt["step_id"], attempt["n"], stream))
            if size_key in attempt and attempt[size_key] != os.path.getsize(path):
                _bad("1", "%s#%s records %s=%s but %s.bin contains %s bytes"
                     % (attempt["step_id"], attempt["n"], size_key,
                        attempt[size_key], stream, os.path.getsize(path)))
            if digest_key in attempt and attempt[digest_key] != digest_file(path):
                _bad("1", "%s#%s records %s=%s but %s.bin digests to %s"
                     % (attempt["step_id"], attempt["n"], digest_key,
                        attempt[digest_key], stream, digest_file(path)))
            if sealed == ATTEMPT_ARTIFACT_CONTRACT and \
                    (size_key not in attempt or digest_key not in attempt):
                _bad("1", "%s#%s is sealed under %s but lacks %s or %s"
                     % (attempt["step_id"], attempt["n"], sealed,
                        size_key, digest_key))


def _self_attestation(r: Dict[str, Any]) -> None:
    """Check a feature's own arithmetic, where it published enough to.

    A feature declaring `self_attests` promises that one extras key holds a
    payload and another holds a digest OF that payload, computed by the
    house canonical rule. That is checkable with no access to anything
    outside the record -- and it was going unchecked, so a feature could
    have published a wrong digest of its own data indefinitely.

    Generic on purpose: the check reads the declaration out of the record
    and never learns which features make it.
    """
    from .canon import digest_obj

    for f in r["features"]:
        decl = f.get("self_attests")
        if not decl:
            continue
        if not isinstance(decl, dict) or "payload" not in decl or "digest" not in decl:
            _bad("1", "feature %r declares a malformed self_attests: %r"
                 % (f["name"], decl))
        blob = r["extras"].get(f["name"])
        if not isinstance(blob, dict):
            if f["status"] == "ok":
                _bad("1", "feature %r declares self_attests but wrote no extras"
                     % f["name"])
            continue                    # it failed; absence is already recorded
        if decl["payload"] not in blob or decl["digest"] not in blob:
            if f["status"] != "ok":
                continue
            _bad("1", "feature %r declares self_attests %r but its extras "
                      "lack those keys" % (f["name"], decl))
        claimed = blob[decl["digest"]]
        actual = digest_obj(blob[decl["payload"]])
        if claimed != actual:
            _bad("1", "feature %r attests digest %s over its %r payload, but "
                      "that payload digests to %s -- the feature's own "
                      "arithmetic disagrees with its data"
                 % (f["name"], claimed, decl["payload"], actual))


def _preserved_spec(r: Dict[str, Any], run_dir: str) -> None:
    """Is `spec_digest` true, or merely claimed?

    Without the spec in the store the digest is unfalsifiable: a spec
    rewritten after its run still verified clean. Absent means the run
    predates preservation -- not recorded, not disproven.
    """
    import json
    import os

    from .spec import DIGEST_EXCLUDE
    from .canon import digest_obj

    path = os.path.join(run_dir, "spec.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except ValueError as e:
        _bad("1", "the preserved spec is not valid JSON: %s" % e)
    actual = digest_obj({k: v for k, v in raw.items() if k not in DIGEST_EXCLUDE})
    if actual != r["spec_digest"]:
        _bad("1", "record claims spec_digest %s but the preserved spec "
                  "digests to %s" % (r["spec_digest"], actual))


def _preserved_features(r: Dict[str, Any], run_dir: str) -> None:
    """Is `features[].digest` true, or merely claimed?

    This is the one that matters most here: the workbench exists to compare
    feature configurations across time, and until the code was preserved a
    past run could not prove which code it executed.
    """
    import os

    from .canon import digest_tree

    root = os.path.join(run_dir, "features")
    if not os.path.isdir(root):
        return
    for f in r["features"]:
        d = os.path.join(root, f["name"])
        if not os.path.isdir(d):
            _bad("1", "feature %r is recorded but its source was not preserved"
                 % f["name"])
        actual = digest_tree(d, skip=("FEATURE.json.lock",))
        if actual != f["digest"]:
            _bad("1", "feature %r is recorded at digest %s but the preserved "
                      "source digests to %s" % (f["name"], f["digest"], actual))


def _namespaces(r: Dict[str, Any]) -> None:
    """Each feature writes under its own key and nowhere else.

    This is the architecture's only prescribed channel between features, so
    a key belonging to no feature means something wrote outside its
    namespace -- the coupling the design exists to prevent.
    """
    names = {f["name"] for f in r["features"]}
    for key in r["extras"]:
        if key not in names:
            _bad("1", "extras contains %r, which is not an attached feature "
                      "-- a feature wrote outside its namespace" % key)
