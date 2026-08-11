"""Feature discovery, manifests, and load-time resolution.

Manifests are JSON so an entire feature set can be validated — powers,
seams, capabilities, version fit — WITHOUT importing any feature code.
Importing is arbitrary code execution; keeping resolution inert means you
can inspect a spec you do not trust.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from typing import Any, Dict, List, Optional

from .canon import digest_tree
from .seams import SEAM_ORDER, SEAM_POWERS, POWERS

SEAM_CONTRACT = "0.2.0"


class FeatureError(Exception):
    """Resolution failed. Loud, at load time, naming the culprit."""


def _reject_json_constant(value: str):
    raise ValueError("non-finite number %s is not valid JSON" % value)


class Manifest:
    __slots__ = ("name", "version", "power", "seams", "provides", "requires",
                 "record_key", "seam_contract", "root", "digest",
                 "self_attests", "inverts", "intent")

    def __init__(self, root: str, raw: Dict[str, Any]):
        self.root = root
        self.name = raw["name"]
        self.version = raw.get("version", "0.0.0")
        self.power = raw["power"]
        self.seams = list(raw["seams"])
        self.provides = list(raw.get("provides", []))
        self.requires = list(raw.get("requires", []))
        self.record_key = raw.get("record_key", self.name)
        self.seam_contract = raw.get("seam_contract", ">=0.2.0,<0.3.0")
        # A feature that publishes both a payload and a digest OF that
        # payload can have its own arithmetic checked. Declared rather than
        # detected, so the core verifies it generically and never learns
        # which features do this -- hardcoding a feature name here would
        # break the base-ignorance test, which is the point of having one.
        #   {"payload": "<extras key>", "digest": "<extras key>"}
        self.self_attests = raw.get("self_attests") or None
        # THE DECISION THIS FEATURE MAKES, plus a well-formed opposite of it.
        # Family 7 swaps `source` in at `seam` and requires the run to come
        # out different; a feature nothing depends on survives that and is
        # thereby shown to be inert.
        #   {"seam": "<seam>", "source": "invert.py", "decision": "<prose>"}
        #
        # DECLARED, never inferred. The base cannot know what "the opposite"
        # of an arbitrary feature means, and guessing would make the family
        # measure the guess. Absent means the feature claims no decision, and
        # Family 7 skips it rather than inventing one -- an untestable
        # feature is honest; a fabricated inversion is not.
        #
        # The author writing their own inversion IS the discipline: a feature
        # whose author cannot state its opposite has not decided what it does.
        self.inverts = raw.get("inverts") or None
        # WHY THIS FEATURE EXISTS -- "capability" (it does work the run
        # needs) or "instrument" (it exists to exercise the harness).
        #
        # The distinction was already in the tree before it had a name.
        # `timing` is installed alongside the working features and its own
        # docstring says it "proves seam dispatch and nothing else"; the
        # `meddler` built to show `interfere` could detect interference was
        # the same kind of thing and lived as a throwaway fixture inside a
        # test instead. One concept, two homes, no word for it -- so an
        # instrument feature could only be recognised by someone who already
        # knew, which is the condition every control here exists to remove.
        #
        # It matters to Family 7 specifically. An instrument feature is often
        # SUPPOSED to be inert, and inertness is the finding that family
        # reports. Without this, "inert as designed" and "inert and nobody
        # noticed" are the same row.
        self.intent = raw.get("intent") or None
        self.digest = digest_tree(root, skip=("FEATURE.json.lock",))


class Loaded:
    __slots__ = ("manifest", "module", "config", "status", "failed_at_step",
                 "error", "order", "breaches")

    def __init__(self, manifest: Manifest, module, config: Dict[str, Any], order: int):
        self.manifest = manifest
        self.module = module
        self.config = config
        self.order = order
        self.status = "ok"
        self.failed_at_step: Optional[str] = None
        self.error: Optional[str] = None
        # Extras namespaces this feature changed by hand rather than through
        # its declared return channel. Recorded, never enforced -- see the
        # confinement note in seams.Dispatcher.
        self.breaches: List[Dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self.manifest.name

    def as_record(self) -> Dict[str, Any]:
        return {
            "name": self.manifest.name,
            "version": self.manifest.version,
            "digest": self.manifest.digest,
            "power": self.manifest.power,
            "seams": list(self.manifest.seams),
            "provides": list(self.manifest.provides),
            # Recorded alongside `provides` so a reader can reconstruct the
            # dependency graph from the record alone. The blast campaign
            # needed it: breaking a provider legitimately changes its
            # consumers, and without the edge that reads as blast damage.
            "requires": list(self.manifest.requires),
            "order": self.order,
            "status": self.status,
            "failed_at_step": self.failed_at_step,
            # Recorded so a checker reads the claim from the record itself
            # rather than needing the manifest -- the record names its own
            # configuration, and that includes what it promises about itself.
            "self_attests": self.manifest.self_attests,
            # The decision claim travels with the run, for the same reason
            # `self_attests` does: a reader checking whether this run's
            # features were ever proven load-bearing should not need the
            # feature tree to find out what they claimed to decide.
            "inverts": self.manifest.inverts,
            # Travels for the same reason: a reader deciding whether an inert
            # feature is a finding or a design needs to know what it was for,
            # and should not have to go and read its source to find out.
            "intent": self.manifest.intent,
            # Family 8's evidence. In the record rather than computed later,
            # because only the dispatcher can see a write happen -- by the
            # time a record is read, a reach-through and a declared write are
            # the same bytes.
            "breaches": list(self.breaches),
        }


# ------------------------------------------------------------------ location

def features_root(spec_dir: str, declared: Optional[str] = None) -> str:
    """$HWB_FEATURES, else the spec's declared root, else <spec dir>/features.

    Never the current working directory: a cwd-relative scan means running
    the same spec from a different folder silently changes the experimental
    condition, which is the one failure this design exists to prevent.

    `declared` resolves RELATIVE TO THE SPEC, like `steps[].inputs`, so it
    travels with the file rather than with whoever invoked it -- and it is
    digested, so two runs that read their features from different trees are
    not mistaken for the same experiment.

    The env var still wins, and must: the campaigns stage mutant feature
    trees and point runs at them, which is the one case where the caller
    legitimately knows better than the file. That override is applied by the
    harness itself and recorded in each campaign manifest, never typed by a
    human before an ordinary run -- which is what it had become.
    """
    env = os.environ.get("HWB_FEATURES")
    if env:
        return os.path.abspath(env)
    if declared == BUILTIN:
        return builtin_root()
    if declared:
        return os.path.normpath(os.path.join(spec_dir, declared))
    return os.path.join(spec_dir, "features")


BUILTIN = "hwb:builtin"


def builtin_root() -> str:
    """The features shipped inside the installed package.

    OPT-IN, NEVER A FALLBACK, and that distinction is the whole design.
    Making these the default when nothing else resolves would mean a
    mistyped `features_root`, or a run started from an unexpected directory,
    SUCCEEDS using code the author did not choose -- and succeeds quietly,
    because a run that works looks like a run that was right. Today an
    unresolvable root fails loudly and names what it could not find; that
    stays true. A spec asks for these by name or does not get them.

    The record is not weakened by shipping them. Every feature's source is
    digested individually into `features[].digest`, and each run preserves
    the source it used beside its record, so what actually executed is
    identified regardless of where it came from -- and `features_source`
    now records which of the four routes supplied it.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin")


def builtin_names() -> List[str]:
    """What ships in the box. Empty if the tree is missing, never an error."""
    root = builtin_root()
    try:
        return sorted(n for n in os.listdir(root)
                      if os.path.isfile(os.path.join(root, n, "FEATURE.json")))
    except OSError:
        return []


def unresolved_message(name: str, root: str) -> str:
    """Why a feature did not resolve, and the route that would supply it.

    NAMES THE ROUTE, NEVER TAKES IT. Falling back to the builtin tree here
    would defeat the opt-in rule in builtin_root() -- but staying silent
    about a tree that ships inside the package is its own defect, and it was
    the first thing a newcomer hit: the old text offered only $HWB_FEATURES,
    an override the harness sets for campaigns and which a human should not
    be typing before an ordinary run. So the suggestion is the DECLARATIVE
    route, which travels with the spec and is digested.

    Only suggested when the name actually exists in the builtin tree.
    Pointing someone at `hwb:builtin` for a feature that is not in it trades
    one unwinnable error for a second one.
    """
    msg = "feature %r not found under %s" % (name, root)
    builtins = builtin_names()
    if name in builtins:
        return (msg + "\n       it ships with hwb -- add "
                '"features_root": "hwb:builtin" to the spec to use it')
    if builtins:
        return (msg + "\n       features shipped with hwb: %s"
                '\n       (declare "features_root": "hwb:builtin" to use those)'
                % ", ".join(builtins))
    return msg


def source_of(spec_dir: str, declared: Optional[str] = None) -> str:
    """Which route supplied the features, for the record."""
    if os.environ.get("HWB_FEATURES"):
        return "env:HWB_FEATURES"
    if declared == BUILTIN:
        return BUILTIN
    if declared:
        return "spec:features_root"
    return "spec-adjacent"


def read_manifest(root: str) -> Manifest:
    path = os.path.join(root, "FEATURE.json")
    if not os.path.isfile(path):
        raise FeatureError("feature at %s has no FEATURE.json" % root)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh, parse_constant=_reject_json_constant)
    except (OSError, ValueError) as e:
        raise FeatureError("%s: FEATURE.json is not valid JSON: %s" % (root, e))
    if not isinstance(raw, dict):
        raise FeatureError("%s: FEATURE.json must be an object" % root)
    for key in ("name", "power", "seams"):
        if key not in raw:
            raise FeatureError("%s: FEATURE.json missing %r" % (root, key))
    if not isinstance(raw["name"], str) or not raw["name"]:
        raise FeatureError("%s: feature name must be a non-empty string" % root)
    if (raw["name"] in (".", "..") or "/" in raw["name"] or
            "\\" in raw["name"] or "\x00" in raw["name"]):
        raise FeatureError("%s: feature name %r is not filesystem-safe"
                           % (root, raw["name"]))
    if not isinstance(raw["power"], str):
        raise FeatureError("%s: power must be a string" % raw["name"])
    for key in ("seams", "provides", "requires"):
        value = raw.get(key, [])
        if (not isinstance(value, list) or
                any(not isinstance(item, str) or not item for item in value)):
            raise FeatureError("%s: %s must be a list of non-empty strings"
                               % (raw["name"], key))
    if not raw["seams"]:
        raise FeatureError("%s: seams must be a non-empty list" % raw["name"])
    if not isinstance(raw.get("seam_contract", ">=0.2.0,<0.3.0"), str):
        raise FeatureError("%s: seam_contract must be a string" % raw["name"])
    m = Manifest(root, raw)
    if m.power not in POWERS:
        raise FeatureError("%s: unknown power %r" % (m.name, m.power))
    if m.power == "grant":
        raise FeatureError(
            "%s declares the 'grant' power, which is specified but DORMANT. "
            "Gates activate only when a run must be prevented rather than "
            "annotated; see the plan's 'Gates - specified, deliberately "
            "dormant' section." % m.name)
    for seam in m.seams:
        if seam not in SEAM_ORDER:
            raise FeatureError("%s: unknown seam %r" % (m.name, seam))
        if m.power not in SEAM_POWERS[seam]:
            raise FeatureError(
                "%s: power %r is not permitted at seam %r (allowed: %s)"
                % (m.name, m.power, seam, ", ".join(SEAM_POWERS[seam])))
    _check_contract(m)
    _check_inverts(m)
    _check_intent(m)
    return m


INTENTS = ("capability", "instrument")


def _check_intent(m: Manifest) -> None:
    """A declared intent must be one of the two, and it is checked HERE.

    Optional in the schema, exactly like `inverts`: a synthesised feature in
    a test has no need to declare why it exists. What is NOT optional is
    that an installed feature declares it, and that is a property of the
    population rather than of the file, so the test suite owns it.

    Fail closed on a typo rather than treating an unrecognised value as
    absent -- `intent: "instrumnet"` would otherwise buy a silent exemption
    from the inversion requirement, which is the exact trade this field
    exists to make explicit.
    """
    if m.intent is None:
        return
    if m.intent not in INTENTS:
        raise FeatureError(
            "%s: unknown intent %r (expected one of: %s)"
            % (m.name, m.intent, ", ".join(INTENTS)))


def _check_inverts(m: Manifest) -> None:
    """A declared inversion must be usable, and it must be checked HERE.

    Validating at campaign time instead would mean a typo surfaces as
    "feature survived inversion" -- an inert-feature finding produced by a
    misspelled filename. The failure mode of a measurement is to quietly
    measure nothing, so this fails closed at load.
    """
    if m.inverts is None:
        return
    if not isinstance(m.inverts, dict):
        raise FeatureError("%s: 'inverts' must be an object" % m.name)
    for key in ("seam", "source", "decision"):
        if not m.inverts.get(key):
            raise FeatureError("%s: 'inverts' missing %r" % (m.name, key))
    seam = m.inverts["seam"]
    if seam not in m.seams:
        raise FeatureError(
            "%s: 'inverts' names seam %r, which this feature does not declare"
            % (m.name, seam))
    src = os.path.join(m.root, m.inverts["source"])
    if not os.path.isfile(src):
        raise FeatureError("%s: 'inverts' source %r does not exist"
                           % (m.name, m.inverts["source"]))


_RANGE = re.compile(r"^>=(?P<lo>[\d.]+),<(?P<hi>[\d.]+)$")


def _vt(v: str):
    return tuple(int(x) for x in v.split("."))


def _check_contract(m: Manifest) -> None:
    """Declared range, fails closed. '*' is rejected: a feature cannot
    honestly claim compatibility with contract versions that did not exist
    when it was written."""
    if m.seam_contract.strip() == "*":
        raise FeatureError("%s: seam_contract '*' is not allowed" % m.name)
    mo = _RANGE.match(m.seam_contract.replace(" ", ""))
    if not mo:
        raise FeatureError(
            "%s: seam_contract must look like '>=0.1.0,<0.2.0', got %r"
            % (m.name, m.seam_contract))
    lo, hi, cur = _vt(mo.group("lo")), _vt(mo.group("hi")), _vt(SEAM_CONTRACT)
    if not (lo <= cur < hi):
        raise FeatureError(
            "%s supports seam contract %s but the host is %s"
            % (m.name, m.seam_contract, SEAM_CONTRACT))


# ------------------------------------------------------------------ resolve

def resolve(spec) -> List[Loaded]:
    """Validate the whole set from manifests, THEN import."""
    root = features_root(spec.dir, getattr(spec, 'features_root', None))
    manifests: List[Manifest] = []
    for ref in spec.features:
        fdir = os.path.join(root, ref.name)
        if not os.path.isdir(fdir):
            raise FeatureError(unresolved_message(ref.name, root))
        manifest = read_manifest(fdir)
        if manifest.name != ref.name:
            raise FeatureError(
                "feature directory %r contains manifest named %r"
                % (ref.name, manifest.name))
        manifests.append(manifest)

    # capability presence + seam ordering, in one pass
    earliest: Dict[str, int] = {}
    for m in manifests:
        first = min(SEAM_ORDER[s] for s in m.seams)
        for cap in m.provides:
            earliest[cap] = min(earliest.get(cap, first), first)

    for m in manifests:
        consumer_at = min(SEAM_ORDER[s] for s in m.seams)
        for cap in m.requires:
            if cap not in earliest:
                raise FeatureError(
                    "%s requires capability %r, which nothing in this spec "
                    "provides" % (m.name, cap))
            if earliest[cap] >= consumer_at:
                raise FeatureError(
                    "%s requires %r but it is only provided at a seam that "
                    "fires no earlier than its own — the edge points backwards"
                    % (m.name, cap))

    loaded: List[Loaded] = []
    for order, (m, ref) in enumerate(zip(manifests, spec.features)):
        loaded.append(Loaded(m, _import(m), ref.config, order))
    return loaded


def _import(m: Manifest):
    path = os.path.join(m.root, "feature.py")
    if not os.path.isfile(path):
        raise FeatureError("%s: no feature.py" % m.name)
    spec_ = importlib.util.spec_from_file_location("hb_feature_%s" % m.name, path)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)          # trust boundary: crossed knowingly
    return mod
