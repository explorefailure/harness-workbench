"""The spec: the unit of work.

A spec is JSON. The runner never imports Python to read one, so a spec you
do not trust can still be inspected. For matrices, write a generator that
*emits* a spec.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .canon import digest_obj

SCHEMA = "hwbspec/v0.1"
# Old-prefix specs still load: the preserved spec.json inside every
# pre-rename run says hbspec/v0.1, and refusing it would make those runs
# unreproducible for a reason that is purely cosmetic.
READABLE_SCHEMAS = (SCHEMA, "hbspec/v0.1")
RUN_CLASSES = ("discovery", "calibration", "confirmation")

# THE DIGEST RULE: digest what DETERMINES the work; exclude what only makes
# a CLAIM about it.
#
# `spec_digest` is the experiment's identity, so anything that can change
# what happens belongs inside it -- including fields that are forward-looking
# rather than currently active. `gate_budget_ms` does nothing while gates are
# dormant and is still digested, deliberately: it will bound behaviour when
# they activate, and excluding it now would mean changing digest semantics
# later, which is the churn worth avoiding. `step_timeout_ms` is digested for
# the plainer reason that a bound can kill a step that would have passed.
#
# `replicates` is the only exclusion and the rule is what justifies it: it
# asserts a relationship to another run and cannot alter this one. Including
# it was also self-defeating -- the plan requires a replicate to share its
# target's digest, which is impossible if making the claim changes the digest.
DIGEST_EXCLUDE = ("replicates",)


class SpecError(Exception):
    """Malformed spec. A harness failure, not a step failure."""


def _reject_json_constant(value: str):
    """Python accepts NaN/Infinity as an extension; the spec is JSON."""
    raise ValueError("non-finite number %s is not valid JSON" % value)


def _safe_component(value: Any, field: str) -> str:
    """Validate a name used as one directory component in the run store.

    Unicode names are valid.  Separators, dot segments, and NUL are not:
    those turn a declarative id into a path outside its assigned namespace.
    Both separator spellings are refused so a spec cannot become unsafe when
    moved between POSIX and Windows.
    """
    if not isinstance(value, str) or not value:
        raise SpecError("%s must be a non-empty string, got %r" % (field, value))
    if value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise SpecError("%s %r is not a filesystem-safe name" % (field, value))
    return value


class Step:
    __slots__ = ("id", "argv", "inputs")

    def __init__(self, id: str, argv: List[str], inputs: List[str]):
        self.id = id
        self.argv = argv
        self.inputs = inputs

    def as_record(self) -> Dict[str, Any]:
        return {"id": self.id, "argv": list(self.argv)}


class FeatureRef:
    __slots__ = ("name", "config", "action")

    def __init__(self, name: str, config: Dict[str, Any], action: Optional[str]):
        self.name = name
        self.config = config
        self.action = action


class Spec:
    __slots__ = ("path", "dir", "raw", "digest", "run_class", "features",
                 "env", "steps", "gate_budget_ms", "step_timeout_ms",
                 "seam_timeout_ms", "replicates", "features_root")

    def __init__(self, path: str, raw: Dict[str, Any]):
        self.path = os.path.abspath(path)
        self.dir = os.path.dirname(self.path)
        self.raw = raw
        # See DIGEST_EXCLUDE above for the rule. Backward compatible:
        # removing an absent key changes nothing, so every digest written
        # before the exclusion existed is unaffected.
        self.digest = digest_obj({k: v for k, v in raw.items()
                                  if k not in DIGEST_EXCLUDE})
        self.run_class = raw.get("run_class", "discovery")
        self.env = list(raw.get("env", []))
        # Where this spec's features live, relative to the spec. Optional;
        # absent keeps the old `<spec dir>/features` default.
        #
        # DIGESTED, not excluded: it determines WHICH CODE RUNS, which is the
        # most experiment-changing thing in the file. It exists because the
        # alternative in practice was exporting $HWB_FEATURES before every
        # command -- an undeclared variable deciding the feature set, which
        # is the precise failure this design exists to prevent, and it had
        # become the documented way to use the tool.
        self.features_root = raw.get("features_root")
        self.gate_budget_ms = raw.get("gate_budget_ms")
        self.replicates = raw.get("replicates")
        # Bounds. Absent means unbounded, which is the historical behaviour
        # and stays the default -- a workbench that killed a slow model call
        # by surprise would be worse than one that hangs visibly.
        self.step_timeout_ms = raw.get("step_timeout_ms")
        self.seam_timeout_ms = raw.get("seam_timeout_ms")
        self.features = [
            FeatureRef(f["name"], f.get("config", {}), f.get("action"))
            for f in raw.get("features", [])
        ]
        self.steps = [
            Step(s["id"], list(s["argv"]), list(s.get("inputs", [])))
            for s in raw["steps"]
        ]

    def all_inputs(self) -> List[str]:
        seen: List[str] = []
        for s in self.steps:
            for i in s.inputs:
                if i not in seen:
                    seen.append(i)
        return seen


def load(path: str) -> Spec:
    if not os.path.isfile(path):
        raise SpecError("no such spec: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh, parse_constant=_reject_json_constant)
    except (OSError, ValueError) as e:
        raise SpecError("spec is not valid JSON: %s" % e)

    if not isinstance(raw, dict):
        raise SpecError("spec must be a JSON object")
    if raw.get("schema") not in READABLE_SCHEMAS:
        raise SpecError("spec schema must be one of %s, got %r"
                        % (", ".join(READABLE_SCHEMAS), raw.get("schema")))
    if raw.get("run_class", "discovery") not in RUN_CLASSES:
        raise SpecError("run_class must be one of %s" % (RUN_CLASSES,))

    features = raw.get("features", [])
    if not isinstance(features, list):
        raise SpecError("features must be a list")
    for i, feature in enumerate(features):
        if not isinstance(feature, dict) or "name" not in feature:
            raise SpecError("feature %d must be an object with a 'name'" % i)
        _safe_component(feature["name"], "feature %d name" % i)
        config = feature.get("config", {})
        if not isinstance(config, dict):
            raise SpecError("feature %r: config must be an object"
                            % feature["name"])

    env = raw.get("env", [])
    if (not isinstance(env, list) or
            any(not isinstance(name, str) or not name for name in env)):
        raise SpecError("env must be a list of non-empty strings")

    feature_root = raw.get("features_root")
    if feature_root is not None and not isinstance(feature_root, str):
        raise SpecError("features_root must be a string or null")

    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise SpecError("spec needs a non-empty 'steps' list")

    ids = set()
    for s in steps:
        if not isinstance(s, dict) or "id" not in s or "argv" not in s:
            raise SpecError("each step needs 'id' and 'argv'")
        step_id = _safe_component(s["id"], "step id")
        if (not isinstance(s["argv"], list) or not s["argv"] or
                any(not isinstance(arg, str) for arg in s["argv"]) or
                not s["argv"][0]):
            raise SpecError("step %r: argv must be a non-empty list" % s.get("id"))
        inputs = s.get("inputs", [])
        if (not isinstance(inputs, list) or
                any(not isinstance(item, str) for item in inputs)):
            raise SpecError("step %r: inputs must be a list of strings" % step_id)
        if step_id in ids:
            raise SpecError("duplicate step id %r" % step_id)
        ids.add(step_id)

    for key in ("step_timeout_ms", "seam_timeout_ms"):
        v = raw.get(key)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v <= 0):
            raise SpecError("%s must be a positive integer, got %r" % (key, v))

    replicates = raw.get("replicates")
    if replicates is not None:
        _safe_component(replicates, "replicates")

    return Spec(path, raw)


def validate_replicates(spec: Spec, root: str) -> None:
    """A reproduction claim must be checkable, or it is worse than absent.

    `replicates: <run_id>` asserts this run re-executes an existing frozen
    package. Unvalidated that is free text -- it reads as provenance and
    carries none. Three checks, all from the plan:

      * the target run must exist,
      * it must share this run's spec_digest (otherwise it reproduces
        something else),
      * it must not itself set `replicates` (no chains -- a claim about a
        claim cannot be resolved to an original).
    """
    target = spec.replicates
    if target is None:
        return
    # Keep the check at the use boundary as well as in load(): callers can
    # construct Spec directly, and no unvalidated id may reach a path join.
    target = _safe_component(target, "replicates")

    path = os.path.join(root, target, "record.json")
    if not os.path.isfile(path):
        raise SpecError(
            "replicates names %r, which is not a run under %s" % (target, root))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            other = json.load(fh)
    except ValueError as e:
        raise SpecError("replicates target %r has an unreadable record: %s"
                        % (target, e))

    if other.get("spec_digest") != spec.digest:
        raise SpecError(
            "replicates target %r ran a different spec (%s), this spec is %s"
            % (target, other.get("spec_digest"), spec.digest))
    if other.get("replicates"):
        raise SpecError(
            "replicates target %r is itself a replicate of %r -- point at the "
            "original run instead" % (target, other["replicates"]))
