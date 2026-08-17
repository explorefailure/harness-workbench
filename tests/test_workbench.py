"""Detachability as a tested invariant, plus the load-time refusals.

stdlib unittest -- no test dependency, so `python3 -m unittest` just works.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from harness_workbench import conform, features, runner, spec as specmod   # noqa: E402

# The ONE feature tree. It lives inside the package so it ships with
# `pip install`; there is no second copy at the repo root to drift
# from, which is why the drift guard that used to sit here is gone.
REAL_FEATURES = os.path.join(ROOT, "src", "harness_workbench", "builtin")


def _installed_features():
    """Every feature on disk, discovered rather than listed.

    This was a hand-typed list, and it went stale the first time it could:
    `retry` was added and never appended, so the most recently written
    feature was the one with no detachment test, absent from the combination
    matrix, and missing from the name set the base-ignorance scan uses. The
    base turned out to be clean anyway -- which is the point. A control that
    silently stops covering the newest thing reports the same green as one
    that covers everything, and the newest thing is where the risk is.

    Deriving it means a feature cannot be added without also being tested
    for removability, with nobody having to remember.
    """
    return sorted(d for d in os.listdir(REAL_FEATURES)
                  if os.path.isdir(os.path.join(REAL_FEATURES, d))
                  and not d.startswith((".", "__")))


ALL = _installed_features()


def write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hb-test-")
        self.runs = os.path.join(self.tmp, "runs")
        self.feat_dir = os.path.join(self.tmp, "features")
        shutil.copytree(REAL_FEATURES, self.feat_dir)
        os.environ["HWB_FEATURES"] = self.feat_dir
        probe = os.path.join(self.tmp, "probe.sh")
        with open(probe, "w") as fh:
            fh.write("#!/bin/sh\necho hello\n")
        os.chmod(probe, 0o755)
        with open(os.path.join(self.tmp, "in.txt"), "w") as fh:
            fh.write("input\n")

    def tearDown(self):
        os.environ.pop("HWB_FEATURES", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spec(self, feats, name="s.json", steps=None):
        p = os.path.join(self.tmp, name)
        write(p, {
            "schema": "hwbspec/v0.1",
            "run_class": "discovery",
            "features": [f if isinstance(f, dict) else {"name": f} for f in feats],
            "steps": steps or [{"id": "01", "argv": ["./probe.sh"],
                                "inputs": ["in.txt"]}],
        })
        return p

    def run_spec(self, feats, **kw):
        sp = specmod.load(self.spec(feats, **kw))
        loaded = features.resolve(sp)
        return runner.execute(sp, loaded, self.runs)

    def assertValidRecord(self, rec):
        self.assertEqual(rec["status"], "completed")
        d = os.path.join(self.runs, rec["run_id"])
        self.assertTrue(os.path.isfile(os.path.join(d, "attempts.jsonl")))
        self.assertTrue(os.path.isfile(os.path.join(d, "integrity.json")))
        self.assertEqual(runner.verify(d)["state"], "clean")
        # The invariants, checked mechanically rather than by eye. Every test
        # that builds a record now asserts them, so a change that quietly
        # nests attempts or writes outside a feature namespace fails loudly
        # wherever it happens rather than wherever someone thought to look.
        attempts = [json.loads(l) for l
                    in read_text(os.path.join(d, "attempts.jsonl")).splitlines()
                    if l.strip()]
        try:
            conform.validate_record(rec, attempts, run_dir=d)
        except conform.NonConforming as e:
            self.fail("record does not conform -- %s" % e)


class TestDetachability(Base):
    def test_zero_features_produces_valid_record(self):
        rec = self.run_spec([])
        self.assertValidRecord(rec)
        self.assertEqual(rec["features"], [])
        self.assertEqual(rec["extras"], {})

    def test_each_feature_detached_leaves_valid_record(self):
        for drop in ALL:
            keep = [f for f in ALL if f != drop]
            if "receipt" in keep and "freeze" not in keep:
                continue          # receipt requires content-digest; see below
            with self.subTest(detached=drop):
                rec = self.run_spec(keep, name="d-%s.json" % drop)
                self.assertValidRecord(rec)
                self.assertNotIn(drop, rec["extras"])

    def test_combination_matrix(self):
        # Singletons are derived too, so a new feature is exercised alone --
        # the configuration where a feature that quietly depends on a
        # neighbour has nowhere to hide. Features declaring `requires` are
        # excluded because a singleton of one is refused at load by design,
        # which the load-time refusal tests already cover.
        solo = tuple((n,) for n in ALL
                     if not features.read_manifest(
                         os.path.join(REAL_FEATURES, n)).requires)
        combos = ((), ("freeze", "receipt"), tuple(ALL)) + solo
        for i, combo in enumerate(combos):
            with self.subTest(features=combo):
                rec = self.run_spec(list(combo), name="c%d.json" % i)
                self.assertValidRecord(rec)
                self.assertEqual(
                    sorted(f["name"] for f in rec["features"]), sorted(combo))


class TestInstalledManifests(unittest.TestCase):
    """Every feature ON DISK is well-formed, and its inversion is a decision.

    TestInvertsDeclaration checks the FIELD -- that a malformed `inverts` is
    refused at load. This checks the POPULATION: that each feature actually
    installed here has been classified, rather than defaulting into silence.

    An absent `inverts` is legal in the schema and should stay legal; a
    feature tree is not obliged to be invertible. What is not acceptable is
    that "this makes no decision" and "nobody wrote one yet" print the same
    way in a campaign, because the second is a hole in the measurement and
    the first is a fact about the feature. Declaring what a feature is FOR
    is what separates them -- the same move `catch` already makes by
    declaring its fault model instead of reporting a bare rate.

    An earlier revision of this class carried an EXEMPT map of feature names
    to prose reasons. It is gone, and the reason it is gone is worth stating:
    its single entry was not an exemption from a check at all, it was a
    statement that `timing` is an instrument rather than a capability, and
    the manifest had no way to say so. A list of excused names in a test file
    is a worse home for that than a field on the feature, because it does not
    travel with the feature and it grows one line per feature forever.
    """

    def test_every_feature_directory_has_a_loadable_manifest(self):
        for name in ALL:
            with self.subTest(feature=name):
                d = os.path.join(REAL_FEATURES, name)
                self.assertTrue(os.path.isfile(os.path.join(d, "FEATURE.json")),
                                "%s has no FEATURE.json" % name)
                m = features.read_manifest(d)
                self.assertEqual(m.name, name,
                                 "manifest name %r does not match its "
                                 "directory %r" % (m.name, name))

    def test_every_declared_seam_has_a_hook(self):
        """Parsed, not imported -- a manifest naming a seam its code does not
        implement is a silent no-op at that seam, and importing to find out
        would run the feature's module-level code to answer a question about
        its manifest."""
        import ast

        for name in ALL:
            with self.subTest(feature=name):
                d = os.path.join(REAL_FEATURES, name)
                m = features.read_manifest(d)
                tree = ast.parse(read_text(os.path.join(d, "feature.py")))
                defined = {n.name for n in tree.body
                           if isinstance(n, (ast.FunctionDef,
                                             ast.AsyncFunctionDef))}
                missing = sorted(set(m.seams) - defined)
                self.assertEqual(missing, [],
                                 "%s declares seam(s) %s with no hook"
                                 % (name, missing))

    def test_every_installed_feature_declares_its_intent(self):
        """Optional in the schema, required here. A feature nobody has to
        classify defaults into whichever reading suits the reader."""
        for name in ALL:
            with self.subTest(feature=name):
                m = features.read_manifest(os.path.join(REAL_FEATURES, name))
                self.assertIn(
                    m.intent, features.INTENTS,
                    "%s declares no intent. Say whether it is a 'capability' "
                    "(it does work the run needs) or an 'instrument' (it "
                    "exists to exercise the harness) -- the two have "
                    "different answers to whether being inert is a bug."
                    % name)

    def test_a_capability_feature_states_the_decision_it_makes(self):
        """The rule the old EXEMPT map was approximating.

        A capability feature is claimed to do work, so it must be able to
        say what would change if it decided the other way. An instrument
        feature carries no such obligation: `timing` is inert on purpose and
        an inversion of it would assert nothing.
        """
        for name in ALL:
            m = features.read_manifest(os.path.join(REAL_FEATURES, name))
            if m.intent != "capability":
                continue
            with self.subTest(feature=name):
                self.assertIsNotNone(
                    m.inverts,
                    "%s is declared a capability but states no decision. "
                    "Efficacy will report it as 'declares no decision', "
                    "which reads as a design and records a gap. Write "
                    "features/%s/invert.py, or declare it an instrument."
                    % (name, name))

    def test_an_instrument_feature_is_not_silently_load_bearing(self):
        """The inverse obligation, so `instrument` cannot become a hiding
        place: nothing may declare a capability edge that other features
        depend on and then disclaim being a capability."""
        for name in ALL:
            m = features.read_manifest(os.path.join(REAL_FEATURES, name))
            if m.intent != "instrument":
                continue
            with self.subTest(feature=name):
                self.assertEqual(
                    m.provides, [],
                    "%s calls itself an instrument but provides %s, which "
                    "other features may require -- that is a capability"
                    % (name, m.provides))


class TestLoadTimeRefusals(Base):
    def test_missing_capability_fails_at_load(self):
        # receipt requires content-digest; freeze is what provides it
        with self.assertRaises(features.FeatureError) as cm:
            self.run_spec(["receipt"], name="nocap.json")
        self.assertIn("content-digest", str(cm.exception))

    def test_capability_provided_after_consumer_fails_at_load(self):
        # move freeze to after_run: it would then provide the capability no
        # earlier than its consumer, so the edge points backwards
        man = os.path.join(self.feat_dir, "freeze", "FEATURE.json")
        m = read(man)
        m["seams"] = ["after_run"]
        # The inversion pointed at the seam this fixture just moved away from,
        # which is its own (correct) refusal. Dropped so the assertion below
        # is about the capability edge and not about whichever check happens
        # to run first.
        m.pop("inverts", None)
        write(man, m)
        with self.assertRaises(features.FeatureError) as cm:
            self.run_spec(["freeze", "receipt"], name="backwards.json")
        self.assertIn("backwards", str(cm.exception))

    def test_out_of_range_seam_contract_fails_at_load(self):
        man = os.path.join(self.feat_dir, "timing", "FEATURE.json")
        m = read(man); m["seam_contract"] = ">=9.0.0,<10.0.0"; write(man, m)
        with self.assertRaises(features.FeatureError) as cm:
            self.run_spec(["timing"], name="oldcontract.json")
        self.assertIn("seam contract", str(cm.exception))

    def test_wildcard_contract_rejected(self):
        man = os.path.join(self.feat_dir, "timing", "FEATURE.json")
        m = read(man); m["seam_contract"] = "*"; write(man, m)
        with self.assertRaises(features.FeatureError):
            self.run_spec(["timing"], name="star.json")

    def test_grant_power_is_rejected_while_dormant(self):
        man = os.path.join(self.feat_dir, "freeze", "FEATURE.json")
        m = read(man); m["power"] = "grant"; write(man, m)
        with self.assertRaises(features.FeatureError) as cm:
            self.run_spec(["freeze"], name="grant.json")
        self.assertIn("DORMANT", str(cm.exception))

    def test_power_not_permitted_at_seam_fails_at_load(self):
        man = os.path.join(self.feat_dir, "sample", "FEATURE.json")
        m = read(man); m["seams"] = ["after_run"]; write(man, m)
        with self.assertRaises(features.FeatureError) as cm:
            self.run_spec(["sample"], name="badseam.json")
        self.assertIn("not permitted", str(cm.exception))

    def test_unknown_feature_names_the_culprit(self):
        with self.assertRaises(features.FeatureError) as cm:
            self.run_spec(["nope"], name="nope.json")
        self.assertIn("nope", str(cm.exception))


class TestFailureSemantics(Base):
    def _annotator(self, source, name="bad"):
        d = os.path.join(self.feat_dir, name)
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": name, "version": "0.1.0", "power": "annotate",
               "seams": ["after_run"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write(source)

    def _assert_failed_annotation_is_inspectable(self, rec, name="bad"):
        self.assertValidRecord(rec)
        feature = [f for f in rec["features"] if f["name"] == name][0]
        self.assertEqual(feature["status"], "failed")
        self.assertEqual(rec["extras"][name]["error"]["type"],
                         "InvalidAnnotation")
        stored = read(os.path.join(self.runs, rec["run_id"], "record.json"))
        self.assertEqual(stored["extras"][name], rec["extras"][name])

    def _crasher(self, seam, power="annotate"):
        d = os.path.join(self.feat_dir, "boom")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "boom", "version": "0.1.0", "power": power,
               "seams": [seam], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def %s(*a):\n    raise RuntimeError('boom')\n" % seam)

    def test_annotate_crash_records_and_continues(self):
        self._crasher("after_run")
        rec = self.run_spec(["boom", "timing"], name="boom.json")
        self.assertValidRecord(rec)                     # run still completed
        boom = [f for f in rec["features"] if f["name"] == "boom"][0]
        self.assertEqual(boom["status"], "failed")
        self.assertIn("error", rec["extras"]["boom"])

    def test_wrap_crash_fails_the_step_not_the_run(self):
        self._crasher("around_step", power="wrap")
        rec = self.run_spec(["boom"], name="wrapboom.json")
        self.assertValidRecord(rec)
        self.assertTrue(rec["failed_steps"])
        self.assertEqual(rec["failed_steps"][0]["by"], "boom")

    def test_annotate_returning_non_dict_is_a_power_mismatch(self):
        d = os.path.join(self.feat_dir, "bad")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "bad", "version": "0.1.0", "power": "annotate",
               "seams": ["after_run"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def after_run(spec, ctx):\n    return 'not a dict'\n")
        rec = self.run_spec(["bad"], name="bad.json")
        bad = [f for f in rec["features"] if f["name"] == "bad"][0]
        self.assertEqual(bad["status"], "failed")
        self.assertIn("PowerMismatch", json.dumps(rec["extras"]["bad"]))

    def test_annotate_returning_non_finite_numbers_fails_without_killing_run(self):
        for label, expression in (("nan", "float('nan')"),
                                  ("infinity", "float('inf')"),
                                  ("negative-infinity", "-float('inf')")):
            with self.subTest(value=label):
                self._annotator(
                    "def after_run(spec, ctx):\n"
                    "    return {'value': %s}\n" % expression)
                rec = self.run_spec(["bad"], name="%s.json" % label)
                self._assert_failed_annotation_is_inspectable(rec)
                self.assertNotIn("value", rec["extras"]["bad"])

    def test_nested_non_canonical_annotation_is_rejected_at_the_seam(self):
        self._annotator(
            "def after_run(spec, ctx):\n"
            "    return {'outer': [{'valid': 1}, "
            "{'invalid': float('nan')}]}\n")
        rec = self.run_spec(["bad"], name="nested-invalid.json")
        self._assert_failed_annotation_is_inspectable(rec)
        self.assertNotIn("outer", rec["extras"]["bad"])

    def test_invalid_direct_mutation_is_rolled_back_and_recorded(self):
        self._annotator(
            "def after_run(spec, ctx):\n"
            "    own = ctx['extras'].setdefault('bad', {})\n"
            "    own['poison'] = {'nested': [float('inf')]}\n"
            "    return {'returned': 'must not be committed'}\n")
        rec = self.run_spec(["bad"], name="direct-invalid.json")
        self._assert_failed_annotation_is_inspectable(rec)
        self.assertNotIn("poison", rec["extras"]["bad"])
        self.assertNotIn("returned", rec["extras"]["bad"])
        feature = [f for f in rec["features"] if f["name"] == "bad"][0]
        self.assertTrue(any(b["namespace"] == "bad"
                            for b in feature["breaches"]))

    def test_invalid_direct_mutation_is_rolled_back_when_hook_also_crashes(self):
        self._annotator(
            "def after_run(spec, ctx):\n"
            "    ctx['extras'].setdefault('bad', {})['poison'] = float('nan')\n"
            "    raise RuntimeError('after poisoning extras')\n")
        rec = self.run_spec(["bad"], name="direct-invalid-crash.json")
        self.assertValidRecord(rec)
        feature = [f for f in rec["features"] if f["name"] == "bad"][0]
        self.assertEqual(feature["status"], "failed")
        self.assertEqual(rec["extras"]["bad"]["error"]["type"],
                         "RuntimeError")
        self.assertNotIn("poison", rec["extras"]["bad"])
        self.assertTrue(any(b["namespace"] == "bad"
                            for b in feature["breaches"]))

    def test_valid_unicode_and_finite_numeric_annotations_are_preserved(self):
        self._annotator(
            "def after_run(spec, ctx):\n"
            "    return {'text': '雪 café 🧪', "
            "'numbers': [-7, 0, 1.25, 100000000000000000000]}\n")
        rec = self.run_spec(["bad"], name="valid-canonical.json")
        self.assertValidRecord(rec)
        feature = [f for f in rec["features"] if f["name"] == "bad"][0]
        self.assertEqual(feature["status"], "ok")
        self.assertEqual(rec["extras"]["bad"]["text"], "雪 café 🧪")
        self.assertEqual(rec["extras"]["bad"]["numbers"],
                         [-7, 0, 1.25, 100000000000000000000])

    def test_residual_record_serialisation_failure_is_a_harness_error(self):
        sp = specmod.load(self.spec([], name="residual-invalid.json"))
        rec = runner.Recorder(self.runs, sp)
        rec.preserve([])
        rec.extras("outside-dispatch")["value"] = float("nan")
        with self.assertRaises(runner.HarnessError) as cm:
            rec.close("completed", [])
        self.assertIn("canonical", str(cm.exception))
        self.assertIn("record.json", str(cm.exception))

    def test_nonzero_exit_is_data_not_an_error(self):
        p = os.path.join(self.tmp, "fail.sh")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\nexit 3\n")
        os.chmod(p, 0o755)
        rec = self.run_spec([], name="fail.json",
                            steps=[{"id": "01", "argv": ["./fail.sh"]}])
        self.assertEqual(rec["status"], "completed")
        line = read_text(os.path.join(self.runs, rec["run_id"],
                                      "attempts.jsonl")).splitlines()[0]
        self.assertEqual(json.loads(line)["exit"], 3)


class TestRecordIntegrity(Base):
    def test_attempts_are_retained_not_collapsed(self):
        rec = self.run_spec([{"name": "sample", "config": {"n": 4}}],
                            name="n4.json")
        lines = read_text(os.path.join(self.runs, rec["run_id"],
                                       "attempts.jsonl")).splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual([json.loads(l)["n"] for l in lines], [0, 1, 2, 3])

    def test_verify_detects_edited_record(self):
        rec = self.run_spec(["timing"], name="v.json")
        d = os.path.join(self.runs, rec["run_id"])
        self.assertEqual(runner.verify(d)["state"], "clean")
        p = os.path.join(d, "record.json")
        obj = read(p); obj["run_class"] = "confirmation"
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(obj))
        res = runner.verify(d)
        self.assertEqual(res["state"], "drifted")
        self.assertIn("record.json", res["drifted"])

    def test_same_spec_runs_twice_without_collision(self):
        a = self.run_spec(["timing"], name="twice.json")
        b = self.run_spec(["timing"], name="twice.json")
        self.assertNotEqual(a["run_id"], b["run_id"])
        self.assertEqual(a["spec_digest"], b["spec_digest"])

    def test_a_declared_but_unset_variable_records_as_null(self):
        """`declared: {}` used to mean both "the spec declared nothing" and
        "the spec declared OLLAMA_HOST and it was unset" -- so the record
        could not say whether the author had considered the environment."""
        p = os.path.join(self.tmp, "envd.json")
        write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                  "features": [], "env": ["DEFINITELY_NOT_SET_XYZ", "HOME"],
                  "steps": [{"id": "01", "argv": ["./probe.sh"], "inputs": []}]})
        sp = specmod.load(p)
        rec = runner.execute(sp, features.resolve(sp), self.runs)
        d = rec["env"]["declared"]
        self.assertIn("DEFINITELY_NOT_SET_XYZ", d)
        self.assertIsNone(d["DEFINITELY_NOT_SET_XYZ"])   # declared, not set
        self.assertIsNotNone(d["HOME"])                  # declared and set

    def test_undeclared_env_records_names_without_values(self):
        os.environ["HB_TEST_SECRET"] = "swordfish"
        try:
            rec = self.run_spec([], name="env.json")
        finally:
            os.environ.pop("HB_TEST_SECRET")
        self.assertIn("HB_TEST_SECRET", rec["env"]["undeclared_names"])
        self.assertNotIn("swordfish", json.dumps(rec))


class TestSpecValidation(Base):
    def test_bad_schema_rejected(self):
        p = os.path.join(self.tmp, "bad.json")
        write(p, {"schema": "nope/v9", "steps": [{"id": "1", "argv": ["x"]}]})
        with self.assertRaises(specmod.SpecError):
            specmod.load(p)

    def test_duplicate_step_ids_rejected(self):
        p = os.path.join(self.tmp, "dup.json")
        write(p, {"schema": "hwbspec/v0.1",
                  "steps": [{"id": "1", "argv": ["x"]},
                            {"id": "1", "argv": ["y"]}]})
        with self.assertRaises(specmod.SpecError):
            specmod.load(p)

    def test_digest_is_stable_and_order_independent(self):
        a = {"schema": "hwbspec/v0.1", "run_class": "discovery",
             "steps": [{"id": "1", "argv": ["x"]}]}
        b = {"steps": [{"argv": ["x"], "id": "1"}], "run_class": "discovery",
             "schema": "hwbspec/v0.1"}
        pa, pb = os.path.join(self.tmp, "a.json"), os.path.join(self.tmp, "b.json")
        write(pa, a); write(pb, b)
        self.assertEqual(specmod.load(pa).digest, specmod.load(pb).digest)


class TestAttemptProvenance(Base):
    """The record must say WHICH mechanism caused an attempt.

    Without this, retry(sample(step)) and sample(retry(step)) are the same
    byte stream and the ordering experiment cannot be measured at all.
    """

    def _wrap(self, name, times):
        d = os.path.join(self.feat_dir, name)
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": name, "version": "0.1.0", "power": "wrap",
               "seams": ["around_step"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def around_step(step, run_step, ctx):\n"
                     "    for _ in range(%d):\n        run_step()\n" % times)

    def attempts(self, rec):
        p = os.path.join(self.runs, rec["run_id"], "attempts.jsonl")
        return [json.loads(l) for l in read_text(p).splitlines() if l.strip()]

    def test_no_wrap_features_means_no_caused_by_key(self):
        # absent, not empty: a reader must not confuse "no wraps ran" with
        # "provenance not recorded" -- which is exactly how the 11 pre-change
        # runs have to be read.
        rec = self.run_spec([], name="nowrap.json")
        for a in self.attempts(rec):
            self.assertNotIn("caused_by", a)

    def test_wrap_attempts_name_the_feature_that_caused_them(self):
        rec = self.run_spec([{"name": "sample", "config": {"n": 3}}],
                            name="prov.json")
        ats = self.attempts(rec)
        self.assertEqual(len(ats), 3)
        for i, a in enumerate(ats):
            self.assertEqual(a["caused_by"], [{"feature": "sample", "i": i}])

    def test_nesting_order_is_distinguishable(self):
        """The Stage 2b experiment, now measurable.

        Both orders run the step 4 times; only the causal stack differs.
        """
        self._wrap("twice", 2)
        outer_twice = self.run_spec(
            [{"name": "sample", "config": {"n": 2}}, "twice"], name="o1.json")
        outer_sample = self.run_spec(
            ["twice", {"name": "sample", "config": {"n": 2}}], name="o2.json")

        a1, a2 = self.attempts(outer_twice), self.attempts(outer_sample)
        self.assertEqual(len(a1), 4)
        self.assertEqual(len(a2), 4)

        # identical counts -- the old record could not tell these apart
        order1 = [[f["feature"] for f in a["caused_by"]] for a in a1]
        order2 = [[f["feature"] for f in a["caused_by"]] for a in a2]
        self.assertEqual(order1[0], ["twice", "sample"])
        self.assertEqual(order2[0], ["sample", "twice"])
        self.assertNotEqual(order1, order2)

    def test_causal_stack_ordinals_count_per_feature(self):
        self._wrap("twice", 2)
        rec = self.run_spec([{"name": "sample", "config": {"n": 2}}, "twice"],
                            name="ord.json")
        stacks = [a["caused_by"] for a in self.attempts(rec)]
        self.assertEqual(
            [(s[0]["i"], s[1]["i"]) for s in stacks],
            [(0, 0), (0, 1), (1, 0), (1, 1)])


class TestCapabilityResolution(Base):
    def test_receipt_binds_the_declared_provider_not_a_lookalike(self):
        """A decoy writing `digests` must not be picked up.

        The old implementation scanned every feature's extras for any dict
        with a "digests" key and took the first hit, so which provider won
        depended on dict order rather than the declared capability edge.
        """
        d = os.path.join(self.feat_dir, "decoy")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "decoy", "version": "0.1.0", "power": "annotate",
               "seams": ["on_spec_loaded"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def on_spec_loaded(spec, ctx):\n"
                     "    return {'digests': {'WRONG.txt': 'sha256:dead'}}\n")

        rec = self.run_spec(["decoy", "freeze", "receipt"], name="decoy.json")
        self.assertValidRecord(rec)
        bound = rec["extras"]["receipt"]["bound"]
        self.assertEqual(bound["inputs_from"], "freeze")
        self.assertNotIn("WRONG.txt", bound["inputs"])
        self.assertIn("in.txt", bound["inputs"])


class TestMeasurementSurfaces(Base):
    def test_seam_dispatch_is_timed_per_feature(self):
        # cost cannot be inferred from wall clock: a model call is seconds
        # and a dispatch is microseconds, so it must be measured in place.
        rec = self.run_spec(["timing", "freeze"], name="timed.json")
        t = rec["seam_timings"]
        self.assertIn("freeze", t)
        self.assertIn("on_spec_loaded", t["freeze"])
        self.assertEqual(t["freeze"]["on_spec_loaded"]["calls"], 1)
        self.assertGreaterEqual(t["freeze"]["on_spec_loaded"]["total_ms"], 0.0)

    def test_zero_features_records_no_timings(self):
        rec = self.run_spec([], name="notime.json")
        self.assertEqual(rec["seam_timings"], {})

    def test_timestamps_have_sub_second_resolution(self):
        rec = self.run_spec([], name="ts.json")
        self.assertRegex(rec["started_at"], r"\.\d{3}Z$")
        self.assertRegex(rec["ended_at"], r"\.\d{3}Z$")


class TestResidue(Base):
    def test_base_never_names_a_feature(self):
        """The base must stay ignorant of what attaches to it.

        Parsed, not grepped. A substring scan flags the English word
        "timings" inside a docstring and calls it coupling -- the documented
        weakness of static greps as a control. Only identifiers and non-doc
        string literals can express a real dependency, and only an exact
        match is one: `seam_timings` is not a reference to `timing`.
        """
        import ast

        names = set(ALL) | {"content-digest"}
        src = os.path.join(ROOT, "src", "harness_workbench")
        for fn in sorted(os.listdir(src)):
            if not fn.endswith(".py"):
                continue
            tree = ast.parse(read_text(os.path.join(src, fn)))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                    body = getattr(node, "body", None)
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstrings.add(id(body[0].value))

            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) not in docstrings and node.value in names:
                        found.add(node.value)
                elif isinstance(node, ast.Name) and node.id in names:
                    found.add(node.id)
                elif isinstance(node, ast.Attribute) and node.attr in names:
                    found.add(node.attr)
            self.assertEqual(
                found, set(),
                "base file %s names the feature/capability %s" % (fn, sorted(found)))

    def test_freeze_baseline_is_named_and_digested_in_the_record(self):
        """Detaching freeze leaves its lock file behind -- deliberately, since
        a baseline must outlive runs. The residue is made visible instead: the
        record always says which baseline it was judged against."""
        rec = self.run_spec(["freeze"], name="base.json")
        f = rec["extras"]["freeze"]
        self.assertEqual(f["baseline_file"], "base.freeze.lock")
        self.assertTrue(f["baseline_digest"].startswith("sha256:"))

        lock = os.path.join(self.tmp, "base.freeze.lock")
        self.assertTrue(os.path.isfile(lock))

        # detach freeze; the lock survives, and the record no longer claims
        # any baseline at all rather than silently implying a stale one
        rec2 = self.run_spec([], name="base.json")
        self.assertTrue(os.path.isfile(lock))
        self.assertEqual(rec2["extras"], {})


class WrapBase(Base):
    """Specs whose step fails the first N times, then succeeds."""

    def flaky(self, fail_times):
        p = os.path.join(self.tmp, "flaky.sh")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\nC=%s/.count\n"
                     "n=$(cat $C 2>/dev/null || echo 0); n=$((n+1)); echo $n > $C\n"
                     "if [ $n -le %d ]; then exit 1; fi\necho ok\n"
                     % (self.tmp, fail_times))
        os.chmod(p, 0o755)
        try:
            os.remove(os.path.join(self.tmp, ".count"))
        except OSError:
            pass
        return [{"id": "01", "argv": ["./flaky.sh"], "inputs": []}]

    def attempts(self, rec):
        p = os.path.join(self.runs, rec["run_id"], "attempts.jsonl")
        return [json.loads(l) for l in read_text(p).splitlines() if l.strip()]

    def causes(self, rec):
        return [[f["feature"] for f in a.get("caused_by", [])]
                for a in self.attempts(rec)]


class TestRetry(WrapBase):
    def test_retry_stops_at_first_success(self):
        rec = self.run_spec([{"name": "retry", "config": {"max": 5}}],
                            name="r1.json", steps=self.flaky(2))
        ats = self.attempts(rec)
        self.assertEqual([a["exit"] for a in ats], [1, 1, 0])

    def test_retry_exhausts_on_persistent_failure(self):
        rec = self.run_spec([{"name": "retry", "config": {"max": 3}}],
                            name="r2.json", steps=self.flaky(99))
        ats = self.attempts(rec)
        self.assertEqual(len(ats), 3)
        self.assertEqual([a["exit"] for a in ats], [1, 1, 1])

    def test_retry_does_not_rerun_a_passing_step(self):
        rec = self.run_spec([{"name": "retry", "config": {"max": 5}}],
                            name="r3.json", steps=self.flaky(0))
        self.assertEqual(len(self.attempts(rec)), 1)


class TestWrapOrdering(WrapBase):
    """Stage 2b: same two features, two experiments.

    The case that motivated `caused_by`: both orders can produce the SAME
    number of attempts with the SAME exit sequence, and differ only in what
    caused each one.
    """

    def test_the_two_orders_are_distinguishable(self):
        outer_retry = self.run_spec(
            [{"name": "sample", "config": {"n": 2}},
             {"name": "retry", "config": {"max": 3}}],
            name="w1.json", steps=self.flaky(2))
        outer_sample = self.run_spec(
            [{"name": "retry", "config": {"max": 3}},
             {"name": "sample", "config": {"n": 2}}],
            name="w2.json", steps=self.flaky(2))

        ca, cb = self.causes(outer_retry), self.causes(outer_sample)
        self.assertEqual(ca[0], ["retry", "sample"])   # retry(sample(step))
        self.assertEqual(cb[0], ["sample", "retry"])   # sample(retry(step))
        self.assertNotEqual(ca, cb)

    def test_outer_retry_sees_the_inner_draw_set(self):
        """retry(sample(...)) must stop once the whole draw set passes.

        Before wrap hooks propagated their return value, retry could not see
        the result of the sample beneath it and ran `max` times regardless --
        silently degenerating into a slower sample.
        """
        rec = self.run_spec(
            [{"name": "sample", "config": {"n": 2}},
             {"name": "retry", "config": {"max": 5}}],
            name="w3.json", steps=self.flaky(2))
        ats = self.attempts(rec)
        self.assertEqual([a["exit"] for a in ats], [1, 1, 0, 0])
        # stopped after the passing draw set, did not exhaust max=5
        self.assertEqual(max(a["caused_by"][0]["i"] for a in ats), 1)

    def test_sample_returns_its_observations(self):
        rec = self.run_spec([{"name": "sample", "config": {"n": 3}}],
                            name="w4.json")
        self.assertEqual(len(self.attempts(rec)), 3)


class TestDiff(WrapBase):
    def diff(self, a, b):
        from harness_workbench import diff as diffmod
        return diffmod.compare(diffmod.load_run(self.runs, a["run_id"]),
                               diffmod.load_run(self.runs, b["run_id"]))

    def test_identical_reruns_are_equivalent(self):
        # NOT run 1 vs run 2: freeze CREATES its baseline on first run and
        # COMPARES on later ones, so a stateful feature's first run is a
        # genuinely different event. Steady state is run 2 vs run 3.
        self.run_spec(["freeze"], name="eq.json")
        b = self.run_spec(["freeze"], name="eq.json")
        c = self.run_spec(["freeze"], name="eq.json")
        res = self.diff(b, c)
        self.assertTrue(res["equivalent"], res["differences"])

    def test_first_run_of_a_stateful_feature_is_not_equivalent(self):
        a = self.run_spec(["freeze"], name="st.json")
        b = self.run_spec(["freeze"], name="st.json")
        res = self.diff(a, b)
        self.assertFalse(res["equivalent"])
        joined = " ".join(res["differences"])
        self.assertIn("baseline", joined)
        self.assertNotIn("digests", joined)   # inputs did NOT change

    def test_diff_detects_attempt_count(self):
        a = self.run_spec([{"name": "sample", "config": {"n": 2}}], name="d1.json")
        b = self.run_spec([{"name": "sample", "config": {"n": 3}}], name="d2.json")
        res = self.diff(a, b)
        self.assertFalse(res["equivalent"])
        self.assertTrue(any("attempt(s)" in d for d in res["differences"]))

    def test_diff_detects_cause_when_counts_and_exits_match(self):
        """The case invisible before attempt provenance existed."""
        a = self.run_spec([{"name": "sample", "config": {"n": 2}},
                           {"name": "retry", "config": {"max": 3}}],
                          name="c1.json", steps=self.flaky(2))
        b = self.run_spec([{"name": "retry", "config": {"max": 3}},
                           {"name": "sample", "config": {"n": 2}}],
                          name="c2.json", steps=self.flaky(2))
        pa = [x["exit"] for x in self.attempts(a)]
        pb = [x["exit"] for x in self.attempts(b)]
        self.assertEqual(pa, pb)          # identical outcomes...
        res = self.diff(a, b)
        self.assertTrue(any("CAUSE differs" in d for d in res["differences"]),
                        res["differences"])   # ...different experiments

    def test_diff_refuses_a_drifted_pair(self):
        from harness_workbench import diff as diffmod
        a = self.run_spec(["freeze"], name="dr.json")
        with open(os.path.join(self.tmp, "in.txt"), "w") as fh:
            fh.write("CHANGED\n")
        b = self.run_spec(["freeze"], name="dr.json")
        with self.assertRaises(diffmod.Incomparable) as cm:
            self.diff(a, b)
        self.assertIn("drift", str(cm.exception))

    def test_diff_refuses_when_the_baseline_was_recreated(self):
        """Both runs report NO drift and still ran different inputs.

        Found against real runs: two determinism runs each said "inputs match
        baseline" while their digest for the probe script differed, because
        the baseline had been recreated between them. Each was truthful about
        its own baseline; the pair was still incomparable. The drift flag is
        an opinion about mutable state -- the digests are the evidence.
        """
        from harness_workbench import diff as diffmod
        a = self.run_spec(["freeze"], name="rb.json")
        os.remove(os.path.join(self.tmp, "rb.freeze.lock"))
        with open(os.path.join(self.tmp, "in.txt"), "w") as fh:
            fh.write("DIFFERENT\n")
        b = self.run_spec(["freeze"], name="rb.json")

        self.assertFalse(a["extras"]["freeze"]["drifted"])
        self.assertFalse(b["extras"]["freeze"]["drifted"])
        with self.assertRaises(diffmod.Incomparable) as cm:
            self.diff(a, b)
        self.assertIn("in.txt", str(cm.exception))

    def test_diff_and_interfere_mask_identity_the_same_way(self):
        """They shared a projection and masked differently: `diff` reported
        `baseline_file` as a difference where `interfere` masked it, so two
        runs of one configuration looked non-identical purely because their
        spec files had different names."""
        from harness_workbench import diff as diffmod
        a = self.run_spec(["freeze"], name="mk1.json")
        b = self.run_spec(["freeze"], name="mk2.json")
        res = self.diff(a, b)
        joined = " ".join(res["differences"])
        self.assertNotIn("baseline_file", joined)
        self.assertTrue(any("identity values inside extras" in m
                            for m in res["masked"]))

    def test_diff_compares_the_declared_environment(self):
        """`env` was masked wholesale, so two runs of one spec -- one pinned,
        one at temperature 1.0 with no seed -- compared as EQUIVALENT despite
        being different experiments producing different output. Declaring a
        knob exists so it cannot change the experiment invisibly; masking it
        at comparison time reintroduced the failure one step later."""
        p = os.path.join(self.tmp, "envcmp.json")
        write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                  "features": [], "env": ["HWB_TEST_KNOB"],
                  "steps": [{"id": "01", "argv": ["./probe.sh"], "inputs": []}]})

        def once():
            sp = specmod.load(p)
            return runner.execute(sp, features.resolve(sp), self.runs)

        os.environ.pop("HWB_TEST_KNOB", None)
        a = once()
        os.environ["HWB_TEST_KNOB"] = "changed"
        try:
            b = once()
        finally:
            os.environ.pop("HWB_TEST_KNOB", None)

        res = self.diff(a, b)
        self.assertFalse(res["equivalent"])
        self.assertTrue(any("HWB_TEST_KNOB" in d for d in res["differences"]),
                        res["differences"])

    def test_undeclared_env_names_stay_masked(self):
        """~43 identical names in every run: noise, correctly ignored."""
        a = self.run_spec([], name="un1.json")
        b = self.run_spec([], name="un2.json")
        self.assertTrue(self.diff(a, b)["equivalent"])

    def test_diff_reports_what_it_masked(self):
        a = self.run_spec([], name="m1.json")
        b = self.run_spec([], name="m2.json")
        res = self.diff(a, b)
        self.assertTrue(res["masked"])
        self.assertTrue(any("run_id" in m for m in res["masked"]))

    def test_missing_provenance_is_not_the_same_as_no_wraps(self):
        """A pre-provenance record must not read as equivalent to a run that
        genuinely had no wrap features."""
        from harness_workbench import diff as diffmod
        a = self.run_spec([], name="p1.json")
        path = os.path.join(self.runs, a["run_id"], "attempts.jsonl")
        rows = [json.loads(l) for l in read_text(path).splitlines() if l.strip()]
        for r in rows:
            r["caused_by"] = [{"feature": "sample", "i": 0}]
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        b = self.run_spec([], name="p2.json")
        res = diffmod.compare(diffmod.load_run(self.runs, a["run_id"]),
                              diffmod.load_run(self.runs, b["run_id"]))
        self.assertFalse(res["equivalent"])


class TestReplicates(Base):
    """A reproduction claim must be checkable or it is worse than absent."""

    def spec_rep(self, name, replicates, **kw):
        p = os.path.join(self.tmp, name)
        body = {
            "schema": "hwbspec/v0.1", "run_class": "discovery", "features": [],
            "steps": [{"id": "01", "argv": ["./probe.sh"], "inputs": ["in.txt"]}],
        }
        if replicates is not None:
            body["replicates"] = replicates
        body.update(kw)
        write(p, body)
        return p

    def run_path(self, path):
        sp = specmod.load(path)
        return runner.execute(sp, features.resolve(sp), self.runs)

    def test_valid_replicate_is_accepted(self):
        first = self.run_path(self.spec_rep("base.json", None))
        rec = self.run_path(self.spec_rep("base.json", first["run_id"]))
        self.assertEqual(rec["replicates"], first["run_id"])

    def test_unicode_run_id_is_an_opaque_valid_component(self):
        first = self.run_path(self.spec_rep("unicode.json", None))
        unicode_id = "実験-α"
        os.rename(os.path.join(self.runs, first["run_id"]),
                  os.path.join(self.runs, unicode_id))
        rec = self.run_path(self.spec_rep("unicode.json", unicode_id))
        self.assertEqual(rec["replicates"], unicode_id)

    def test_replicates_rejects_unsafe_components_at_spec_load(self):
        for i, value in enumerate(("", ".", "..", "../outside", "/absolute",
                                   "nested/run", "nested\\run", 3, True, [])):
            with self.subTest(value=value):
                path = self.spec_rep("unsafe-%d.json" % i, value)
                with self.assertRaises(specmod.SpecError) as cm:
                    specmod.load(path)
                self.assertIn("replicates", str(cm.exception))

    def test_direct_spec_cannot_read_an_outside_sibling_record(self):
        # validate_replicates is also a use boundary: even a caller that
        # bypasses load() must not turn a claim into a path traversal.
        path = self.spec_rep("direct.json", None)
        raw = json.loads(read_text(path))
        raw["replicates"] = "../outside"
        direct = specmod.Spec(path, raw)
        outside = os.path.join(os.path.dirname(self.runs), "outside")
        os.makedirs(outside)
        write(os.path.join(outside, "record.json"),
              {"spec_digest": direct.digest, "replicates": None})
        with self.assertRaises(specmod.SpecError) as cm:
            specmod.validate_replicates(direct, self.runs)
        self.assertIn("filesystem-safe", str(cm.exception))

    def test_replicates_a_missing_run_is_rejected(self):
        with self.assertRaises(runner.HarnessError) as cm:
            self.run_path(self.spec_rep("m.json", "20990101T000000Z-dead-beef"))
        self.assertIn("not a run", str(cm.exception))

    def test_replicates_a_different_spec_is_rejected(self):
        other = self.run_path(self.spec_rep("other.json", None, env=["HOME"]))
        with self.assertRaises(runner.HarnessError) as cm:
            self.run_path(self.spec_rep("d.json", other["run_id"]))
        self.assertIn("different spec", str(cm.exception))

    def test_replicate_chains_are_rejected(self):
        first = self.run_path(self.spec_rep("c.json", None))
        second = self.run_path(self.spec_rep("c.json", first["run_id"]))
        with self.assertRaises(runner.HarnessError) as cm:
            self.run_path(self.spec_rep("c.json", second["run_id"]))
        self.assertIn("itself a replicate", str(cm.exception))

    def test_a_rejected_replicate_leaves_no_run_directory(self):
        before = len(os.listdir(self.runs)) if os.path.isdir(self.runs) else 0
        with self.assertRaises(runner.HarnessError):
            self.run_path(self.spec_rep("n.json", "nope"))
        after = len(os.listdir(self.runs)) if os.path.isdir(self.runs) else 0
        self.assertEqual(before, after)


class TestBounds(Base):
    """A hang used to leave a husk: a run directory with a zero-byte
    attempts stream, no record, and invisible to `hwb ls`."""

    def slow_step(self, seconds=30):
        p = os.path.join(self.tmp, "slow.sh")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\nsleep %d\n" % seconds)
        os.chmod(p, 0o755)
        return [{"id": "01", "argv": ["./slow.sh"], "inputs": []}]

    def hanging_feature(self, seam="on_spec_loaded", power="annotate"):
        d = os.path.join(self.feat_dir, "hang")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "hang", "version": "0.1.0", "power": power,
               "seams": [seam], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("import time\ndef %s(*a):\n    time.sleep(30)\n" % seam)

    def spec_bounded(self, name, **kw):
        p = os.path.join(self.tmp, name)
        body = {"schema": "hwbspec/v0.1", "run_class": "discovery",
                "features": [], "steps": [{"id": "01", "argv": ["./probe.sh"],
                                           "inputs": []}]}
        body.update(kw)
        write(p, body)
        return p

    def run_path(self, path):
        sp = specmod.load(path)
        return runner.execute(sp, features.resolve(sp), self.runs)

    def test_a_hanging_step_is_bounded_and_recorded(self):
        rec = self.run_path(self.spec_bounded(
            "st.json", step_timeout_ms=300, steps=self.slow_step()))
        self.assertValidRecord(rec)            # a record EXISTS -- no husk
        p = os.path.join(self.runs, rec["run_id"], "attempts.jsonl")
        a = json.loads(read_text(p).splitlines()[0])
        self.assertTrue(a["timed_out"])
        self.assertIsNone(a["exit"])

    def test_a_hanging_annotate_seam_is_bounded_and_the_run_survives(self):
        self.hanging_feature()
        rec = self.run_path(self.spec_bounded(
            "sh.json", seam_timeout_ms=300, features=[{"name": "hang"}]))
        self.assertValidRecord(rec)
        hang = [f for f in rec["features"] if f["name"] == "hang"][0]
        self.assertEqual(hang["status"], "failed")
        self.assertIn("SeamTimeout", json.dumps(rec["extras"]["hang"]))

    def test_a_hook_that_swallows_the_timeout_is_still_bounded(self):
        """The measured hole in the seam budget.

        `except Exception: pass` inside a retry loop is ordinary careless
        code, and it absorbs SeamTimeout on every fire -- the wall-clock
        check on return is never reached by a hook that never returns. A
        hook doing exactly this ran 120.6 seconds against a 0.4 second
        budget. The escalation raises SeamAbort, which is a BaseException
        and so slips past `except Exception`.
        """
        d = os.path.join(self.feat_dir, "swallow")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "swallow", "version": "0.1.0", "power": "annotate",
               "seams": ["on_spec_loaded"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("import time\n"
                     "def on_spec_loaded(spec, ctx):\n"
                     "    end = time.time() + 20\n"
                     "    while time.time() < end:\n"
                     "        try:\n"
                     "            time.sleep(2)\n"
                     "        except Exception:\n"
                     "            pass\n")
        started = time.time()
        rec = self.run_path(self.spec_bounded(
            "sw.json", seam_timeout_ms=200, features=[{"name": "swallow"}]))
        elapsed = time.time() - started
        self.assertValidRecord(rec)
        self.assertLess(elapsed, 10, "the budget was advisory, not binding")
        f = [x for x in rec["features"] if x["name"] == "swallow"][0]
        self.assertEqual(f["status"], "failed")
        self.assertIn("swallowed", json.dumps(rec["extras"]["swallow"]))

    def test_the_escalation_is_not_an_ordinary_exception(self):
        """Structural: if SeamAbort were an Exception the careless handler
        would absorb it too, and the escalation would buy nothing."""
        from harness_workbench.seams import SeamAbort, SeamTimeout
        self.assertTrue(issubclass(SeamAbort, BaseException))
        self.assertFalse(issubclass(SeamAbort, Exception))
        self.assertTrue(issubclass(SeamTimeout, Exception))

    def test_a_hook_may_still_catch_the_first_timeout_and_return(self):
        """The first fire stays an ordinary Exception on purpose -- a hook
        may legitimately catch it, unwind, and return. Escalation withdraws
        that courtesy only when it is abused."""
        d = os.path.join(self.feat_dir, "polite")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "polite", "version": "0.1.0", "power": "annotate",
               "seams": ["on_spec_loaded"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("import time\n"
                     "def on_spec_loaded(spec, ctx):\n"
                     "    try:\n"
                     "        time.sleep(30)\n"
                     "    except Exception:\n"
                     "        return {'unwound': True}\n")
        rec = self.run_path(self.spec_bounded(
            "pol.json", seam_timeout_ms=200, features=[{"name": "polite"}]))
        self.assertValidRecord(rec)
        # It returned late, so the wall-clock check still fails it -- but it
        # got to run its cleanup, which is the distinction being preserved.
        self.assertTrue(rec["extras"]["polite"])

    def test_wrap_is_not_seam_bounded(self):
        """A wrap feature's elapsed time is mostly the STEP's time -- it
        exists to run the step. Bounding the seam there would fire on a slow
        workload rather than a slow feature, so steps carry their own bound."""
        rec = self.run_path(self.spec_bounded(
            "wb.json", seam_timeout_ms=200,
            features=[{"name": "sample", "config": {"n": 2}}],
            steps=[{"id": "01", "argv": ["./probe.sh"], "inputs": []}]))
        self.assertValidRecord(rec)
        s = [f for f in rec["features"] if f["name"] == "sample"][0]
        self.assertEqual(s["status"], "ok")

    def test_unbounded_is_still_the_default(self):
        rec = self.run_path(self.spec_bounded("ub.json"))
        self.assertValidRecord(rec)

    def test_bad_timeout_values_are_rejected(self):
        for bad in (0, -5, "300", 1.5, True):
            with self.subTest(value=bad):
                p = self.spec_bounded("bad.json", step_timeout_ms=bad)
                with self.assertRaises(specmod.SpecError):
                    specmod.load(p)


class TestConformance(Base):
    """The validator must REJECT. A control that never fires is not a control.

    Every case below is a way one of the two invariants could be broken by a
    future change while the record still looked plausible.
    """

    def good(self):
        rec = self.run_spec([{"name": "sample", "config": {"n": 2}}, "freeze"],
                            name="cf.json")
        self.rundir = os.path.join(self.runs, rec["run_id"])
        p = os.path.join(self.rundir, "attempts.jsonl")
        ats = [json.loads(l) for l in read_text(p).splitlines() if l.strip()]
        return rec, ats

    def assertRejects(self, rec, ats, needle):
        with self.assertRaises(conform.NonConforming) as cm:
            conform.validate_record(rec, ats, run_dir=self.rundir)
        self.assertIn(needle, str(cm.exception))

    def test_the_good_record_passes(self):
        rec, ats = self.good()
        conform.validate_record(rec, ats, run_dir=self.rundir)   # must not raise

    def test_rejects_collapsed_attempts(self):
        """The failure Invariant 1 exists to prevent: reduction at capture."""
        rec, ats = self.good()
        collapsed = [dict(ats[0])]
        collapsed[0]["runs"] = len(ats)
        self.assertRejects(rec, collapsed, "never collapsed")

    def test_rejects_nested_sub_attempts(self):
        rec, ats = self.good()
        ats[0]["sub_attempts"] = [{"n": 0}]
        self.assertRejects(rec, ats, "must not nest")

    def test_rejects_a_feature_writing_outside_its_namespace(self):
        rec, ats = self.good()
        rec["extras"]["somebody_else"] = {"snuck": "in"}
        self.assertRejects(rec, ats, "outside its namespace")

    def test_rejects_a_dormant_grant_power_in_the_record(self):
        rec, ats = self.good()
        rec["features"][0]["power"] = "grant"
        self.assertRejects(rec, ats, "not live")

    def test_rejects_a_power_illegal_at_its_seam(self):
        rec, ats = self.good()
        f = [x for x in rec["features"] if x["name"] == "freeze"][0]
        f["power"] = "wrap"                        # annotate seam, wrap power
        self.assertRejects(rec, ats, "permits")

    def test_rejects_an_attempt_for_an_unlisted_step(self):
        rec, ats = self.good()
        ats[0]["step_id"] = "ghost"
        self.assertRejects(rec, ats, "does not list")

    def test_rejects_a_malformed_provenance_frame(self):
        rec, ats = self.good()
        ats[0]["caused_by"] = [{"feature": "sample"}]     # no ordinal
        self.assertRejects(rec, ats, "feature, i")

    def test_rejects_a_negative_provenance_ordinal(self):
        rec, ats = self.good()
        ats[0]["caused_by"] = [{"feature": "sample", "i": -1}]
        self.assertRejects(rec, ats, "non-negative")

    def test_rejects_a_bad_status(self):
        rec, ats = self.good()
        rec["status"] = "finished"
        self.assertRejects(rec, ats, "status")

    def test_rejects_a_duplicate_feature(self):
        rec, ats = self.good()
        rec["features"].append(dict(rec["features"][0]))
        self.assertRejects(rec, ats, "twice")

    def test_rejects_ok_feature_that_names_a_failed_step(self):
        rec, ats = self.good()
        rec["features"][0]["failed_at_step"] = "01"
        self.assertRejects(rec, ats, "ok but names a failed step")

    def test_a_step_a_wrap_never_ran_conforms(self):
        """Found by running the blast campaign against a real 3-step spec.

        When a wrap feature raises before calling run_step, the runner
        records a synthetic attempt -- the step produced nothing, which is a
        fact, not a crash. But it is the one attempt line with no bytes
        behind it, so the store check called it fabricated. Both designs were
        right in isolation and contradicted each other; the line now declares
        `executed: false` and is exempted by its own declaration.
        """
        d = os.path.join(self.feat_dir, "boom")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "boom", "version": "0.1.0", "power": "wrap",
               "seams": ["around_step"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def around_step(*a):\n    raise RuntimeError('nope')\n")
        rec = self.run_spec(["boom"], name="nx.json",
                            steps=[{"id": "01", "argv": ["./probe.sh"], "inputs": []},
                                   {"id": "02", "argv": ["./probe.sh"], "inputs": []}])
        self.assertValidRecord(rec)      # conformance included

    def test_the_store_check_runs_even_when_nothing_executed(self):
        """The toy case passed only because the check did not run: with no
        steps/ directory it returned early, so a fully fabricated attempt
        stream would have been accepted."""
        rec, ats = self.good()
        shutil.rmtree(os.path.join(self.rundir, "steps"))
        self.assertRejects(rec, ats, "nothing in the store")

    def test_rejects_a_missing_required_key(self):
        rec, ats = self.good()
        del rec["spec_digest"]
        self.assertRejects(rec, ats, "spec_digest")

    def test_unknown_keys_are_still_ignored(self):
        """The additive-contract rule survives: a strict schema would break
        on the next additive field, which is why this is not one."""
        rec, ats = self.good()
        rec["some_future_field"] = {"anything": True}
        ats[0]["another_one"] = 42
        conform.validate_record(rec, ats, run_dir=self.rundir)   # must not raise

    # ---- preservation: the referent is stored, so the claim is checkable

    def test_the_spec_is_preserved(self):
        rec, _ = self.good()
        self.assertTrue(os.path.isfile(os.path.join(self.rundir, "spec.json")))

    def test_the_feature_source_is_preserved(self):
        rec, _ = self.good()
        for f in rec["features"]:
            self.assertTrue(
                os.path.isfile(os.path.join(self.rundir, "features", f["name"],
                                            "feature.py")),
                "feature %r source not preserved" % f["name"])

    def test_rejects_a_spec_digest_that_does_not_match_the_preserved_spec(self):
        """The hole this closes: a spec rewritten after its run used to
        verify clean, because nothing could check the digest against
        anything."""
        rec, ats = self.good()
        p = os.path.join(self.rundir, "spec.json")
        body = read(p)
        body["run_class"] = "confirmation"        # a different experiment
        write(p, body)
        self.assertRejects(rec, ats, "preserved spec digests to")

    def test_rejects_feature_source_that_does_not_match_its_digest(self):
        """The one that matters most: comparing runs across time is only
        sound if each can prove which code it executed."""
        rec, ats = self.good()
        p = os.path.join(self.rundir, "features", "freeze", "feature.py")
        with open(p, "a") as fh:
            fh.write("\n# tampered\n")
        self.assertRejects(rec, ats, "preserved source digests to")

    def test_rejects_a_recorded_feature_whose_source_is_missing(self):
        rec, ats = self.good()
        shutil.rmtree(os.path.join(self.rundir, "features", "freeze"))
        self.assertRejects(rec, ats, "source was not preserved")

    def test_runs_without_preservation_still_conform(self):
        """Backward compatibility: absent means not preserved, not disproven.
        The 11 historical runs predate this and must keep validating."""
        rec, ats = self.good()
        os.remove(os.path.join(self.rundir, "spec.json"))
        shutil.rmtree(os.path.join(self.rundir, "features"))
        conform.validate_record(rec, ats, run_dir=self.rundir)   # must not raise

    # ---- self-attestation: a feature's own arithmetic

    def attesting(self):
        rec = self.run_spec(["freeze", "receipt"], name="at.json")
        self.rundir = os.path.join(self.runs, rec["run_id"])
        p = os.path.join(self.rundir, "attempts.jsonl")
        return rec, [json.loads(l) for l in read_text(p).splitlines() if l.strip()]

    def test_a_self_attesting_feature_is_declared_and_verified(self):
        rec, ats = self.attesting()
        r = [f for f in rec["features"] if f["name"] == "receipt"][0]
        self.assertEqual(r["self_attests"], {"payload": "bound", "digest": "digest"})
        conform.validate_record(rec, ats, run_dir=self.rundir)   # must not raise

    def test_rejects_a_feature_whose_own_digest_disagrees_with_its_payload(self):
        rec, ats = self.attesting()
        rec["extras"]["receipt"]["bound"]["run_class"] = "confirmation"
        self.assertRejects(rec, ats, "own arithmetic disagrees")

    def test_rejects_a_malformed_self_attests_declaration(self):
        rec, ats = self.attesting()
        r = [f for f in rec["features"] if f["name"] == "receipt"][0]
        r["self_attests"] = {"payload": "bound"}          # no digest key
        self.assertRejects(rec, ats, "malformed self_attests")

    def test_verify_reports_conformance(self):
        rec, _ = self.good()
        from harness_workbench import cli
        rc = cli.main(["--root", self.runs, "verify", rec["run_id"]])
        self.assertEqual(rc, 0)

    def test_omitting_run_dir_is_an_error_not_a_silent_downgrade(self):
        """The mechanism behind the bug below, not just the bug.

        `run_dir` used to default to None, so forgetting it produced a
        quieter and MORE confident answer -- the worst failure shape for a
        checker. It is now required: the weak mode must be chosen by typing
        `None`, never by omission.
        """
        rec, ats = self.good()
        with self.assertRaises(TypeError):
            conform.validate_record(rec, ats)          # no run_dir

    def test_the_weak_mode_is_still_available_deliberately(self):
        """Required does not mean mandatory -- a caller holding only a
        record (a shipped log, a record over the wire) can still check what
        is checkable, having said so explicitly."""
        rec, ats = self.good()
        conform.validate_record(rec, ats, None)        # must not raise

    def test_the_weak_mode_really_is_weaker(self):
        """Proves the two modes differ, so the requirement is not ceremony:
        collapse passes without the store and is caught with it."""
        rec, ats = self.good()
        collapsed = [dict(ats[0])]
        conform.validate_record(rec, collapsed, None)          # slips through
        with self.assertRaises(conform.NonConforming):
            conform.validate_record(rec, collapsed, self.rundir)

    def test_verify_runs_the_store_backed_checks(self):
        """Regression: `verify` called validate_record WITHOUT run_dir, which
        silently switched off every check that needs the store as evidence --
        collapse detection and whether the preserved spec and feature source
        match their recorded digests. The tool reported `conforms: yes` on a
        record whose spec had been altered."""
        rec, _ = self.good()
        from harness_workbench import cli
        p = os.path.join(self.rundir, "spec.json")
        body = read(p)
        body["run_class"] = "confirmation"
        write(p, body)
        rc = cli.main(["--root", self.runs, "verify", rec["run_id"]])
        self.assertEqual(rc, 1, "verify must fail on a spec that does not "
                                "match its recorded digest")


class TestSweep(Base):
    """Stage 6's keystone: generate the configurations worth comparing."""

    def setUp(self):
        super().setUp()
        self.sweeps = os.path.join(self.tmp, "sweeps")

    def base_spec(self, feats, name="sw.json"):
        return self.spec(feats, name=name)

    def sweep(self, feats, mode="pairs", name="sw.json"):
        from harness_workbench import sweep as sweepmod
        return sweepmod.run_sweep(self.base_spec(feats, name), self.runs,
                                  self.sweeps, mode)

    def test_configuration_sets(self):
        from harness_workbench import sweep as sweepmod
        names = ["a", "b", "c"]
        self.assertEqual(sweepmod.configurations(names, "singletons"),
                         [(), ("a",), ("b",), ("c",)])
        pairs = sweepmod.configurations(names, "pairs")
        self.assertIn(("a", "b"), pairs)
        self.assertEqual(len(pairs), 1 + 3 + 3)
        self.assertEqual(len(sweepmod.configurations(names, "powerset")), 8)

    def test_powerset_is_capped(self):
        from harness_workbench import sweep as sweepmod
        with self.assertRaises(sweepmod.SweepError) as cm:
            sweepmod.configurations(list("abcdefgh"), "powerset")
        self.assertIn("pairs", str(cm.exception))

    def test_sweep_runs_every_valid_configuration(self):
        man = self.sweep(["freeze", "timing"])
        ran = [r for r in man["configurations"] if r.get("run_id")]
        self.assertEqual(len(ran), 4)          # none, freeze, timing, both
        for r in ran:
            self.assertTrue(r["executed"], "steps did not run for %s" % r["config"])

    def test_an_unrunnable_subset_is_skipped_with_its_reason(self):
        """Not an error: the capability check refusing an invalid condition."""
        man = self.sweep(["freeze", "receipt"])
        skipped = [r for r in man["configurations"] if r.get("skipped")]
        self.assertTrue(skipped)
        self.assertIn("content-digest", skipped[0]["skipped"])
        self.assertEqual(skipped[0]["config"], ["receipt"])

    def test_derived_specs_are_cleaned_up(self):
        self.sweep(["freeze", "timing"])
        leftovers = [f for f in os.listdir(self.tmp) if f.startswith(".hwbsweep-")]
        self.assertEqual(leftovers, [], "derived specs littered the spec dir")

    def test_steps_actually_execute(self):
        """Regression. Derived specs were written into the sweep directory,
        so `cwd=spec.dir` pointed somewhere the step's argv did not exist.
        Every run 'completed', the sweep reported them as ran, and the
        analysis produced findings over runs in which nothing had executed."""
        man = self.sweep(["freeze"])
        for r in man["configurations"]:
            if r.get("run_id"):
                p = os.path.join(self.runs, r["run_id"], "attempts.jsonl")
                exits = [json.loads(l)["exit"]
                         for l in read_text(p).splitlines() if l.strip()]
                self.assertTrue(exits and all(e == 0 for e in exits),
                                "config %s did not execute: %s"
                                % (r["config"], exits))


class TestInterference(Base):
    """MR-1: extras[A] is invariant under attaching B."""

    def setUp(self):
        super().setUp()
        self.sweeps = os.path.join(self.tmp, "sweeps")

    def analyse(self, feats, name):
        from harness_workbench import sweep as sweepmod
        man = sweepmod.run_sweep(self.spec(feats, name=name), self.runs,
                                 self.sweeps, "pairs")
        return sweepmod.interference(man, self.runs)

    def meddler(self):
        """A feature that reaches into another's namespace through the
        record -- zero imports, real coupling, invisible to import analysis."""
        d = os.path.join(self.feat_dir, "meddler")
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": "meddler", "version": "0.1.0", "power": "annotate",
               "seams": ["after_step"], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def after_step(step, obs, ctx):\n"
                     "    o = (ctx.get('extras') or {}).get('freeze')\n"
                     "    if isinstance(o, dict) and isinstance(o.get('digests'), dict):\n"
                     "        for k in o['digests']:\n"
                     "            o['digests'][k] = 'sha256:tampered'\n"
                     "    return {'meddled': True}\n")

    def test_independent_features_show_no_interference(self):
        res = self.analyse(["freeze", "timing"], "ok.json")
        self.assertTrue(res["pairs_checked"])
        self.assertEqual(res["findings"], [],
                         "unexpected interference: %s" % res["findings"])

    def test_it_detects_a_feature_that_reaches_into_another_namespace(self):
        """The negative control. Without this, 'no interference' would be
        indistinguishable from a check that cannot fire."""
        self.meddler()
        res = self.analyse(["freeze", "meddler"], "bad.json")
        self.assertEqual(len(res["findings"]), 1)
        f = res["findings"][0]
        self.assertEqual(f["feature"], "freeze")
        self.assertEqual(f["perturbed_by"], "meddler")
        self.assertTrue(any("digests" in x for x in f["fields"]))

    def test_spec_identity_is_masked_not_reported(self):
        """A sweep necessarily varies the spec, and `freeze` keys its
        baseline to the spec stem -- so `baseline_file` differed in every
        pair and was reported as interference. That is a confound of the
        instrument, not a property of the system."""
        res = self.analyse(["freeze", "timing"], "mask.json")
        for f in res["findings"]:
            self.assertNotIn("baseline_file", f["fields"])
        self.assertTrue(any("filename" in m for m in res["masked"]))

    def test_runs_that_never_executed_are_excluded(self):
        from harness_workbench import sweep as sweepmod
        man = sweepmod.run_sweep(self.spec(["freeze", "timing"], name="ex.json"),
                                 self.runs, self.sweeps, "pairs")
        for r in man["configurations"]:
            if r.get("run_id"):
                r["executed"] = False
        res = sweepmod.interference(man, self.runs)
        self.assertEqual(res["pairs_checked"], 0)
        self.assertTrue(res["unusable"])


class TestBlast(Base):
    """Family 2: inject one fault, measure what survived."""

    def setUp(self):
        super().setUp()
        self.blasts = os.path.join(self.tmp, "blasts")

    def run_campaign(self, feats, name="bl.json"):
        from harness_workbench import blast as blastmod
        return blastmod.campaign(self.spec(feats, name=name), self.runs,
                                 self.blasts, seam_timeout_ms=300)

    def row(self, man, feature, fault):
        for r in man["injections"]:
            if r["feature"] == feature and r["fault"] == fault:
                return r
        self.fail("no injection for %s/%s" % (feature, fault))

    def test_the_fault_library_covers_more_than_raise(self):
        """A library that only raises reports excellent containment and is
        wrong: the killer case is a hook that returns None and never raises."""
        from harness_workbench import blast as blastmod
        self.assertIn("silent", blastmod.applicable("annotate"))
        self.assertIn("hang", blastmod.applicable("annotate"))
        self.assertIn("meddle", blastmod.applicable("annotate"))
        self.assertIn("noop", blastmod.applicable("wrap"))
        self.assertNotIn("noop", blastmod.applicable("annotate"))

    def test_an_annotate_crash_is_contained(self):
        """Invariant 2's promise: record it, disable the feature, continue."""
        man = self.run_campaign(["freeze", "timing"])
        r = self.row(man, "freeze", "raise")
        self.assertTrue(r["completed"])
        self.assertTrue(r["conforms"])
        self.assertTrue(r["others_intact"])
        self.assertTrue(r["steps_retained"])
        self.assertEqual(r["feature_status"], "failed")

    def test_a_wrap_crash_costs_the_step_but_not_the_run(self):
        man = self.run_campaign(["sample"])
        r = self.row(man, "sample", "raise")
        self.assertTrue(r["completed"])
        self.assertTrue(r["conforms"])
        self.assertFalse(r["steps_retained"])      # the step is the cost

    def test_meddle_is_detected_disturbing_another_namespace(self):
        """The negative control. Nothing prevents a feature writing into
        another's namespace through the live extras dict -- the coupling
        channel the plan admits has no mechanical control. Measured here
        rather than asserted."""
        man = self.run_campaign(["freeze", "timing"])
        r = self.row(man, "timing", "meddle")
        self.assertFalse(r["others_intact"])
        self.assertIn("freeze", r["others_moved"])

    def test_a_consumer_of_the_injured_feature_is_excused(self):
        """`receipt` requires content-digest from `freeze`, so a broken
        freeze legitimately changes receipt. Counting that as blast damage
        made five correct results look like violations."""
        man = self.run_campaign(["freeze", "receipt"])
        r = self.row(man, "freeze", "raise")
        self.assertIn("receipt", r["consumers_excused"])
        self.assertTrue(r["others_intact"])

    def test_a_quiet_failure_in_a_self_attesting_feature_is_caught(self):
        """`silent` returns None: the run completes and the feature is never
        marked failed, so nothing in the powers taxonomy notices. The
        self_attests declaration is what turns it into a detectable defect."""
        man = self.run_campaign(["freeze", "receipt"])
        r = self.row(man, "receipt", "silent")
        self.assertTrue(r["completed"])
        self.assertEqual(r["feature_status"], "ok")   # looks fine...
        self.assertFalse(r["conforms"])               # ...but is not

    def test_a_hang_is_contained_now_that_seams_are_bounded(self):
        """Before bounds this was the one fault Family 2 could not measure:
        an unbounded hang left a husk with no record to compare."""
        man = self.run_campaign(["freeze", "timing"])
        r = self.row(man, "freeze", "hang")
        self.assertTrue(r["completed"])
        self.assertTrue(r["conforms"])
        self.assertEqual(r["feature_status"], "failed")

    def test_the_control_runs_under_the_same_conditions(self):
        """A campaign whose control reused an existing freeze baseline
        reported 'compared' while every injection reported 'created', making
        all 15 look like blast damage. The control is derived exactly as the
        injections are."""
        man = self.run_campaign(["freeze", "timing"])
        base = _read_record(self.runs, man["baseline_run"])
        self.assertEqual(base["extras"]["freeze"]["baseline"], "created")

    def test_derived_specs_are_cleaned_up(self):
        self.run_campaign(["freeze", "timing"])
        left = [f for f in os.listdir(self.tmp) if f.startswith(".hwbblast-")]
        self.assertEqual(left, [])


class TestUnusedSeams(Base):
    """`before_run` and `before_step` have no shipped feature.

    That made them unproven surface, which was the actual complaint -- not
    their runtime cost, which is ~0.26us per empty dispatch. These tests
    convert "unproven" into "proven but unused", which is a legitimate state
    for a plugin architecture and costs no contract break.
    """

    def probe(self, seam, name=None):
        name = name or "probe_%s" % seam
        d = os.path.join(self.feat_dir, name)
        os.makedirs(d, exist_ok=True)
        write(os.path.join(d, "FEATURE.json"),
              {"name": name, "version": "0.1.0", "power": "annotate",
               "seams": [seam], "seam_contract": ">=0.2.0,<0.3.0"})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def %s(arg, ctx):\n"
                     "    return {'seam': '%s', 'arg': type(arg).__name__,\n"
                     "            'step': ctx.get('step'),\n"
                     "            'saw': sorted((ctx.get('extras') or {}).keys())}\n"
                     % (seam, seam))
        return name

    def test_before_run_dispatches_and_can_annotate(self):
        n = self.probe("before_run")
        rec = self.run_spec([n], name="br.json")
        self.assertValidRecord(rec)
        self.assertEqual(rec["extras"][n]["seam"], "before_run")
        self.assertEqual(rec["extras"][n]["arg"], "Spec")
        self.assertIsNone(rec["extras"][n]["step"])

    def test_before_step_dispatches_once_per_step(self):
        n = self.probe("before_step")
        rec = self.run_spec([n], name="bs.json",
                            steps=[{"id": "01", "argv": ["./probe.sh"], "inputs": []},
                                   {"id": "02", "argv": ["./probe.sh"], "inputs": []}])
        self.assertValidRecord(rec)
        t = rec["seam_timings"][n]["before_step"]
        self.assertEqual(t["calls"], 2)
        self.assertEqual(rec["extras"][n]["arg"], "Step")

    def test_before_run_is_not_a_duplicate_of_on_spec_loaded(self):
        """It looked redundant -- same signature, same argument, called
        back-to-back with nothing in between. It is not: `before_run` can see
        what `on_spec_loaded` features wrote, and `on_spec_loaded` cannot.
        That makes it the only slot for a feature that must CONSUME a
        spec-time capability and still act before anything executes."""
        early = self.probe("on_spec_loaded", "probe_early")
        late = self.probe("before_run", "probe_late")
        rec = self.run_spec([early, late], name="dup.json")
        self.assertEqual(rec["extras"][early]["saw"], [])
        self.assertIn(early, rec["extras"][late]["saw"])

    def test_a_per_step_annotate_overwrites_its_own_extras(self):
        """A sharp edge worth knowing rather than fixing: the extras
        namespace is per FEATURE, not per feature-per-step, so a `before_step`
        annotator sees only its last write unless it accumulates itself."""
        n = self.probe("before_step")
        rec = self.run_spec([n], name="ow.json",
                            steps=[{"id": "01", "argv": ["./probe.sh"], "inputs": []},
                                   {"id": "02", "argv": ["./probe.sh"], "inputs": []}])
        self.assertEqual(rec["extras"][n]["step"], "02")   # 01's write is gone

    def test_an_empty_seam_costs_nothing_observable(self):
        with_none = self.run_spec([], name="e1.json")
        self.assertValidRecord(with_none)
        self.assertNotIn("before_run", json.dumps(with_none["seam_timings"]))


class TestCatch(Base):
    """Family 4: what does a detector catch that would otherwise pass?"""

    def setUp(self):
        super().setUp()
        self.catches = os.path.join(self.tmp, "catches")

    def campaign(self, feats=("freeze",), name="ct.json"):
        from harness_workbench import catch as catchmod
        return catchmod.campaign(self.spec(list(feats), name=name),
                                 self.runs, self.catches)

    def row(self, man, mutation):
        for r in man["results"]:
            if r["mutation"] == mutation:
                return r
        self.fail("no result for %s" % mutation)

    def test_the_fault_model_is_declared(self):
        """A catch rate with no fault model behind it is the thing this
        family exists to avoid -- freeze has reported drift zero times in
        every real run, and that is absence of injection, not uselessness."""
        man = self.campaign()
        self.assertTrue(man["fault_model"])
        for name, m in man["fault_model"].items():
            self.assertIn(m["expected"], ("caught", "ignored"))
            self.assertTrue(m["why"])

    def test_a_content_change_is_caught(self):
        man = self.campaign()
        self.assertEqual(self.row(man, "append_byte")["detected_by"], "freeze")

    def test_a_deleted_input_is_caught(self):
        man = self.campaign()
        self.assertEqual(self.row(man, "delete")["detected_by"], "freeze")

    def test_metadata_only_change_is_correctly_ignored(self):
        """Digesting content rather than metadata is what makes an mtime
        change ignorable; catching it would be crying wolf."""
        man = self.campaign()
        self.assertIsNone(self.row(man, "touch_only")["detected_by"])

    def test_the_equivalent_mutant_is_caught_and_flagged_as_such(self):
        """A trailing newline changes the bytes and not the meaning. freeze
        catches it, correctly by byte equality -- and that gap between
        'bytes differ' and 'the experiment is incomparable' is the finding."""
        man = self.campaign()
        r = self.row(man, "trailing_newline")
        self.assertEqual(r["detected_by"], "freeze")
        self.assertIn("meaning did not", r["why"])

    def test_the_blind_spot_is_measured_not_assumed(self):
        """Only declared inputs are digested, so an undeclared file is
        structurally invisible. A limit you measured beats one you assumed."""
        man = self.campaign()
        r = self.row(man, "undeclared_file")
        self.assertIsNone(r["detected_by"])
        self.assertIn("structurally invisible", r["why"])

    def test_the_clean_run_reports_no_drift(self):
        man = self.campaign()
        self.assertIsNone(self.row(man, "(none)")["detected_by"])

    def test_inputs_are_restored_after_each_mutation(self):
        before = read_text(os.path.join(self.tmp, "in.txt"))
        self.campaign()
        self.assertEqual(read_text(os.path.join(self.tmp, "in.txt")), before)

    def test_the_executable_bit_survives_a_campaign(self):
        """Found by running catch against the real spec and checking git.

        `delete` removes the input and the restore recreates it with default
        permissions, stripping the executable bit. A spec whose step is
        `./probe.sh` would then fail every subsequent run with permission
        denied -- and it would read as a step failure, not as damage the
        measurement did to its own workload.
        """
        probe = os.path.join(self.tmp, "probe.sh")
        before = os.stat(probe).st_mode
        self.assertTrue(before & 0o111, "probe.sh should start executable")
        p = self.spec(["freeze"], name="mode.json",
                      steps=[{"id": "01", "argv": ["./probe.sh"],
                              "inputs": ["probe.sh"]}])
        from harness_workbench import catch as catchmod
        catchmod.campaign(p, self.runs, self.catches)
        self.assertEqual(os.stat(probe).st_mode, before,
                         "the campaign changed its own workload's mode")

    def test_catch_rate_carries_its_denominator(self):
        from harness_workbench import catch as catchmod
        res = catchmod.summarise(self.campaign())
        self.assertIn("/", res["catch_rate"])


class TestFidelity(Base):
    """Family 5: which questions can the record answer by itself?"""

    def assess(self, rec):
        from harness_workbench import fidelity as fidmod
        return fidmod.assess(os.path.join(self.runs, rec["run_id"]))

    def verdict(self, res, key):
        for r in res["questions"]:
            if r["key"] == key:
                return r["verdict"]
        self.fail("no question %r" % key)

    def test_a_current_run_answers_nearly_everything(self):
        from harness_workbench import fidelity as fidmod
        res = self.assess(self.run_spec(["freeze", "timing"], name="f1.json"))
        self.assertEqual(res["counts"][fidmod.UNANSWERED], 0)

    def test_preservation_is_what_makes_reproduction_answerable(self):
        from harness_workbench import fidelity as fidmod
        rec = self.run_spec(["freeze"], name="f2.json")
        d = os.path.join(self.runs, rec["run_id"])
        self.assertEqual(self.assess(rec)["questions"][6]["verdict"],
                         fidmod.ANSWERED)
        os.remove(os.path.join(d, "spec.json"))
        res = fidmod.assess(d)
        self.assertEqual(self.verdict(res, "could_i_reproduce"), fidmod.PARTIAL)

    def test_absent_provenance_is_unanswered_not_assumed(self):
        """The distinction the whole absence rule turns on: a missing
        caused_by means 'not recorded', never 'ran once'."""
        from harness_workbench import fidelity as fidmod
        rec = self.run_spec([{"name": "sample", "config": {"n": 2}}],
                            name="f3.json")
        d = os.path.join(self.runs, rec["run_id"])
        p = os.path.join(d, "attempts.jsonl")
        rows = [json.loads(l) for l in read_text(p).splitlines() if l.strip()]
        for r in rows:
            r.pop("caused_by", None)
        with open(p, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        self.assertEqual(self.verdict(fidmod.assess(d), "why_this_attempt"),
                         fidmod.UNANSWERED)

    def test_no_wrap_feature_makes_provenance_answerable_by_absence(self):
        """With no wrap attached, one attempt per step IS the answer."""
        from harness_workbench import fidelity as fidmod
        res = self.assess(self.run_spec(["timing"], name="f4.json"))
        self.assertEqual(self.verdict(res, "why_this_attempt"), fidmod.ANSWERED)

    def test_it_reads_only_the_run_directory(self):
        """The claim under test is that the record is readable WITHOUT the
        tool, so a resolver that needed the harness would beg the question."""
        import inspect
        from harness_workbench import fidelity as fidmod
        src = inspect.getsource(fidmod)
        for forbidden in ("from .runner", "from .features", "from .seams"):
            self.assertNotIn(forbidden, src)


class TestOrder(Base):
    """Family 11 -- feature ORDER as a variable distinct from feature SET."""

    def _plant(self, name, body, power="annotate", seam="after_step"):
        d = os.path.join(self.feat_dir, name)
        os.makedirs(d)
        write(os.path.join(d, "FEATURE.json"), {
            "name": name, "version": "0.1.0", "power": power,
            "seams": [seam], "seam_contract": ">=0.2.0,<0.3.0",
            "record_key": name})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write(body)

    def sweep(self, feats, name):
        from harness_workbench import sweep as sw
        return sw.run_sweep(self.spec(feats, name=name), self.runs,
                            os.path.join(self.tmp, "sw-" + name),
                            mode="permutations")

    def test_declared_order_is_the_first_configuration(self):
        from harness_workbench import sweep as sw
        self.assertEqual(sw.configurations(["a", "b"], "permutations")[0],
                         ("a", "b"))

    def test_permutations_are_capped(self):
        from harness_workbench import sweep as sw
        with self.assertRaises(sw.SweepError):
            sw.configurations(list("abcde"), "permutations")

    def test_derive_reorders_the_feature_list(self):
        """The mechanism the mode depends on. Without it every permutation
        would emit the spec's original order and the family would report
        'order is not significant' for every input -- a green result produced
        by not varying anything."""
        from harness_workbench import sweep as sw
        raw = {"features": [{"name": "a"}, {"name": "b"}]}
        got = sw._derive(raw, ("b", "a"))
        self.assertEqual([f["name"] for f in got["features"]], ["b", "a"])

    def test_subset_modes_keep_the_spec_order(self):
        """The reorder must not change what the existing modes measured."""
        from harness_workbench import sweep as sw
        raw = {"features": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
        for combo in sw.configurations(["a", "b", "c"], "pairs"):
            got = [f["name"] for f in sw._derive(raw, combo)["features"]]
            self.assertEqual(got, sorted(got, key=["a", "b", "c"].index))

    def test_order_insensitive_features_report_not_significant(self):
        from harness_workbench import sweep as sw
        self._plant("alpha", "def after_step(step, result, ctx):\n"
                             "    return {'v': 1}\n")
        self._plant("beta", "def after_step(step, result, ctx):\n"
                            "    return {'v': 2}\n")
        man = self.sweep(["alpha", "beta"], "o1.json")
        res = sw.order_significance(man, self.runs)
        self.assertEqual(res["findings"], [])

    def test_an_order_dependent_feature_is_caught(self):
        """THE REJECTION TEST. A feature that reads what earlier features
        wrote genuinely depends on sequence; if this reports 'not
        significant', the family cannot detect order effects at all."""
        from harness_workbench import sweep as sw
        self._plant("writer", "def after_step(step, result, ctx):\n"
                              "    return {'v': 1}\n")
        self._plant("reader",
                    "def after_step(step, result, ctx):\n"
                    "    seen = sorted((ctx.get('extras') or {}).keys())\n"
                    "    return {'saw': seen}\n")
        man = self.sweep(["writer", "reader"], "o2.json")
        res = sw.order_significance(man, self.runs)
        self.assertTrue(res["findings"],
                        "order effect not detected: %s" % res)

    def test_the_scope_is_stated(self):
        """'No finding' must not be readable as 'order is free in general'."""
        from harness_workbench import sweep as sw
        self._plant("solo", "def after_step(step, result, ctx):\n"
                            "    return {'v': 1}\n")
        man = self.sweep(["solo", "timing"], "o3.json")
        res = sw.order_significance(man, self.runs)
        self.assertIn("this workload", res["scope"])


class TestConfine(Base):
    """Family 8 -- declared power vs exercised power."""

    def _plant(self, name, power, seam, body):
        d = os.path.join(self.feat_dir, name)
        os.makedirs(d)
        write(os.path.join(d, "FEATURE.json"), {
            "name": name, "version": "0.1.0", "power": power,
            "seams": [seam], "seam_contract": ">=0.2.0,<0.3.0",
            "record_key": name})
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write(body)

    def assess(self, rec):
        from harness_workbench import confine
        return confine.assess(os.path.join(self.runs, rec["run_id"]))

    def test_a_wellbehaved_feature_is_clean(self):
        from harness_workbench import confine
        res = self.assess(self.run_spec(["freeze"], name="c1.json"))
        row = [r for r in res["features"] if r["feature"] == "freeze"][0]
        self.assertEqual(row["verdict"], confine.CLEAN)

    def test_an_observe_feature_that_writes_is_caught(self):
        """THE REJECTION TEST. `observe` has its return value discarded by
        contract, so it appears to have no way into the record -- and can
        write to any namespace through ctx. Without this test, `clean` and
        `cannot see writes` are the same output."""
        from harness_workbench import confine
        self._plant("peeker", "observe", "after_step",
                    "def after_step(step, result, ctx):\n"
                    "    ctx['extras'].setdefault('peeker', {})['snuck'] = True\n")
        res = self.assess(self.run_spec(["peeker"], name="c2.json"))
        row = [r for r in res["features"] if r["feature"] == "peeker"][0]
        self.assertEqual(row["verdict"], confine.BREACHED, row["detail"])

    def test_reaching_into_another_namespace_is_caught_and_named(self):
        from harness_workbench import confine
        self._plant("reacher", "annotate", "after_step",
                    "def after_step(step, result, ctx):\n"
                    "    for k, v in (ctx.get('extras') or {}).items():\n"
                    "        if k != ctx.get('feature') and isinstance(v, dict):\n"
                    "            v['injected'] = True\n"
                    "    return {'ok': True}\n")
        rec = self.run_spec(["freeze", "reacher"], name="c3.json")
        res = self.assess(rec)
        row = [r for r in res["features"] if r["feature"] == "reacher"][0]
        self.assertEqual(row["verdict"], confine.BREACHED)
        self.assertIn("freeze", row["detail"])
        self.assertTrue(any(b["kind"] == "foreign" for b in row["breaches"]))

    def test_returning_a_dict_is_not_a_breach(self):
        """The declared channel must not be flagged, or the family reports
        every correct annotate feature."""
        from harness_workbench import confine
        self._plant("polite", "annotate", "after_step",
                    "def after_step(step, result, ctx):\n"
                    "    return {'ok': True}\n")
        res = self.assess(self.run_spec(["polite"], name="c4.json"))
        row = [r for r in res["features"] if r["feature"] == "polite"][0]
        self.assertEqual(row["verdict"], confine.CLEAN, row["detail"])

    def test_a_wellbehaved_wrap_is_clean(self):
        """`sample` runs the step N times and never touches the record."""
        from harness_workbench import confine
        res = self.assess(self.run_spec(["sample"], name="c5.json"))
        row = [r for r in res["features"] if r["feature"] == "sample"][0]
        self.assertEqual(row["verdict"], confine.CLEAN, row["detail"])

    def test_a_wrap_that_writes_the_record_is_caught(self):
        """A wrap's power is over EXECUTION, not over the record -- it has no
        declared channel into extras at all."""
        from harness_workbench import confine
        self._plant("grabby", "wrap", "around_step",
                    "def around_step(step, run_step, ctx):\n"
                    "    ctx['extras'].setdefault('grabby', {})['x'] = 1\n"
                    "    return run_step()\n")
        res = self.assess(self.run_spec(["grabby"], name="c8.json"))
        row = [r for r in res["features"] if r["feature"] == "grabby"][0]
        self.assertEqual(row["verdict"], confine.BREACHED, row["detail"])

    def test_a_nested_features_writes_are_not_blamed_on_the_wrap(self):
        """THE FALSE-POSITIVE GUARD, and the reason this was unmeasured.

        A wrap RUNS the step, so features nested inside it write to the
        record legitimately while it is on the stack. Attributing those to
        the wrap would report a breach for every wrap that composes
        correctly -- the shape that made blast report 15 of 15 injections as
        damage before calibration. Measured between the wrap's own segments,
        never across a nested call.
        """
        from harness_workbench import confine
        self._plant("outer", "wrap", "around_step",
                    "def around_step(step, run_step, ctx):\n"
                    "    return run_step()\n")
        self._plant("inner", "wrap", "around_step",
                    "def around_step(step, run_step, ctx):\n"
                    "    ctx['extras'].setdefault('inner', {})['x'] = 1\n"
                    "    return run_step()\n")
        # inner is declared FIRST so it ends up innermost: the last-declared
        # wrap is outermost (Dispatcher.wrap_chain).
        res = self.assess(self.run_spec(["inner", "outer"], name="c9.json"))
        outer = [r for r in res["features"] if r["feature"] == "outer"][0]
        inner = [r for r in res["features"] if r["feature"] == "inner"][0]
        self.assertEqual(inner["verdict"], confine.BREACHED, inner["detail"])
        self.assertEqual(outer["verdict"], confine.CLEAN,
                         "the nested write was blamed on the outer wrap: %s"
                         % outer["detail"])

    def test_a_record_without_the_field_is_unmeasured_not_clean(self):
        """Older runs carry no breach list. Absent means NOT RECORDED."""
        from harness_workbench import confine
        rec = self.run_spec(["freeze"], name="c6.json")
        p = os.path.join(self.runs, rec["run_id"], "record.json")
        r = read(p)
        for f in r["features"]:
            f.pop("breaches", None)
        write(p, r)
        res = confine.assess(os.path.join(self.runs, rec["run_id"]))
        self.assertEqual(res["features"][0]["verdict"], confine.UNMEASURED)

    def test_it_records_rather_than_disables(self):
        """Enforcing would change what every earlier campaign measured --
        blast's `meddle` fault is this behaviour and is reported contained."""
        self._plant("peeker2", "observe", "after_step",
                    "def after_step(step, result, ctx):\n"
                    "    ctx['extras'].setdefault('peeker2', {})['x'] = 1\n")
        rec = self.run_spec(["peeker2"], name="c7.json")
        f = [x for x in rec["features"] if x["name"] == "peeker2"][0]
        self.assertEqual(f["status"], "ok")
        self.assertEqual(rec["status"], "completed")
        self.assertTrue(f["breaches"])


class TestReplay(Base):
    """Family 10 -- exercising the reproducibility claim instead of reporting it."""

    def replay(self, rec, tag, source=None):
        from harness_workbench import replay as rp
        return rp.replay(self.runs, rec["run_id"],
                         os.path.join(self.tmp, "replays-" + tag),
                         source_dir=source or self.tmp)

    def test_a_deterministic_run_replays_to_a_match(self):
        """The SECOND run of a spec -- one that compared rather than created."""
        from harness_workbench import replay as rp
        self.run_spec(["freeze", "timing"], name="r1.json")
        rec = self.run_spec(["freeze", "timing"], name="r1.json")
        man = self.replay(rec, "r1")
        self.assertEqual(man["verdict"], rp.MATCHED,
                         "%s %s" % (man["differences"], man["refused"]))

    def test_a_run_that_created_state_is_named_unreplayable(self):
        """A first run cannot be reproduced: the state it created now exists.

        The diagnosis is evidence-based rather than assumed -- two replays
        agreeing with each other and not with the original is what makes the
        original the outlier, and it needs no knowledge of WHICH feature was
        stateful.
        """
        from harness_workbench import replay as rp
        rec = self.run_spec(["freeze", "timing"], name="r1b.json")
        man = self.replay(rec, "r1b")
        self.assertEqual(man["verdict"], rp.STATEFUL_ORIGIN,
                         "%s" % man["differences"])
        self.assertIsNotNone(man["second_replay_run"])

    def test_a_step_file_missing_from_inputs_is_reported(self):
        """`./probe.sh` is what the step RUNS and is not declared an input,
        so no content-digest feature can see it change."""
        rec = self.run_spec(["freeze", "timing"], name="r7.json")
        man = self.replay(rec, "r7")
        self.assertIn("probe.sh", man["undeclared_step_files"])

    def test_changed_inputs_are_refused_not_reported_as_divergence(self):
        """A divergence caused by a changed workload is not a finding about
        the record, and must not be able to look like one."""
        from harness_workbench import replay as rp
        rec = self.run_spec(["freeze"], name="r2.json")
        with open(os.path.join(self.tmp, "in.txt"), "w") as fh:
            fh.write("something else\n")
        with self.assertRaises(rp.ReplayError) as cm:
            self.replay(rec, "r2")
        self.assertIn("not the ones this run recorded", str(cm.exception))

    def test_a_run_without_a_preserved_spec_is_refused(self):
        from harness_workbench import replay as rp
        rec = self.run_spec(["timing"], name="r3.json")
        os.remove(os.path.join(self.runs, rec["run_id"], "spec.json"))
        with self.assertRaises(rp.ReplayError) as cm:
            self.replay(rec, "r3")
        self.assertIn("preserve", str(cm.exception))

    def test_it_does_not_touch_the_source_workload(self):
        """Including modes -- a lost executable bit is how this class of
        defect presented the first time."""
        rec = self.run_spec(["freeze", "timing"], name="r4.json")
        watched = [os.path.join(self.tmp, "probe.sh"),
                   os.path.join(self.tmp, "in.txt")]
        before = {}
        for p in watched:
            with open(p, "rb") as fh:
                before[p] = (fh.read(), os.stat(p).st_mode)
        self.replay(rec, "r4")
        for p in watched:
            with open(p, "rb") as fh:
                self.assertEqual(fh.read(), before[p][0], "replay edited %s" % p)
            self.assertEqual(os.stat(p).st_mode, before[p][1],
                             "replay changed the mode of %s" % p)

    def test_the_copied_workload_preserves_the_executable_bit(self):
        """The step is `./probe.sh`; a copy without its mode cannot run."""
        rec = self.run_spec(["freeze", "timing"], name="r5.json")
        man = self.replay(rec, "r5")
        fresh = _read_record(self.runs, man["replay_run"])
        self.assertEqual(fresh["status"], "completed")
        self.assertEqual(fresh["failed_steps"], [])

    def test_it_records_whether_the_directory_came_from_outside(self):
        """Where the workload came from is part of what `matched` means: a
        verdict that needed a human to point at the files is weaker evidence
        than one the record located itself."""
        rec = self.run_spec(["timing"], name="r6.json")
        man = self.replay(rec, "r6")
        self.assertTrue(man["workload_dir_supplied_by_hand"])

    def test_the_record_names_the_directory_it_ran_in(self):
        """Steps resolve argv and inputs against the spec's directory, so a
        preserved spec without it does not describe a reproducible run."""
        rec = self.run_spec(["timing"], name="r8.json")
        stored = _read_record(self.runs, rec["run_id"])["spec_path"]
        self.assertEqual(os.path.realpath(os.path.dirname(stored)),
                         os.path.realpath(self.tmp))
        self.assertTrue(os.path.isfile(stored))

    def test_replay_needs_no_in_argument(self):
        """The gap this closes: replay used to require a human to supply a
        directory the record could not name."""
        from harness_workbench import replay as rp
        self.run_spec(["freeze", "timing"], name="r9.json")
        rec = self.run_spec(["freeze", "timing"], name="r9.json")
        man = rp.replay(self.runs, rec["run_id"],
                        os.path.join(self.tmp, "replays-r9"))   # no source_dir
        self.assertTrue(man["workload_dir_recoverable_from_record"])
        self.assertFalse(man["workload_dir_supplied_by_hand"])
        self.assertEqual(man["verdict"], rp.MATCHED,
                         "%s %s" % (man["differences"], man["refused"]))

    def test_the_path_is_masked_in_comparison(self):
        """Where a run sat is not what it was. Two runs of one spec must
        stay comparable, or recording the path would break every diff."""
        from harness_workbench import diff as d
        a = self.run_spec(["timing"], name="r10.json")
        b = self.run_spec(["timing"], name="r10.json")
        res = d.compare(d.load_run(self.runs, a["run_id"]),
                        d.load_run(self.runs, b["run_id"]))
        self.assertTrue(res["equivalent"], res["differences"])


class TestEfficacy(Base):
    """Family 7 -- inverting a feature's decision and requiring a difference."""

    def _plant(self, name, hook_body, invert_body, seam="after_step",
               power="annotate"):
        """A synthetic feature plus its declared inversion."""
        d = os.path.join(self.feat_dir, name)
        os.makedirs(d)
        write(os.path.join(d, "FEATURE.json"), {
            "name": name, "version": "0.1.0", "power": power,
            "seams": [seam], "seam_contract": ">=0.2.0,<0.3.0",
            "record_key": name,
            "inverts": {"seam": seam, "source": "invert.py",
                        "decision": "a synthetic verdict"},
        })
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write(hook_body)
        with open(os.path.join(d, "invert.py"), "w") as fh:
            fh.write(invert_body)
        return d

    def campaign(self, feats, name):
        from harness_workbench import efficacy as eff
        return eff.campaign(self.spec(feats, name=name), self.runs,
                            os.path.join(self.tmp, "eff-" + name))

    def test_a_load_bearing_feature_is_killed(self):
        """Its verdict reaches the record, so inverting it is visible."""
        from harness_workbench import efficacy as eff
        self._plant(
            "verdicter",
            "def after_step(step, result, ctx):\n    return {'ok': True}\n",
            "def after_step(step, result, ctx):\n    return {'ok': False}\n")
        man = self.campaign(["verdicter"], "e1.json")
        row = [r for r in man["mutants"] if r["feature"] == "verdicter"][0]
        self.assertEqual(row["verdict"], eff.KILLED, row["detail"])

    def test_an_inert_feature_survives_and_is_reported(self):
        """THE REJECTION TEST FOR THIS FAMILY.

        A feature that computes a verdict and then records a constant is the
        'gate wired to permit everything' shape: it decides, and the decision
        reaches nothing. Without this test, `killed` and `cannot detect
        inertness` would be the same output -- the exact failure Family 9
        exists to name, applied to Family 7 itself.
        """
        from harness_workbench import efficacy as eff
        self._plant(
            "decorative",
            "def after_step(step, result, ctx):\n"
            "    decision = True\n"
            "    return {'summary': 'checked'}\n",
            "def after_step(step, result, ctx):\n"
            "    decision = False\n"
            "    return {'summary': 'checked'}\n")
        man = self.campaign(["decorative"], "e2.json")
        row = [r for r in man["mutants"] if r["feature"] == "decorative"][0]
        self.assertEqual(row["verdict"], eff.SURVIVED, row["detail"])
        self.assertEqual(len(eff.summarise(man)["inert"]), 1)

    def test_a_broken_inversion_is_malformed_not_killed(self):
        """A mutant that crashes tests Family 2, not this one.

        Counting it as a kill would be the worst outcome available: a broken
        inversion would read as proof the feature is load-bearing.
        """
        from harness_workbench import efficacy as eff
        self._plant(
            "crasher",
            "def after_step(step, result, ctx):\n    return {'ok': True}\n",
            "def after_step(step, result, ctx):\n"
            "    raise RuntimeError('not an inversion')\n")
        man = self.campaign(["crasher"], "e3.json")
        row = [r for r in man["mutants"] if r["feature"] == "crasher"][0]
        self.assertEqual(row["verdict"], eff.MALFORMED, row["detail"])
        self.assertNotEqual(row["verdict"], eff.KILLED)

    def test_a_feature_declaring_no_inversion_is_skipped_not_passed(self):
        """Untestable and tested-and-fine must not print the same way."""
        from harness_workbench import efficacy as eff
        man = self.campaign(["timing"], "e4.json")
        row = [r for r in man["mutants"] if r["feature"] == "timing"][0]
        self.assertEqual(row["verdict"], eff.SKIPPED)
        self.assertEqual(eff.summarise(man)["tested"], 0)

    def test_an_unstable_baseline_refuses_rather_than_reporting(self):
        """Two control runs that disagree make every kill uninterpretable."""
        from harness_workbench import efficacy as eff
        self._plant(
            "drifty",
            "import time\n"
            "def after_step(step, result, ctx):\n"
            "    return {'t': time.time()}\n",
            "def after_step(step, result, ctx):\n    return {'t': 0}\n")
        with self.assertRaises(eff.UnstableBaseline):
            self.campaign(["drifty"], "e5.json")

    def test_the_real_freeze_inversion_is_wellformed_and_killed(self):
        """The shipped inversion must be a semantic mutant, not a fault."""
        from harness_workbench import efficacy as eff
        man = self.campaign(["freeze"], "e6.json")
        row = [r for r in man["mutants"] if r["feature"] == "freeze"][0]
        self.assertEqual(row["verdict"], eff.KILLED, row["detail"])

    def test_an_invariant_rejecting_the_mutant_is_a_kill_not_a_malformation(self):
        """`receipt` publishes a digest OF its payload and `conform` verifies
        the pair, so inverting the binding makes the record non-conforming.

        An earlier version treated that as MALFORMED -- reporting the
        strongest available kill as an unusable result, and doing it to
        exactly the features whose decisions are checked hardest. conform is
        DOWNSTREAM; a downstream checker refusing the record is a detection.
        """
        from harness_workbench import efficacy as eff
        man = self.campaign(["freeze", "receipt"], "e7.json")
        row = [r for r in man["mutants"] if r["feature"] == "receipt"][0]
        self.assertEqual(row["verdict"], eff.KILLED, row["detail"])
        self.assertEqual(row["killed_by"], "conform", row["detail"])

    def test_the_kill_names_which_checker_caught_it(self):
        """Killed-by-conform and killed-by-diff are different claims: one
        says an invariant guards the feature, the other only that the run
        came out different."""
        from harness_workbench import efficacy as eff
        man = self.campaign(["freeze"], "e8.json")
        row = [r for r in man["mutants"] if r["feature"] == "freeze"][0]
        self.assertIn(row["killed_by"], ("diff", "conform"))


class TestSteady(Base):
    """Family 12: is the unchanged control stable enough to compare?"""

    def campaign(self, steps=None, repeats=3, allowance=None, name="steady.json"):
        from harness_workbench import steady
        root = os.path.join(self.tmp, "steadies")
        return steady.campaign(self.spec([], name=name, steps=steps), self.runs,
                               root, repeats=repeats, allowance=allowance)

    def moving_steps(self):
        p = os.path.join(self.tmp, "moving.sh")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\n"
                     "n=0\n"
                     "test ! -f .steady-counter || n=$(cat .steady-counter)\n"
                     "n=$((n + 1))\n"
                     "echo $n > .steady-counter\n"
                     "echo $n\n")
        os.chmod(p, 0o755)
        return [{"id": "01", "argv": ["./moving.sh"], "inputs": []}]

    def test_default_is_three_preserved_runs_with_empty_allowance(self):
        from harness_workbench import steady
        man = self.campaign(steps=[{"id": "01", "argv": ["/bin/echo", "same"],
                                    "inputs": []}])
        self.assertEqual(man["verdict"], steady.STABLE)
        self.assertEqual(man["repeats_requested"], 3)
        self.assertEqual(len(man["run_ids"]), 3)
        self.assertEqual(len(man["comparisons"]), 2)
        self.assertEqual(man["allowance"], [])
        self.assertTrue(man["base_spec_digest"].startswith("sha256:"))
        for run_id in man["run_ids"]:
            self.assertTrue(os.path.isfile(os.path.join(
                self.runs, run_id, "record.json")))

    def test_positive_control_rejects_a_deterministically_moving_output(self):
        from harness_workbench import steady
        man = self.campaign(steps=self.moving_steps(), name="moving.json")
        axis = "output:steps/01/attempts/0/stdout.bin"
        self.assertEqual(man["verdict"], steady.UNSTABLE)
        self.assertIn(axis, man["moving_axes"])
        self.assertIn(axis, man["unallowed_axes"])
        self.assertTrue(all(r["run_a"] == man["run_ids"][0]
                            for r in man["comparisons"]))

    def test_an_explicit_exact_axis_allowance_is_not_implicit_noise(self):
        from harness_workbench import steady
        axis = "output:steps/01/attempts/0/stdout.bin"
        man = self.campaign(steps=self.moving_steps(), allowance=[axis],
                            name="allowed.json")
        self.assertEqual(man["verdict"], steady.STABLE)
        self.assertIn(axis, man["moving_axes"])
        self.assertEqual(man["unallowed_axes"], [])

    def test_a_comparison_refusal_is_uninterpretable_not_unstable(self):
        from harness_workbench import steady
        a = self.run_spec(["freeze"], name="refuse.json")
        with open(os.path.join(self.tmp, "in.txt"), "w", encoding="utf-8") as fh:
            fh.write("changed\n")
        b = self.run_spec(["freeze"], name="refuse.json")
        row = steady.compare_pair(self.runs, a["run_id"], b["run_id"])
        self.assertEqual(row["verdict"], steady.UNINTERPRETABLE)
        self.assertIn("drift", row["detail"])

    def test_feature_tree_drift_is_a_harness_axis(self):
        from harness_workbench import steady
        a = self.run_spec(["timing"], name="feature-drift.json")
        with open(os.path.join(self.feat_dir, "timing", "feature.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("\n# changed between unchanged controls\n")
        b = self.run_spec(["timing"], name="feature-drift.json")
        row = steady.compare_pair(self.runs, a["run_id"], b["run_id"])
        self.assertEqual(row["verdict"], steady.UNSTABLE)
        self.assertIn("harness:features[timing].digest", row["moving_axes"])

    def test_setup_error_is_not_a_stability_verdict(self):
        from harness_workbench import steady
        with self.assertRaisesRegex(steady.SteadyError, "at least 2"):
            self.campaign(repeats=1)

        man = steady.campaign(self.spec(["does-not-exist"], name="bad-feature.json"),
                              self.runs, os.path.join(self.tmp, "bad-steadies"))
        self.assertEqual(man["verdict"], steady.SETUP_ERROR)
        self.assertIn("could not execute", man["setup_error"])
        self.assertEqual(man["run_ids"], [])

    def test_pair_verdicts_are_fail_closed_not_averaged(self):
        from harness_workbench import steady
        rows = [{"verdict": steady.STABLE}, {"verdict": steady.UNSTABLE},
                {"verdict": steady.UNINTERPRETABLE}]
        self.assertEqual(steady.classify(rows), steady.UNINTERPRETABLE)
        self.assertEqual(steady.classify(rows[:2]), steady.UNSTABLE)

    def test_cli_exit_distinguishes_stable_unstable_and_setup(self):
        from harness_workbench import cli
        stable = self.spec([], name="cli-stable.json", steps=[
            {"id": "01", "argv": ["/bin/echo", "same"], "inputs": []}])
        moving = self.spec([], name="cli-moving.json", steps=self.moving_steps())
        args = ["--root", self.runs, "--steadies",
                os.path.join(self.tmp, "cli-steadies"), "steady"]
        self.assertEqual(cli.main(args + [stable]), 0)
        self.assertEqual(cli.main(args + [moving]), 1)
        self.assertEqual(cli.main(args + ["--repeats", "1", stable]), 2)


class TestEffects(Base):
    """Family 13: bounded endpoint filesystem-effect observation."""

    def setUp(self):
        super().setUp()
        self.state = os.path.join(self.tmp, "state")
        self.effects_store = os.path.join(self.tmp, "effect-campaigns")
        os.makedirs(self.state)

    def campaign(self, command, allowances=None, name="effects.json"):
        from harness_workbench import effects
        path = self.spec([], name=name, steps=[{
            "id": "01", "argv": ["/bin/sh", "-c", command], "inputs": []}])
        return effects.campaign(path, self.runs, self.effects_store,
                                ["state"], allowances or [])

    def test_allowed_endpoint_change_is_within_envelope_not_clean(self):
        from harness_workbench import effects
        man = self.campaign("printf allowed > state/allowed.txt",
                            ["state/allowed.txt"])
        self.assertEqual(man["verdict"], effects.WITHIN_ENVELOPE)
        self.assertNotEqual(man["verdict"].lower(), "clean")
        self.assertEqual([r["path"] for r in man["allowed_changes"]],
                         ["state/allowed.txt"])
        self.assertEqual(man["breaches"], [])
        self.assertTrue(os.path.isfile(os.path.join(
            self.runs, man["run_id"], "record.json")))
        self.assertIn("process creation, descendant lifetime, signals, and IPC",
                      man["sensor"]["unobserved"])

    def test_known_red_write_outside_allowance_is_a_breach(self):
        from harness_workbench import effects
        man = self.campaign(
            "printf allowed > state/allowed.txt; printf spill > state/spill.txt",
            ["state/allowed.txt"], name="breach.json")
        self.assertEqual(man["verdict"], effects.BREACH)
        self.assertEqual([r["path"] for r in man["breaches"]],
                         ["state/spill.txt"])
        row = man["breaches"][0]
        self.assertEqual(row["change"], "added")
        self.assertIsNone(row["before"])
        self.assertEqual(row["after"]["type"], "regular")
        self.assertTrue(row["after"]["digest"].startswith("sha256:"))

    def test_content_mode_removal_and_type_are_exact_evidence(self):
        from harness_workbench import effects
        file_path = os.path.join(self.state, "subject")
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write("before")
        watches, allowances = effects._resolve_contract(
            self.tmp, ["state"], [], self.runs, self.effects_store)
        before, _ = effects.snapshot(watches, self.tmp)
        os.remove(file_path)
        os.mkdir(file_path)
        after, _ = effects.snapshot(watches, self.tmp)
        changes = effects.compare(before, after, allowances, self.tmp)
        row = [r for r in changes if r["path"] == "state/subject"][0]
        self.assertEqual(row["change"], "type_changed")
        self.assertEqual(row["before"]["type"], "regular")
        self.assertEqual(row["after"]["type"], "directory")

    def test_no_watch_and_broad_or_overlapping_watch_are_setup_errors(self):
        from harness_workbench import effects
        path = self.spec([], name="contract.json")
        with self.assertRaisesRegex(effects.EffectsError, "no default root"):
            effects.campaign(path, self.runs, self.effects_store, [], [])
        with self.assertRaisesRegex(effects.EffectsError, "broader roots"):
            effects.campaign(path, self.runs, self.effects_store, ["."], [])

        child = os.path.join(self.state, "child")
        os.makedirs(child)
        with self.assertRaisesRegex(effects.EffectsError, "overlap"):
            effects.campaign(path, self.runs, self.effects_store,
                             ["state", "state/child"], [])

    def test_allowance_must_belong_to_one_watch(self):
        from harness_workbench import effects
        path = self.spec([], name="allow-contract.json")
        with self.assertRaisesRegex(effects.EffectsError,
                                    "inside exactly one watched root"):
            effects.campaign(path, self.runs, self.effects_store,
                             ["state"], ["outside.txt"])

    def test_instrument_stores_cannot_overlap_the_subject_watch(self):
        from harness_workbench import effects
        path = self.spec([], name="store-contract.json")
        inside = os.path.join(self.state, "campaigns")
        with self.assertRaisesRegex(effects.EffectsError,
                                    "instrument-owned writes"):
            effects.campaign(path, self.runs, inside, ["state"], [])

    def test_campaign_store_cannot_be_the_run_store(self):
        from harness_workbench import effects

        path = self.spec([], name="same-store-effects.json")
        self.assertFalse(os.path.exists(self.runs))
        with self.assertRaisesRegex(effects.EffectsError, "must not overlap"):
            effects.campaign(path, self.runs, self.runs, ["state"], [])
        self.assertFalse(os.path.exists(self.runs),
                         "setup refusal must not leave campaign or run evidence")

    def test_subject_setup_error_is_not_a_scoped_pass(self):
        from harness_workbench import effects
        path = self.spec(["does-not-exist"], name="bad-effects.json")
        man = effects.campaign(path, self.runs, self.effects_store,
                               ["state"], [])
        self.assertEqual(man["verdict"], effects.SETUP_ERROR)
        self.assertIn("could not execute", man["setup_error"])
        self.assertIsNone(man["run_id"])

    def test_snapshot_failure_is_an_instrument_error(self):
        from unittest import mock
        from harness_workbench import effects
        path = self.spec([], name="sensor-error.json")
        with mock.patch.object(effects, "snapshot",
                               side_effect=PermissionError("denied")):
            man = effects.campaign(path, self.runs, self.effects_store,
                                   ["state"], [])
        self.assertEqual(man["verdict"], effects.INSTRUMENT_ERROR)
        self.assertIn("before snapshot failed", man["instrument_error"])
        self.assertIsNone(man["run_id"])

    def test_special_nodes_make_the_scoped_result_uninterpretable(self):
        from harness_workbench import effects
        self.assertEqual(effects.classify([], ["state/socket"]),
                         effects.UNINTERPRETABLE)

    def test_cli_distinguishes_within_breach_and_bad_setup(self):
        from harness_workbench import cli
        allowed = self.spec([], name="fx-cli-ok.json", steps=[{
            "id": "01", "argv": ["/bin/sh", "-c",
                                    "printf ok > state/allowed.txt"],
            "inputs": []}])
        breached = self.spec([], name="fx-cli-red.json", steps=[{
            "id": "01", "argv": ["/bin/sh", "-c",
                                    "printf red > state/spill.txt"],
            "inputs": []}])
        prefix = ["--root", self.runs, "--effects-store", self.effects_store,
                  "effects"]
        self.assertEqual(cli.main(prefix + [allowed, "--watch", "state",
                                            "--allow", "state/allowed.txt"]), 0)
        self.assertEqual(cli.main(prefix + [breached, "--watch", "state",
                                            "--allow", "state/allowed.txt"]), 1)
        self.assertEqual(cli.main(prefix + [allowed, "--watch", "."]), 2)


class TestInterruptions(Base):
    """Family 14: named direct-child interruption and store-state truth."""

    def setUp(self):
        super().setUp()
        self.interrupts = os.path.join(self.tmp, "interrupt-campaigns")

    def test_state_oracle_distinguishes_all_closed_states_without_repair(self):
        from harness_workbench import interrupt

        missing = os.path.join(self.runs, "announced-but-absent")
        self.assertEqual(interrupt.inspect_state(missing)["state"],
                         interrupt.ABSENT)

        incomplete = os.path.join(self.runs, "husk")
        os.makedirs(incomplete)
        with open(os.path.join(incomplete, "attempts.jsonl"), "w"):
            pass
        self.assertEqual(interrupt.inspect_state(incomplete)["state"],
                         interrupt.INCOMPLETE)

        rec = self.run_spec([], name="oracle.json")
        d = os.path.join(self.runs, rec["run_id"])
        self.assertEqual(interrupt.inspect_state(d)["state"], interrupt.COMPLETE)
        os.remove(os.path.join(d, "integrity.json"))
        observed = interrupt.inspect_state(d)
        self.assertEqual(observed["state"], interrupt.RECOVERABLE)
        self.assertIn("not closed", observed["reasons"][0])
        self.assertTrue(os.path.isfile(os.path.join(d, "record.json")),
                        "inspection must not delete or quarantine evidence")

    def test_exhaustive_integrity_rejects_a_file_added_after_close(self):
        rec = self.run_spec([], name="inventory.json")
        d = os.path.join(self.runs, rec["run_id"])
        with open(os.path.join(d, "late.tmp"), "w", encoding="utf-8") as fh:
            fh.write("not in the close-time inventory")
        result = runner.verify(d)
        self.assertEqual(result["state"], "drifted")
        self.assertEqual(result["untracked"], ["late.tmp"])

    def test_integrity_inventory_cannot_escape_the_run_directory(self):
        rec = self.run_spec([], name="inventory-path.json")
        d = os.path.join(self.runs, rec["run_id"])
        path = os.path.join(d, "integrity.json")
        base = read(path)
        base["files"]["../outside"] = "sha256:" + "0" * 64
        write(path, base)
        result = runner.verify(d)
        self.assertEqual(result["state"], "baseline_invalid")
        self.assertIn("escapes", result["error"])

    def test_integrity_closure_metadata_is_required_before_clean(self):
        from harness_workbench import interrupt

        rec = self.run_spec([], name="integrity-metadata.json")
        d = os.path.join(self.runs, rec["run_id"])
        path = os.path.join(d, "integrity.json")
        original = read(path)
        mutations = (
            ("missing schema", lambda value: value.pop("schema")),
            ("wrong schema", lambda value: value.__setitem__(
                "schema", "integrity/future")),
            ("missing written_at", lambda value: value.pop("written_at")),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                changed = json.loads(json.dumps(original))
                mutate(changed)
                write(path, changed)
                verified = runner.verify(d)
                self.assertEqual(verified["state"], "baseline_invalid")
                self.assertIn(label.split()[-1], verified["error"])
                self.assertEqual(interrupt.inspect_state(d)["state"],
                                 interrupt.INCOMPLETE)
        write(path, original)
        self.assertEqual(runner.verify(d)["state"], "clean")

    def test_directory_name_must_match_the_embedded_run_id(self):
        from harness_workbench import interrupt

        rec = self.run_spec([], name="directory-identity.json")
        original = os.path.join(self.runs, rec["run_id"])
        renamed = os.path.join(self.runs, "renamed-valid-bytes")
        shutil.copytree(original, renamed)

        # Integrity is deliberately location-neutral: the stored bytes did
        # not move. Store-backed conformance owns the directory identity.
        self.assertEqual(runner.verify(renamed)["state"], "clean")
        observed = interrupt.inspect_state(renamed)
        self.assertEqual(observed["state"], interrupt.INCOMPLETE)
        self.assertIn("does not match run directory", observed["reasons"][0])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX mkfifo")
    def test_non_regular_nodes_prevent_a_clean_exhaustive_inventory(self):
        from harness_workbench import interrupt

        rec = self.run_spec([], name="inventory-special.json")
        d = os.path.join(self.runs, rec["run_id"])
        fifo = os.path.join(d, "late.pipe")
        os.mkfifo(fifo)

        verified = runner.verify(d)
        self.assertEqual(verified["state"], "drifted")
        self.assertEqual(verified["unsupported"], ["late.pipe"])
        self.assertEqual(interrupt.inspect_state(d)["state"],
                         interrupt.INCOMPLETE)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX mkfifo")
    def test_integrity_writer_refuses_non_regular_nodes_without_opening_them(self):
        d = os.path.join(self.tmp, "special-close")
        os.makedirs(d)
        with open(os.path.join(d, "ordinary.txt"), "w", encoding="utf-8") as fh:
            fh.write("ordinary")
        os.mkfifo(os.path.join(d, "blocking.pipe"))

        with self.assertRaisesRegex(runner.HarnessError,
                                    "non-regular stored path"):
            runner._write_integrity(d)
        self.assertFalse(os.path.exists(os.path.join(d, "integrity.json")))

    def test_every_named_checkpoint_has_the_exact_expected_state(self):
        from harness_workbench import interrupt

        path = self.spec([], name="interrupt.json")
        man = interrupt.campaign(path, self.runs, self.interrupts,
                                 timeout_seconds=5)
        self.assertEqual(man["verdict"], interrupt.PASSED, man["violations"])
        rows = {r["checkpoint"]: r for r in man["checkpoints"]}
        self.assertEqual(set(rows) - {"uninterrupted_control"},
                         {name for name, _ in interrupt.CHECKPOINTS})
        for name, expected in interrupt.CHECKPOINTS:
            with self.subTest(checkpoint=name):
                row = rows[name]
                self.assertEqual(row["observed_state"], expected)
                self.assertEqual(row["violations"], [])
                self.assertTrue(row["child"]["terminate_requested"])
        self.assertEqual(rows["uninterrupted_control"]["observed_state"],
                         interrupt.COMPLETE)
        self.assertEqual(rows["uninterrupted_control"]["child"]["returncode"], 0)

        early = [rows[name] for name, _ in interrupt.CHECKPOINTS
                 if name not in ("integrity_written",)]
        self.assertFalse(any(r["observed_state"] == interrupt.COMPLETE
                             for r in early),
                         "no pre-integrity boundary may appear complete")
        self.assertIn("descendant-process lifetime",
                      " ".join(man["unobserved"]))
        self.assertTrue(os.path.isfile(os.path.join(
            self.interrupts, man["campaign_id"], "campaign.json")))

    def test_list_exposes_husks_and_show_verify_refuse_them(self):
        import io
        from contextlib import redirect_stdout
        from harness_workbench import cli

        husk = os.path.join(self.runs, "interrupted-husk")
        os.makedirs(husk)
        with open(os.path.join(husk, "attempts.jsonl"), "w"):
            pass
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--root", self.runs, "ls"]), 0)
            self.assertEqual(cli.main(["--root", self.runs, "show",
                                       "interrupted-husk"]), 1)
            self.assertEqual(cli.main(["--root", self.runs, "verify",
                                       "interrupted-husk"]), 1)
        shown = out.getvalue()
        self.assertIn("interrupted-husk", shown)
        self.assertIn("incomplete", shown)
        self.assertIn("record.json has not closed", shown)

    def test_show_json_remains_parseable_for_complete_and_incomplete_paths(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from harness_workbench import cli

        rec = self.run_spec([], name="show-json.json")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--root", self.runs, "show",
                                       rec["run_id"], "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["run_id"], rec["run_id"])

        husk = os.path.join(self.runs, "json-husk")
        os.makedirs(husk)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(["--root", self.runs, "show",
                                       "json-husk", "--json"]), 1)
        self.assertEqual(json.loads(out.getvalue())["lifecycle"], "incomplete")
        self.assertIn("non-passing", err.getvalue())

    def test_list_and_show_fall_back_for_readable_malformed_records(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from harness_workbench import cli

        malformed = {
            "empty-record": {},
            "bad-features": {"features": ["x"]},
        }
        for run_id, record in malformed.items():
            d = os.path.join(self.runs, run_id)
            os.makedirs(d)
            write(os.path.join(d, "record.json"), record)

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(["--root", self.runs, "ls"]), 0)
        listing = out.getvalue()
        self.assertEqual(err.getvalue(), "")
        for run_id in malformed:
            self.assertIn(run_id, listing)
        self.assertEqual(listing.count("incomplete"), len(malformed))

        for run_id in malformed:
            with self.subTest(run_id=run_id, format="text"):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--root", self.runs, "show", run_id])
                self.assertEqual(code, 1)
                self.assertEqual(err.getvalue(), "")
                self.assertIn("lifecycle incomplete", out.getvalue())
                self.assertIn("record does not conform", out.getvalue())
                self.assertIn("retained: record.json", out.getvalue())
                self.assertNotIn("run       ", out.getvalue())

            with self.subTest(run_id=run_id, format="json"):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--root", self.runs, "show", run_id,
                                     "--json"])
                self.assertEqual(code, 1)
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["lifecycle"], "incomplete")
                self.assertEqual(payload["run_id"], run_id)
                self.assertEqual(payload["inventory"], ["record.json"])
                self.assertIn("record does not conform", payload["reasons"][0])
                self.assertNotIn("features", payload)
                self.assertIn("non-passing", err.getvalue())

    def test_list_and_show_fall_back_for_malformed_attempt_streams(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from harness_workbench import cli, interrupt

        cases = {"invalid-json": "not json\n", "non-object": "[]\n"}
        run_ids = []
        for label, body in cases.items():
            rec = self.run_spec([], name="%s-attempts.json" % label)
            run_id = rec["run_id"]
            run_ids.append(run_id)
            with open(os.path.join(self.runs, run_id, "attempts.jsonl"),
                      "w", encoding="utf-8") as fh:
                fh.write(body)
            observed = interrupt.inspect_state(os.path.join(self.runs, run_id))
            self.assertEqual(observed["state"], interrupt.INCOMPLETE)
            self.assertIsNone(observed["record"])

        rec = self.run_spec([], name="missing-exit-attempts.json")
        run_id = rec["run_id"]
        run_ids.append(run_id)
        attempts_path = os.path.join(self.runs, run_id, "attempts.jsonl")
        attempts = [json.loads(line) for line in read_text(attempts_path).splitlines()
                    if line.strip()]
        attempts[0].pop("exit")
        with open(attempts_path, "w", encoding="utf-8") as fh:
            for attempt in attempts:
                fh.write(json.dumps(attempt) + "\n")
        observed = interrupt.inspect_state(os.path.join(self.runs, run_id))
        self.assertEqual(observed["state"], interrupt.INCOMPLETE)
        self.assertIsNone(observed["record"])
        self.assertIn("missing 'exit'", observed["reasons"][0])

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(cli.main(["--root", self.runs, "ls"]), 0)
        self.assertEqual(err.getvalue(), "")
        for run_id in run_ids:
            self.assertIn(run_id, out.getvalue())

        for run_id in run_ids:
            with self.subTest(run_id=run_id, format="text"):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--root", self.runs, "show", run_id])
                self.assertEqual(code, 1)
                self.assertEqual(err.getvalue(), "")
                self.assertIn("lifecycle incomplete", out.getvalue())
                self.assertIn("retained:", out.getvalue())
                self.assertNotIn("\nattempts\n", out.getvalue())

            with self.subTest(run_id=run_id, format="json"):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--root", self.runs, "show", run_id,
                                     "--json"])
                self.assertEqual(code, 1)
                payload = json.loads(out.getvalue())
                self.assertEqual(payload["lifecycle"], "incomplete")
                self.assertEqual(payload["run_id"], run_id)
                self.assertIn("attempts.jsonl", payload["inventory"])
                self.assertTrue(payload["reasons"])
                self.assertIn("non-passing", err.getvalue())

    def test_bad_timeout_is_refused_before_spawning(self):
        from harness_workbench import interrupt
        with self.assertRaisesRegex(interrupt.InterruptError, "greater than zero"):
            interrupt.campaign(self.spec([], name="bad-timeout.json"), self.runs,
                               self.interrupts, timeout_seconds=0)

    def test_instrument_store_cannot_overlap_the_run_store(self):
        from harness_workbench import interrupt
        path = self.spec([], name="overlap.json")
        with self.assertRaisesRegex(interrupt.InterruptError, "must not overlap"):
            interrupt.campaign(path, self.runs,
                               os.path.join(self.runs, "campaigns"),
                               timeout_seconds=5)


class TestCampaignStoreBoundaries(Base):
    """Campaign manifests and run evidence never share a directory tree."""

    def test_steady_rejects_equal_stores_before_creating_evidence(self):
        from harness_workbench import steady

        path = self.spec([], name="same-store-steady.json")
        self.assertFalse(os.path.exists(self.runs))
        with self.assertRaisesRegex(steady.SteadyError, "must not overlap"):
            steady.campaign(path, self.runs, self.runs)
        self.assertFalse(os.path.exists(self.runs),
                         "setup refusal must not leave campaign or run evidence")

    def test_nesting_is_rejected_in_both_directions_without_partial_stores(self):
        from harness_workbench import steady

        path = self.spec([], name="nested-stores.json")
        cases = (
            (os.path.join(self.tmp, "outer-runs"),
             os.path.join(self.tmp, "outer-runs", "steadies")),
            (os.path.join(self.tmp, "outer-steadies", "runs"),
             os.path.join(self.tmp, "outer-steadies")),
        )
        for runs_root, campaign_root in cases:
            with self.subTest(runs_root=runs_root,
                              campaign_root=campaign_root):
                with self.assertRaisesRegex(steady.SteadyError,
                                            "must not overlap"):
                    steady.campaign(path, runs_root, campaign_root)
                self.assertFalse(os.path.exists(runs_root))
                self.assertFalse(os.path.exists(campaign_root))

    @unittest.skipUnless(hasattr(os, "symlink"), "requires symlink support")
    def test_realpath_check_rejects_a_symlink_alias(self):
        from harness_workbench import steady

        real_store = os.path.join(self.tmp, "real-store")
        alias = os.path.join(self.tmp, "store-alias")
        os.makedirs(real_store)
        try:
            os.symlink(real_store, alias)
        except OSError as e:
            self.skipTest("cannot create symlink: %s" % e)

        path = self.spec([], name="alias-stores.json")
        with self.assertRaisesRegex(steady.SteadyError, "must not overlap"):
            steady.campaign(path, real_store, alias)
        self.assertEqual(os.listdir(real_store), [],
                         "alias refusal must not create campaign or run evidence")

    def test_every_manifest_producer_uses_the_shared_boundary(self):
        from harness_workbench import (blast, catch, efficacy, interrupt, replay,
                         sensitivity, sweep)

        path = self.spec(["freeze"], name="all-campaign-stores.json")
        subject = self.run_spec(["freeze"], name="existing-subject.json")
        before = sorted(os.listdir(self.runs))
        cases = (
            ("sweep", sweep.SweepError,
             lambda: sweep.run_sweep(path, self.runs, self.runs)),
            ("blast", blast.BlastError,
             lambda: blast.campaign(path, self.runs, self.runs)),
            ("catch", catch.CatchError,
             lambda: catch.campaign(path, self.runs, self.runs)),
            ("efficacy", efficacy.EfficacyError,
             lambda: efficacy.campaign(path, self.runs, self.runs)),
            ("replay", replay.ReplayError,
             lambda: replay.replay(self.runs, subject["run_id"], self.runs,
                                   source_dir=self.tmp)),
            ("sensitivity", sensitivity.SensitivityError,
             lambda: sensitivity.campaign(self.runs, subject["run_id"],
                                           self.runs)),
            ("interrupt", interrupt.InterruptError,
             lambda: interrupt.campaign(path, self.runs, self.runs,
                                        timeout_seconds=5)),
        )
        for name, error, invoke in cases:
            with self.subTest(campaign=name):
                with self.assertRaisesRegex(error, "must not overlap"):
                    invoke()
                self.assertEqual(sorted(os.listdir(self.runs)), before,
                                 "%s left partial evidence" % name)


class TestInvertsDeclaration(Base):
    """The manifest field must fail closed at load, not at campaign time.

    A typo that surfaced during a campaign would print as 'feature survived
    inversion' -- an inert-feature finding manufactured by a misspelling.
    """

    def _manifest(self, **over):
        d = os.path.join(self.feat_dir, "decl")
        os.makedirs(d, exist_ok=True)
        base = {"name": "decl", "version": "0.1.0", "power": "annotate",
                "seams": ["after_step"], "seam_contract": ">=0.2.0,<0.3.0"}
        base.update(over)
        write(os.path.join(d, "FEATURE.json"), base)
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def after_step(step, result, ctx):\n    return {}\n")
        return d

    def test_unknown_seam_is_rejected(self):
        d = self._manifest(inverts={"seam": "before_run", "source": "invert.py",
                                    "decision": "x"})
        with open(os.path.join(d, "invert.py"), "w") as fh:
            fh.write("")
        with self.assertRaises(features.FeatureError):
            features.read_manifest(d)

    def test_missing_source_file_is_rejected(self):
        d = self._manifest(inverts={"seam": "after_step", "source": "nope.py",
                                    "decision": "x"})
        with self.assertRaises(features.FeatureError):
            features.read_manifest(d)

    def test_missing_decision_prose_is_rejected(self):
        """A declared inversion with no stated decision is a mutant with no
        hypothesis -- the campaign could run it and report nothing readable."""
        d = self._manifest(inverts={"seam": "after_step", "source": "invert.py"})
        with open(os.path.join(d, "invert.py"), "w") as fh:
            fh.write("")
        with self.assertRaises(features.FeatureError):
            features.read_manifest(d)

    def test_absent_is_legal_and_means_no_claim(self):
        d = self._manifest()
        self.assertIsNone(features.read_manifest(d).inverts)

    def test_the_claim_travels_in_the_record(self):
        """A reader should not need the feature tree to see what was claimed."""
        rec = self.run_spec(["freeze"], name="decl.json")
        f = [x for x in rec["features"] if x["name"] == "freeze"][0]
        self.assertEqual(f["inverts"]["seam"], "on_spec_loaded")


class TestIntentDeclaration(Base):
    """`intent` says what a feature is FOR, and a typo must not buy silence.

    An unrecognised value read as absent would be worse than no field: it
    would look declared to a human reading the manifest and count as
    undeclared everywhere it matters.
    """

    def _manifest(self, **over):
        d = os.path.join(self.feat_dir, "kind")
        os.makedirs(d, exist_ok=True)
        base = {"name": "kind", "version": "0.1.0", "power": "annotate",
                "seams": ["after_step"], "seam_contract": ">=0.2.0,<0.3.0"}
        base.update(over)
        write(os.path.join(d, "FEATURE.json"), base)
        with open(os.path.join(d, "feature.py"), "w") as fh:
            fh.write("def after_step(step, result, ctx):\n    return {}\n")
        return d

    def test_unknown_intent_is_rejected(self):
        d = self._manifest(intent="instrumnet")
        with self.assertRaises(features.FeatureError):
            features.read_manifest(d)

    def test_absent_is_legal_for_a_synthesised_feature(self):
        """Required of installed features by TestInstalledManifests, not of
        every manifest that can be constructed -- a fixture built inside a
        test has no reason to answer why it exists."""
        self.assertIsNone(features.read_manifest(self._manifest()).intent)

    def test_both_values_load(self):
        for value in features.INTENTS:
            with self.subTest(intent=value):
                m = features.read_manifest(self._manifest(intent=value))
                self.assertEqual(m.intent, value)

    def test_intent_travels_in_the_record(self):
        rec = self.run_spec(["timing", "freeze"], name="kind.json")
        got = {f["name"]: f["intent"] for f in rec["features"]}
        self.assertEqual(got, {"timing": "instrument",
                               "freeze": "capability"})


class TestDeclaredFeaturesRoot(Base):
    """A spec can name where its features live.

    It exists because the alternative in practice was exporting
    $HWB_FEATURES before every command -- an undeclared environment variable
    deciding the feature set, which is the precise failure this design
    exists to prevent, and it had become the documented way to use the tool.
    """

    def setUp(self):
        super().setUp()
        # Move the features somewhere the default would never find.
        self.elsewhere = os.path.join(self.tmp, "elsewhere", "feats")
        shutil.copytree(self.feat_dir, self.elsewhere)
        shutil.rmtree(self.feat_dir)
        os.environ.pop("HWB_FEATURES", None)

    def spec_with_root(self, root, name):
        p = os.path.join(self.tmp, name)
        write(p, {
            "schema": "hwbspec/v0.1", "run_class": "discovery",
            "features_root": root,
            "features": [{"name": "timing"}],
            "steps": [{"id": "01", "argv": ["./probe.sh"],
                       "inputs": ["in.txt"]}],
        })
        return p

    def test_a_declared_root_is_used(self):
        sp = specmod.load(self.spec_with_root("elsewhere/feats", "fr1.json"))
        rec = runner.execute(sp, features.resolve(sp), self.runs)
        self.assertEqual(rec["status"], "completed")

    def test_it_resolves_relative_to_the_spec_not_the_cwd(self):
        """Same reason `steps[].inputs` do: the declaration travels with the
        file, so running from another folder cannot change the condition."""
        sub = os.path.join(self.tmp, "nested")
        os.makedirs(sub)
        shutil.copy(os.path.join(self.tmp, "probe.sh"), sub)
        os.chmod(os.path.join(sub, "probe.sh"), 0o755)
        with open(os.path.join(sub, "in.txt"), "w") as fh:
            fh.write("input\n")
        p = os.path.join(sub, "fr2.json")
        write(p, {
            "schema": "hwbspec/v0.1", "run_class": "discovery",
            "features_root": "../elsewhere/feats",
            "features": [{"name": "timing"}],
            "steps": [{"id": "01", "argv": ["./probe.sh"],
                       "inputs": ["in.txt"]}],
        })
        sp = specmod.load(p)
        self.assertEqual(rec_status(sp, self.runs), "completed")

    def test_the_env_var_still_wins(self):
        """The campaigns stage mutant trees and point runs at them -- the one
        case where the caller legitimately knows better than the file."""
        os.environ["HWB_FEATURES"] = self.elsewhere
        try:
            sp = specmod.load(self.spec_with_root("nowhere-at-all", "fr3.json"))
            self.assertEqual(rec_status(sp, self.runs), "completed")
        finally:
            os.environ.pop("HWB_FEATURES", None)

    def test_the_root_is_digested(self):
        """It determines WHICH CODE RUNS, so two specs differing only here
        are different experiments and must not share a digest."""
        a = specmod.load(self.spec_with_root("elsewhere/feats", "fr4.json"))
        b = specmod.load(self.spec_with_root("elsewhere/other", "fr5.json"))
        self.assertNotEqual(a.digest, b.digest)

    def test_absent_keeps_the_old_default(self):
        p = os.path.join(self.tmp, "fr6.json")
        write(p, {
            "schema": "hwbspec/v0.1", "run_class": "discovery",
            "features": [{"name": "timing"}],
            "steps": [{"id": "01", "argv": ["./probe.sh"], "inputs": ["in.txt"]}],
        })
        sp = specmod.load(p)
        self.assertIsNone(sp.features_root)
        with self.assertRaises(features.FeatureError):
            features.resolve(sp)          # <spec dir>/features, which is gone


def rec_status(sp, runs):
    return runner.execute(sp, features.resolve(sp), runs)["status"]


class TestDiffSeesOutput(Base):
    """`diff` was blind to step output for the whole of its existence.

    These are the standing guard on the fix. Family 9 reports the blindness
    campaign-side; this stops it coming back silently.
    """

    def two_runs(self, name):
        a = self.run_spec(["timing"], name=name)
        b = self.run_spec(["timing"], name=name)
        return a["run_id"], b["run_id"]

    def test_identical_runs_are_equivalent(self):
        from harness_workbench import diff as d
        a, b = self.two_runs("d1.json")
        res = d.compare(d.load_run(self.runs, a), d.load_run(self.runs, b))
        self.assertTrue(res["equivalent"], res)
        self.assertEqual(res["output_differences"], [])

    def test_changed_output_is_not_equivalent(self):
        """The case that failed: same harness, different bytes."""
        from harness_workbench import diff as d
        a, b = self.two_runs("d2.json")
        target = None
        for dp, _, fns in os.walk(os.path.join(self.runs, b, "steps")):
            for fn in fns:
                if fn == "stdout.bin":
                    target = os.path.join(dp, fn)
        self.assertIsNotNone(target)
        with open(target, "ab") as fh:
            fh.write(b"different\n")

        res = d.compare(d.load_run(self.runs, a), d.load_run(self.runs, b))
        self.assertFalse(res["equivalent"])
        self.assertTrue(res["output_differences"])
        # The harness axis stays clean -- that is the point of two axes.
        self.assertTrue(res["harness_equivalent"], res["differences"])

    def test_digests_come_from_the_bytes_not_from_integrity(self):
        """An integrity entry is a CLAIM about the bytes written at close.

        Comparing two claims cannot see output that changed after the claim
        was made -- which would put the original defect back one layer down,
        where the fix merely LOOKS present.
        """
        from harness_workbench import diff as d
        a, _ = self.two_runs("d3.json")
        rd = os.path.join(self.runs, a)
        before = d.output_digests(rd)
        target = sorted(before)[0]
        with open(os.path.join(rd, target), "ab") as fh:
            fh.write(b"x")
        after = d.output_digests(rd)
        self.assertNotEqual(before[target], after[target])

    def test_a_run_without_steps_is_unknown_not_matching(self):
        from harness_workbench import diff as d
        a, b = self.two_runs("d4.json")
        shutil.rmtree(os.path.join(self.runs, b, "steps"))
        res = d.compare(d.load_run(self.runs, a), d.load_run(self.runs, b))
        self.assertFalse(res["output_known"])
        self.assertFalse(res["equivalent"])


class TestSensitivity(Base):
    """Family 9 -- the instrument measuring itself.

    These tests are deliberately about the PROBE HARNESS, not about which
    checkers currently pass. Asserting "diff is blind to output" here would
    freeze a defect into the suite as expected behaviour; the campaign
    reports that, and the campaign is where it belongs.
    """

    def campaign(self):
        from harness_workbench import sensitivity as sens
        rec = self.run_spec(["freeze", "timing"], name="sens.json")
        return sens.campaign(self.runs, rec["run_id"],
                             os.path.join(self.tmp, "sensitivity"))

    def test_the_positive_control_is_detected(self):
        """If the control ever misses, every other verdict is meaningless.

        This is the test that makes the family's OTHER results readable: a
        probe harness that mutates nothing reports 'detected' for a checker
        that has stopped looking, and the control is what separates those.
        """
        from harness_workbench import sensitivity as sens
        man = self.campaign()
        ctl = [r for r in man["probes"] if r["control"]]
        self.assertTrue(ctl, "the campaign must ship a positive control")
        for r in ctl:
            self.assertEqual(r["verdict"], sens.DETECTED, r["detail"])
        self.assertTrue(sens.summarise(man)["control_ok"])

    def test_every_probe_reaches_a_verdict(self):
        """`errored` is not a verdict about a checker -- it is a broken probe."""
        from harness_workbench import sensitivity as sens
        man = self.campaign()
        for r in man["probes"]:
            self.assertIn(r["verdict"], (sens.DETECTED, sens.MISSED),
                          "%s errored: %s" % (r["probe"], r["detail"]))

    def test_it_never_touches_the_run_it_probes(self):
        """The catch campaign's third defect was damaging its own workload.

        A probe that mutates records must do it to a copy, or the family
        that measures the instrument becomes the thing that breaks it.
        """
        from harness_workbench import sensitivity as sens
        rec = self.run_spec(["freeze", "timing"], name="sens2.json")
        d = os.path.join(self.runs, rec["run_id"])
        before = {}
        for dirpath, _, files in os.walk(d):
            for fn in files:
                p = os.path.join(dirpath, fn)
                with open(p, "rb") as fh:
                    before[p] = (fh.read(), os.stat(p).st_mode)

        sens.campaign(self.runs, rec["run_id"],
                      os.path.join(self.tmp, "sensitivity2"))

        for p, (body, mode) in before.items():
            self.assertTrue(os.path.isfile(p), "probe deleted %s" % p)
            with open(p, "rb") as fh:
                self.assertEqual(fh.read(), body, "probe edited %s" % p)
            self.assertEqual(os.stat(p).st_mode, mode,
                             "probe changed the mode of %s" % p)

    def test_a_blind_checker_is_named_by_tool_not_by_probe(self):
        """The actionable unit is the tool. Two probes missing on one tool
        is one blind tool, and a summary that says otherwise inflates."""
        from harness_workbench import sensitivity as sens
        man = self.campaign()
        res = sens.summarise(man)
        for name in res["blind_checkers"]:
            self.assertIn(name, {r["checker"] for r in man["probes"]})
        self.assertEqual(len(res["blind_checkers"]),
                         len(set(res["blind_checkers"])))

    def test_every_public_verdict_engine_is_mechanically_accounted_for(self):
        """The CLI/engine registry is independent of the probe declarations.

        Adding a checker without a probe must create a red row; otherwise the
        newest checker is exactly the one a hand-maintained probe map omits.
        """
        from harness_workbench import commands, sensitivity as sens
        man = self.campaign()
        reported = {r["checker"] for r in man["probes"]}
        universe = set(commands.public_verdict_engines())
        self.assertEqual(reported, universe)
        self.assertEqual(set(man["public_verdict_engines"]), universe)
        n = len(universe)
        self.assertEqual(sens.summarise(man)["checker_coverage"], "%d/%d" % (n, n))

    def test_a_metadata_registered_verdict_command_is_automatically_unprobed(self):
        from unittest import mock
        from types import SimpleNamespace
        from harness_workbench import cli, commands, sensitivity as sens

        rec = self.run_spec(["freeze", "timing"], name="sens-new.json")
        added = {"new-verdict": {"help": "new public verdict",
                                  "verdict_engine": True}}
        with mock.patch.dict(commands.COMMANDS, added):
            man = sens.campaign(self.runs, rec["run_id"],
                                os.path.join(self.tmp, "sens-new"))
            summary = sens.summarise(man)
            with mock.patch.object(cli.sensmod, "campaign", return_value=man):
                code = cli.cmd_sensitivity(SimpleNamespace(
                    root=self.runs, run_id=rec["run_id"],
                    sensitivity=os.path.join(self.tmp, "unused")))
        rows = [r for r in summary["unprobed"]
                if r["checker"] == "new-verdict"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], sens.UNPROBED)
        self.assertEqual(code, 1)
        n = len(commands.public_verdict_engines())
        self.assertEqual(summary["checker_coverage"], "%d/%d" % (n, n + 1))

    def _boundary_probe(self, probe, patch_target, replacement, tag):
        from unittest import mock
        from harness_workbench import sensitivity as sens

        rec = self.run_spec(["freeze", "timing"], name="sens-%s.json" % tag)
        work = os.path.join(self.tmp, "boundary-%s" % tag)
        os.makedirs(work)
        with mock.patch(patch_target, replacement):
            verdict, detail = probe(self.runs, rec["run_id"], work)
        self.assertEqual(verdict, sens.MISSED, detail)

    def test_blast_probe_fails_when_survival_acquisition_is_disabled(self):
        from harness_workbench import sensitivity as sens

        self._boundary_probe(
            sens._probe_blast_broken_survival,
            "harness_workbench.blast._survival",
            lambda *a, **k: {"completed": True, "conforms": True,
                             "others_intact": True, "steps_retained": True},
            "blast")

    def test_catch_probe_fails_when_drift_acquisition_is_disabled(self):
        from harness_workbench import sensitivity as sens

        self._boundary_probe(
            sens._probe_catch_missed_declared_drift,
            "harness_workbench.catch._drift_reported", lambda record: None, "catch")

    def test_efficacy_probe_fails_when_difference_classifier_is_disabled(self):
        from harness_workbench import sensitivity as sens

        self._boundary_probe(
            sens._probe_efficacy_surviving_opposite,
            "harness_workbench.efficacy._differs",
            lambda *a, **k: (True, "false kill from disabled classifier"),
            "efficacy")

    def test_steady_probe_fails_when_pair_classifier_is_disabled(self):
        from harness_workbench import sensitivity as sens, steady

        self._boundary_probe(
            sens._probe_steady_moving_baseline,
            "harness_workbench.steady.compare_pair",
            lambda *a, **k: {"verdict": steady.STABLE,
                             "moving_axes": [], "unallowed_axes": []},
            "steady")

    def test_a_missed_probe_makes_the_command_exit_nonzero(self):
        """A family that reports a blind checker and exits 0 is a dashboard.

        Wired to the exit code so a blind checker can fail a script rather
        than needing someone to read the table.
        """
        from harness_workbench import cli
        rec = self.run_spec(["freeze", "timing"], name="sens3.json")
        code = cli.main(["--root", self.runs,
                         "--sensitivity", os.path.join(self.tmp, "sens3"),
                         "sensitivity", rec["run_id"]])
        from harness_workbench import sensitivity as sens
        man = sens.campaign(self.runs, rec["run_id"],
                            os.path.join(self.tmp, "sens3b"))
        expected = 1 if sens.summarise(man)["missed"] else 0
        self.assertEqual(code, expected)


def _read_record(runs, run_id):
    with open(os.path.join(runs, run_id, "record.json"), encoding="utf-8") as fh:
        return json.load(fh)


class TestRedact(Base):
    """The first feature whose subject is the captured output itself.

    Redact still exposes the missing `mutate` power and filesystem-effects
    measurement. What is enforceable without inventing that power is the
    record/artifact agreement: after every hook, the sealed attempt must
    describe the bytes that actually remain on disk.
    """

    SENTINEL = "redaction-fixture-ALPHABETSOUP"
    PATTERN = "redaction-fixture-[A-Z]{12}"

    def leaky(self):
        p = os.path.join(self.tmp, "leak.sh")
        with open(p, "w") as fh:
            fh.write("#!/bin/sh\necho 'fixture %s'\necho 'again %s'\n"
                     % (self.SENTINEL, self.SENTINEL))
        os.chmod(p, 0o755)
        return [{"id": "01", "argv": ["./leak.sh"], "inputs": []}]

    def run_leaky(self, feats, name):
        return self.run_spec(feats, name=name, steps=self.leaky())

    def captured(self, rec, n=0):
        p = os.path.join(self.runs, rec["run_id"], "steps", "01",
                         "attempts", str(n), "stdout.bin")
        with open(p, "rb") as fh:
            return fh.read()

    def cfg(self, **over):
        c = {"patterns": [self.PATTERN]}
        c.update(over)
        return [{"name": "redact", "config": c}]

    def test_a_declared_pattern_does_not_survive_to_disk(self):
        rec = self.run_leaky(self.cfg(), "r1.json")
        self.assertValidRecord(rec)
        out = self.captured(rec)
        self.assertNotIn(self.SENTINEL.encode(), out)
        self.assertIn(b"[REDACTED]", out)

    def test_detached_the_sentinel_stays(self):
        """THE REJECTION TEST. Without it, 'no sentinel in the output' and
        'no sentinel in this workload' are the same result."""
        rec = self.run_leaky([], "r2.json")
        self.assertIn(self.SENTINEL.encode(), self.captured(rec))

    def test_every_attempt_is_scrubbed_not_only_the_first(self):
        """Nested under `sample`, so three draws exist by the time redact
        runs. A secret in the second draw is no less a secret."""
        feats = [{"name": "sample", "config": {"n": 3}}] + self.cfg()
        rec = self.run_leaky(feats, "r3.json")
        for n in range(3):
            with self.subTest(attempt=n):
                self.assertNotIn(self.SENTINEL.encode(), self.captured(rec, n))

    def test_reporting_what_it_did_is_a_breach(self):
        """The finding this feature was built to produce. A wrap has no
        declared channel into the record, so the only way to say what it
        changed is to reach through ctx -- and that is a breach. The honest
        version of this feature is the one that fails the check."""
        from harness_workbench import confine
        rec = self.run_leaky(self.cfg(report=True), "r4.json")
        res = confine.assess(os.path.join(self.runs, rec["run_id"]))
        row = [r for r in res["features"] if r["feature"] == "redact"][0]
        self.assertEqual(row["verdict"], confine.BREACHED)

    def test_filesystem_rewrite_is_outside_record_power_confinement(self):
        """An explicit boundary, pending the separate effects campaign.

        `confine` measures record-power channels collected by the dispatcher;
        it does not attribute filesystem writes. Calling this clean is scoped
        to that relation and must not be worded as filesystem confinement.
        """
        from harness_workbench import confine
        rec = self.run_leaky(self.cfg(), "r5.json")
        self.assertNotIn(self.SENTINEL.encode(), self.captured(rec))
        res = confine.assess(os.path.join(self.runs, rec["run_id"]))
        row = [r for r in res["features"] if r["feature"] == "redact"][0]
        self.assertEqual(row["verdict"], confine.CLEAN)

    def test_final_attempt_describes_the_rewritten_artifact(self):
        """Close-time descriptors are over final bytes, not capture-time bytes."""
        from harness_workbench.canon import digest_file

        rec = self.run_leaky(self.cfg(), "r6.json")
        d = os.path.join(self.runs, rec["run_id"])
        ats = [json.loads(l) for l
               in read_text(os.path.join(d, "attempts.jsonl")).splitlines()
               if l.strip()]
        stdout = os.path.join(d, "steps", "01", "attempts", "0", "stdout.bin")
        self.assertEqual(ats[0]["stdout_bytes"], os.path.getsize(stdout))
        self.assertEqual(ats[0]["stdout_digest"], digest_file(stdout))
        self.assertEqual(rec["attempt_artifact_contract"],
                         runner.ATTEMPT_ARTIFACT_CONTRACT)
        self.assertValidRecord(rec)

    def test_pre_sealing_redaction_remains_complete_and_readable(self):
        """Legacy counts described capture, before a wrap rewrote the bytes.

        This is the historical failure shape: redaction made the final stdout
        artifact smaller, the old attempt retained its provisional byte count,
        no artifact contract or stream digests claimed final sealing, and the
        integrity inventory was clean. Readers must keep the store witness
        without upgrading that old count into a final-size assertion.
        """
        import io
        from contextlib import redirect_stdout
        from harness_workbench import cli, interrupt
        from harness_workbench.canon import canon_bytes

        rec = self.run_leaky(self.cfg(), "legacy-redaction.json")
        d = os.path.join(self.runs, rec["run_id"])
        attempts_path = os.path.join(d, "attempts.jsonl")
        ats = [json.loads(line) for line in read_text(attempts_path).splitlines()
               if line.strip()]

        captured = ("token %s\nagain %s\n" %
                    (self.SENTINEL, self.SENTINEL)).encode("utf-8")
        final = self.captured(rec)
        self.assertLess(len(final), len(captured))
        rec.pop("attempt_artifact_contract")
        ats[0]["stdout_bytes"] = len(captured)
        for attempt in ats:
            attempt.pop("stdout_digest")
            attempt.pop("stderr_digest")

        with open(os.path.join(d, "record.json"), "wb") as fh:
            fh.write(canon_bytes(rec))
        with open(attempts_path, "w", encoding="utf-8") as fh:
            for attempt in ats:
                fh.write(json.dumps(attempt, sort_keys=True) + "\n")
        runner._write_integrity(d)

        self.assertEqual(runner.verify(d)["state"], "clean")
        conform.validate_record(rec, ats, run_dir=d)  # must not raise
        self.assertEqual(interrupt.inspect_state(d)["state"],
                         interrupt.COMPLETE)

        shown = io.StringIO()
        with redirect_stdout(shown):
            self.assertEqual(cli.main(["--root", self.runs, "show",
                                       rec["run_id"]]), 0)
        self.assertIn("lifecycle complete", shown.getvalue())

        verified = io.StringIO()
        with redirect_stdout(verified):
            self.assertEqual(cli.main(["--root", self.runs, "verify",
                                       rec["run_id"]]), 0)
        self.assertIn("complete", verified.getvalue())
        self.assertIn("conforms: yes", verified.getvalue())

        with self.assertRaisesRegex(conform.NonConforming, "never collapsed"):
            conform.validate_record(rec, [], run_dir=d)
        os.remove(os.path.join(d, "steps", "01", "attempts", "0",
                               "stderr.bin"))
        with self.assertRaisesRegex(conform.NonConforming,
                                    "has no stored stderr.bin"):
            conform.validate_record(rec, ats, run_dir=d)

    def test_store_agreement_rejects_a_post_close_artifact_rewrite(self):
        """The descriptor is an invariant a reader can independently check."""
        rec = self.run_leaky(self.cfg(), "r6b.json")
        d = os.path.join(self.runs, rec["run_id"])
        stdout = os.path.join(d, "steps", "01", "attempts", "0", "stdout.bin")
        with open(stdout, "ab") as fh:
            fh.write(b"post-close mutation\n")
        ats = [json.loads(l) for l
               in read_text(os.path.join(d, "attempts.jsonl")).splitlines()
               if l.strip()]
        with self.assertRaisesRegex(conform.NonConforming,
                                    "records stdout_bytes"):
            conform.validate_record(rec, ats, run_dir=d)

    def test_sealed_attempt_requires_and_checks_its_digest(self):
        rec = self.run_leaky(self.cfg(), "r6c.json")
        d = os.path.join(self.runs, rec["run_id"])
        ats = [json.loads(line) for line
               in read_text(os.path.join(d, "attempts.jsonl")).splitlines()
               if line.strip()]

        ats[0]["stdout_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(conform.NonConforming,
                                    "records stdout_digest"):
            conform.validate_record(rec, ats, run_dir=d)
        ats[0].pop("stdout_digest")
        with self.assertRaisesRegex(conform.NonConforming, "but lacks"):
            conform.validate_record(rec, ats, run_dir=d)

    def test_an_output_only_difference_states_its_reason(self):
        """Regression. `efficacy._differs` joined only the harness-field
        differences, so a feature whose whole effect is in the captured bytes
        was killed with an empty detail -- a verdict with no reason attached.
        """
        from harness_workbench import efficacy
        # The same configuration twice, so nothing in the harness fields can
        # differ, and then ONLY the captured bytes are perturbed. Comparing a
        # redacted run against an unredacted one would not test this: those
        # also differ by spec and feature set, and the harness differences
        # alone fill the detail string.
        a = self.run_leaky([], "r7.json")
        b = self.run_leaky([], "r7.json")
        p = os.path.join(self.runs, b["run_id"], "steps", "01",
                         "attempts", "0", "stdout.bin")
        with open(p, "ab") as fh:
            fh.write(b"one more line\n")

        moved, why = efficacy._differs(self.runs, a["run_id"], b["run_id"])
        self.assertTrue(moved, "an output-only change was not noticed at all")
        self.assertTrue(why.strip(), "killed with no stated reason")
        self.assertIn("output", why)


class TestPackageIdentity(unittest.TestCase):
    """Distribution, import package, and command are deliberately distinct."""

    def test_declared_identity_has_no_legacy_import_namespace(self):
        project = read_text(os.path.join(ROOT, "pyproject.toml"))
        self.assertIn('name = "harness-workbench"', project)
        self.assertIn('hwb = "harness_workbench.cli:main"', project)
        # Structural, not literal: the claim is that package data is declared
        # under the import namespace and not the legacy one. Pinning the exact
        # formatting made this fail whenever a shipped tree was added, which is
        # not what the test is about.
        import tomllib
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            package_data = tomllib.load(fh)["tool"]["setuptools"]["package-data"]
        self.assertEqual(["harness_workbench"], list(package_data))
        for glob in ("builtin/*/*.py", "builtin/*/FEATURE.json"):
            self.assertIn(glob, package_data["harness_workbench"])
        self.assertTrue(os.path.isfile(
            os.path.join(ROOT, "src", "harness_workbench", "__init__.py")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "src", "hwb")))

    def test_builtin_locator_uses_the_import_namespace(self):
        self.assertEqual(features.BUILTIN, "harness_workbench:builtin")

    def test_subject_tree_ships_runnable_and_is_never_imported(self):
        from harness_workbench import subject_tree

        shipped = subject_tree.subject_files()
        self.assertTrue(shipped, "no subject tree shipped with the package")
        # The tree is only useful if a spec, its adapter, and something to run
        # all arrive together.
        for required in ("adapters.py", "oracles.py", "runner.py",
                         "run_subject.sh", "claude.json", "pin.json",
                         "model_selection.json", "README.md"):
            self.assertIn(required, shipped)

        # DATA, NEVER IMPORTED. Core reaching into the subject tree would make
        # five externally-pinned third-party integrations part of the package's
        # own import graph, and a broken adapter would then break `import
        # harness_workbench` rather than one experiment.
        #
        # Parsed, and walked, rather than matched as substrings. This checked
        # `"from .subjects"` and `"import subjects\n"` against the top-level
        # modules only, which is three separate ways to miss: a subpackage was
        # never opened, `from harness_workbench.subjects import x` does not
        # contain either string, and neither does `import subjects.adapters`.
        # The rule is about the import GRAPH, so read the imports.
        import ast

        package = os.path.join(ROOT, "src", "harness_workbench")
        offenders = []
        for directory, _, filenames in os.walk(package):
            # The subject tree is allowed to import itself; it is the only
            # thing that may.
            if "subjects" in os.path.relpath(directory, package).split(os.sep):
                continue
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                tree = ast.parse(read_text(path), filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        # `level` covers `from .subjects` and `from ..subjects`.
                        name = node.module or ""
                        if name == "subjects" or name.endswith(".subjects") \
                                or name.startswith("subjects."):
                            offenders.append(f"{path}: from {name}")
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == "subjects" \
                                    or alias.name.startswith("subjects.") \
                                    or ".subjects" in alias.name:
                                offenders.append(f"{path}: import {alias.name}")
        self.assertEqual([], offenders)

    def test_subject_tree_does_not_reimplement_the_capture_primitive(self):
        # The tree carried a standalone second implementation of `capture` for
        # as long as it existed, and the two drifted: the copy synthesized exit
        # code 124 for a timeout, which is indistinguishable from a subject
        # that genuinely exits 124. Consuming the primitive fixed that once;
        # this keeps it fixed, because the cheapest way to satisfy a fifth
        # harness's odd requirement is to paste a private variant back in.
        import ast
        from harness_workbench import capture

        package = os.path.join(ROOT, "src", "harness_workbench")
        subjects = os.path.join(package, "subjects")
        reserved = set(capture.__all__)
        # Names are the weak half of this check and were once the whole of it.
        # Of the deleted common.py's ten primitive members only six collided
        # with `__all__`; `ProcessResult`, `canonical_digest`, `file_digest`
        # and `normalized_path` would all have sailed through, and so would a
        # PRIVATE variant -- which is precisely what gets pasted back in.
        #
        # So the modules a reimplementation cannot avoid importing are the
        # other half. Bounded capture needs `selectors` and `signal`; owning a
        # process group needs them too. Nothing in this tree may import them:
        # the primitive does that, once, on everyone's behalf. `subprocess` is
        # allowed only where a process is genuinely not being measured.
        #
        # WHAT THIS STILL MISSES, verified by trying it: a private helper that
        # reimplements bounded FILE reading -- the deleted `_bounded_evidence`
        # -- collides with no reserved name and imports nothing forbidden, and
        # passes. Static checks over names and imports cannot see it. Saying so
        # here rather than implying the guard is total, because a control
        # trusted past its range is worse than one known to be partial.
        forbidden_imports = {"selectors": (), "signal": (),
                             "subprocess": ("test_experiment.py", "hook.py")}
        for entry in sorted(os.listdir(subjects)):
            if not entry.endswith(".py"):
                continue
            tree = ast.parse(read_text(os.path.join(subjects, entry)))
            # ast.walk, not tree.body: a method or a nested def is still a
            # reimplementation, and module-level rebinding is the cheapest one.
            for node in ast.walk(tree):
                name = None
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    name = node.name
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    name = node.id
                if name is not None:
                    self.assertNotIn(
                        name, reserved,
                        "%s defines %s, which the capture primitive already "
                        "exports" % (entry, name))
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = (node.module if isinstance(node, ast.ImportFrom)
                              else None)
                    names = ([module] if module else
                             [a.name for a in node.names])
                    for imported in names:
                        root_module = (imported or "").split(".")[0]
                        allowed = forbidden_imports.get(root_module)
                        if allowed is not None and entry not in allowed:
                            self.fail(
                                "%s imports %s; bounded capture and process "
                                "ownership belong to harness_workbench.capture"
                                % (entry, root_module))

    def test_subject_tree_materializes_without_clobbering(self):
        from harness_workbench import subject_tree

        tmp = tempfile.mkdtemp(prefix="hb-subjects-")
        self.addCleanup(shutil.rmtree, tmp, True)
        destination = os.path.join(tmp, "materialized")
        written, skipped = subject_tree.materialize(destination)
        # The shipped files plus the apparatus manifest, which is generated
        # rather than copied and so is not one of `subject_files()`.
        self.assertEqual(
            sorted(written),
            sorted(subject_tree.subject_files() + [subject_tree.APPARATUS]))
        self.assertEqual([], skipped)

        target = os.path.join(destination, "adapters.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("# edited by whoever owns this copy\n")

        written, skipped = subject_tree.materialize(destination)
        # The manifest is rewritten every time and is the one thing the skip
        # rule must NOT protect: it describes which primitive is importable
        # now, so a stale copy of it is a false statement rather than someone's
        # edited work.
        self.assertEqual([subject_tree.APPARATUS], written)
        self.assertEqual(sorted(skipped), subject_tree.subject_files())
        self.assertEqual("# edited by whoever owns this copy\n",
                         read_text(target))

        written, skipped = subject_tree.materialize(destination, force=True)
        self.assertEqual([], skipped)
        self.assertNotIn("edited by whoever", read_text(target))

    def test_materialize_records_which_primitive_the_tree_was_cut_against(self):
        # The adapters import `capture` from the installed package, so those
        # bytes decide how a subject is measured from outside everything a spec
        # can declare. This is the baseline the adapter compares itself to.
        import json
        from harness_workbench import canon, capture, subject_tree

        tmp = tempfile.mkdtemp(prefix="hb-apparatus-")
        self.addCleanup(shutil.rmtree, tmp, True)
        destination = os.path.join(tmp, "materialized")
        subject_tree.materialize(destination)

        manifest = json.loads(
            read_text(os.path.join(destination, subject_tree.APPARATUS)))
        self.assertEqual("hwb-subject-apparatus/v0.1", manifest["schema"])
        # Both modules, not just the obvious one: `capture.digest_file` wraps
        # `canon.digest_file`, so a change in canon moves every digest in a
        # record while capture.py stays byte-identical.
        self.assertEqual({"canon", "capture"}, set(manifest["modules"]))
        for name, module in (("canon", canon), ("capture", capture)):
            self.assertEqual(
                canon.digest_file(module.__file__).split(":", 1)[1],
                manifest["modules"][name]["sha256"])

    def test_source_and_project_metadata_have_one_version_authority(self):
        import tomllib
        import harness_workbench

        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            pyproject = tomllib.load(fh)
        project = pyproject["project"]
        self.assertNotIn("version", project)
        self.assertEqual(["version"], project["dynamic"])
        self.assertEqual(
            "harness_workbench.__version__",
            pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"],
        )
        self.assertRegex(
            harness_workbench.__version__,
            r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$",
        )

    def test_release_metadata_is_explicit_and_runtime_stays_stdlib_only(self):
        import tomllib

        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as fh:
            project = tomllib.load(fh)["project"]
        self.assertEqual([], project["dependencies"])
        self.assertEqual("Apache-2.0", project["license"])
        self.assertEqual(["LICENSE", "NOTICE"], project["license-files"])
        self.assertEqual([{"name": "Garrett Davis"}], project["maintainers"])
        self.assertEqual(
            ["build==1.5.0", "setuptools==83.0.0", "twine==7.0.0"],
            project["optional-dependencies"]["release"],
        )
        self.assertEqual(
            "https://github.com/explorefailure/harness-workbench",
            project["urls"]["Repository"],
        )


class TestCompatibilityContract(unittest.TestCase):
    """Packaging and the public compatibility claim must move together."""

    def test_python_floor_matches_the_readme(self):
        pyproject = read_text(os.path.join(ROOT, "pyproject.toml"))
        readme = read_text(os.path.join(ROOT, "README.md"))
        self.assertRegex(pyproject,
                         r'(?m)^requires-python = ">=3\.11"$')
        self.assertIn("requires CPython 3.11 or newer", readme)

    def test_platform_scope_and_matrix_are_explicit(self):
        readme = read_text(os.path.join(ROOT, "README.md"))
        self.assertIn("CPython 3.11, 3.12, 3.13, and 3.14", readme)
        self.assertIn("on Linux and macOS", readme)
        self.assertIn("Windows is unsupported", readme)
        self.assertIn("Immutable-tag CI", readme)
        self.assertIn("full Linux/macOS matrix", readme)
        self.assertIn("release-final conformance record", readme)


class TestBuiltinFeatures(Base):
    """The installed distribution ships an opt-in feature tree.

    The point of shipping them is that an installed tool can demonstrate
    itself: every measurement command exists to measure features, so a
    package with none is an instrument with no subjects. The point of making
    it OPT-IN is that a mistyped root must keep failing loudly instead of
    quietly succeeding with code the author did not choose.
    """

    def test_a_spec_can_ask_for_the_shipped_tree_by_name(self):
        os.environ.pop("HWB_FEATURES", None)
        p = os.path.join(self.tmp, "b1.json")
        write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                  "features_root": features.BUILTIN,
                  "features": [{"name": "freeze"}],
                  "steps": [{"id": "01", "argv": ["./probe.sh"],
                             "inputs": ["in.txt"]}]})
        sp = specmod.load(p)
        rec = runner.execute(sp, features.resolve(sp), self.runs)
        self.assertValidRecord(rec)
        self.assertIn("freeze", rec["extras"])

    def test_builtins_are_not_a_fallback(self):
        """THE REJECTION TEST, and the reason the token exists.

        A spec that does not ask for the shipped tree must not receive it.
        Without this, a typo in `features_root` -- or a run started from an
        unexpected directory -- succeeds with different code than intended,
        and a run that works looks like a run that was right.
        """
        os.environ.pop("HWB_FEATURES", None)
        p = os.path.join(self.tmp, "b2.json")
        write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                  "features_root": "./typo-here",
                  "features": [{"name": "freeze"}],
                  "steps": [{"id": "01", "argv": ["./probe.sh"], "inputs": []}]})
        with self.assertRaises(features.FeatureError) as cm:
            features.resolve(specmod.load(p))
        self.assertIn("not found", str(cm.exception))

    def test_builtins_are_not_the_default_either(self):
        """The SECOND fallback path, and it was missed the first time.

        Resolution has two places builtins could wrongly leak in: a declared
        root that does not exist, and no declared root at all. The test above
        covers only the first. Making `builtin_root()` the final default left
        it green, which is how a control ends up covering less than its name
        suggests. A spec with no declared root and no adjacent `features/`
        must fail, naming the directory it looked in.
        """
        os.environ.pop("HWB_FEATURES", None)
        bare = tempfile.mkdtemp(prefix="hb-nofeat-")
        try:
            p = os.path.join(bare, "b5.json")
            write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                      "features": [{"name": "freeze"}],
                      "steps": [{"id": "01", "argv": ["/bin/echo", "x"],
                                 "inputs": []}]})
            with self.assertRaises(features.FeatureError) as cm:
                features.resolve(specmod.load(p))
            msg = str(cm.exception)
            self.assertIn("not found", msg)
            self.assertIn(bare, msg,
                          "the error must name the directory it searched, "
                          "not the shipped tree")
        finally:
            shutil.rmtree(bare, ignore_errors=True)

    def test_the_record_names_which_route_supplied_the_features(self):
        os.environ.pop("HWB_FEATURES", None)
        p = os.path.join(self.tmp, "b3.json")
        write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                  "features_root": features.BUILTIN,
                  "features": [{"name": "timing"}],
                  "steps": [{"id": "01", "argv": ["./probe.sh"], "inputs": []}]})
        sp = specmod.load(p)
        rec = runner.execute(sp, features.resolve(sp), self.runs)
        self.assertEqual(rec["features_source"], features.BUILTIN)

        # and the spec-adjacent route is distinguishable from it
        rec2 = self.run_spec(["timing"], name="b4.json")
        self.assertEqual(rec2["features_source"], "spec-adjacent")


class TestTheFirstRunDeadEnds(Base):
    """The two places a newcomer stops, and whether the message gets them out.

    Both were reachable in under a minute from a clean install, and both
    reported the symptom rather than the fix. They are tested rather than
    merely fixed because a message is exactly the kind of thing a later
    refactor quietly drops -- and nothing else fails when it does.
    """

    def _resolve_without_root(self, feature_name, stem):
        os.environ.pop("HWB_FEATURES", None)
        bare = tempfile.mkdtemp(prefix="hb-deadend-")
        try:
            p = os.path.join(bare, stem)
            write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                      "features": [{"name": feature_name}],
                      "steps": [{"id": "01", "argv": ["/bin/echo", "x"],
                                 "inputs": []}]})
            with self.assertRaises(features.FeatureError) as cm:
                features.resolve(specmod.load(p))
            return str(cm.exception)
        finally:
            shutil.rmtree(bare, ignore_errors=True)

    def test_a_shipped_feature_names_the_route_that_would_supply_it(self):
        # The old text offered only $HWB_FEATURES -- an override the campaigns
        # set and a human should not be typing before an ordinary run. The
        # declarative route travels with the spec and is digested, so it is
        # the one to name.
        msg = self._resolve_without_root("retry", "d1.json")
        self.assertIn("ships with hwb", msg)
        self.assertIn(features.BUILTIN, msg)

    def test_an_unknown_feature_is_not_pointed_at_the_builtin_tree(self):
        # Suggesting `harness_workbench:builtin` for a name that is not in it
        # would trade
        # one unwinnable error for a second one. It may LIST what ships;
        # it must not claim this name is there.
        msg = self._resolve_without_root("nosuch", "d2.json")
        self.assertNotIn("ships with hwb", msg)
        self.assertIn("retry", msg, "listing what ships is still useful")

    def test_suggestion_tracks_the_tree_rather_than_a_hardcoded_list(self):
        root = features.builtin_root()
        on_disk = sorted(n for n in os.listdir(root)
                         if os.path.isfile(os.path.join(root, n, "FEATURE.json")))
        self.assertEqual(sorted(features.builtin_names()), on_disk)
        # a manifest is what makes a directory a feature -- __pycache__ and
        # anything else beside it must not be offered as one
        self.assertNotIn("__pycache__", features.builtin_names())

    def test_a_spec_handed_to_a_run_id_command_says_so(self):
        from harness_workbench import cli
        p = os.path.join(self.tmp, "aspec.json")
        write(p, {"schema": "hwbspec/v0.1", "steps": [
            {"id": "01", "argv": ["/bin/echo", "x"]}]})
        args = cli.build_parser().parse_args(["confine", p])
        msg = cli.misplaced_spec(args)
        self.assertIsNotNone(msg, "a spec file where an id belongs is the "
                                  "mistake; 'no record at runs/<spec>' "
                                  "describes it one layer too low")
        self.assertIn("run id", msg)
        self.assertIn("confine", msg)

    def test_a_real_run_id_is_not_mistaken_for_a_spec(self):
        from harness_workbench import cli
        rec = self.run_spec(["timing"], name="ok.json")
        args = cli.build_parser().parse_args(["confine", rec["run_id"]])
        self.assertIsNone(cli.misplaced_spec(args))

    def test_store_ids_are_opaque_components_not_paths(self):
        from harness_workbench import cli
        for argv in (("show", "../outside"),
                     ("verify", "/absolute"),
                     ("diff", "good", "nested/run"),
                     ("interfere", "nested\\sweep")):
            with self.subTest(argv=argv):
                args = cli.build_parser().parse_args(list(argv))
                self.assertIsNotNone(cli.invalid_store_id(args))

    def test_unicode_store_id_remains_valid(self):
        from harness_workbench import cli
        args = cli.build_parser().parse_args(["show", "実験-α"])
        self.assertIsNone(cli.invalid_store_id(args))

    def test_cli_rejects_traversal_before_dispatch(self):
        import io
        from contextlib import redirect_stderr
        from harness_workbench import cli
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        write(os.path.join(outside, "record.json"), {})
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["--root", self.runs, "show", "../outside"])
        self.assertEqual(code, 2)
        self.assertIn("filesystem-safe", err.getvalue())

    def test_the_guard_covers_every_id_taking_command(self):
        # Enumerated from the parser rather than listed by hand, so a command
        # added later cannot quietly escape the check.
        from harness_workbench import cli
        spec_takers = {"run", "sweep", "blast", "catch", "efficacy",
                       "steady", "effects", "interrupt"}
        sub = [a for a in cli.build_parser()._actions
               if isinstance(a, argparse._SubParsersAction)][0]
        for name, parser in sub.choices.items():
            positionals = [a.dest for a in parser._actions if not a.option_strings]
            if not positionals or name in spec_takers:
                continue
            self.assertTrue(
                set(positionals) & set(cli.ID_ARGS),
                "%s takes %s, which the misplaced-spec guard cannot see"
                % (name, positionals))


DOCS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def doc(*parts):
    with open(os.path.join(DOCS, *parts), "r", encoding="utf-8") as fh:
        return fh.read()


class TestTheDocsDescribeThisCode(unittest.TestCase):
    """Check the published claims against the implementation.

    Docs drift silently: nothing fails when a table goes stale, and the
    reader who finds out is the one who trusted it. These assert only
    MECHANICAL facts -- lists and tables that exist in both places -- because
    those are the ones that rot without anybody touching the doc. Prose is
    left to review; a test cannot tell whether an explanation is still true.
    """

    def test_the_command_split_is_the_one_the_parser_implements(self):
        from harness_workbench import cli
        sub = [a for a in cli.build_parser()._actions
               if isinstance(a, argparse._SubParsersAction)][0]
        actual_spec = set()
        actual_id = set()
        for name, parser in sub.choices.items():
            dests = {a.dest for a in parser._actions if not a.option_strings}
            if "spec" in dests:
                actual_spec.add(name)
            elif dests & set(cli.ID_ARGS):
                actual_id.add(name)

        for path in ("README.md", os.path.join("docs", "measuring.md")):
            text = doc(path)
            for name in actual_spec:
                self.assertIn("`%s`" % name, text,
                              "%s omits the spec-taking command %s" % (path, name))
            for name in actual_id:
                self.assertIn("`%s`" % name, text,
                              "%s omits the id-taking command %s" % (path, name))
            # and the split itself is not reversed anywhere
            for name in actual_spec:
                block = text.split("take an id")[-1].split("\n\n")[0]
                self.assertNotIn("`%s`" % name, block,
                                 "%s files %s under id-taking commands"
                                 % (path, name))

    def test_the_seam_table_matches_the_dispatcher(self):
        text = doc("docs", "writing-a-feature.md")
        from harness_workbench import seams
        for seam, powers in seams.SEAM_POWERS.items():
            self.assertIn("`%s`" % seam, text,
                          "the seam table omits %s" % seam)
        # around_step is wrap-only, which is the row a reader most relies on
        row = [ln for ln in text.splitlines()
               if ln.startswith("| `around_step`")]
        self.assertTrue(row, "no around_step row in the seam table")
        self.assertIn("wrap", row[0])
        self.assertEqual(("wrap",), seams.SEAM_POWERS["around_step"],
                         "the doc says around_step is wrap-only")

    def test_every_shipped_feature_is_listed_in_the_readme(self):
        text = doc("README.md")
        for name in features.builtin_names():
            self.assertIn("`%s`" % name, text,
                          "README does not list the shipped feature %s" % name)

    def test_the_readme_does_not_overclaim_the_test_count(self):
        # Asserted as a CEILING, not equality: under-claiming is harmless and
        # equality would fail on every test added, which trains people to
        # ignore the failure. Overclaiming is the thing that is a lie.
        import re
        m = re.search(r"(\d+) tests", doc("README.md"))
        self.assertIsNotNone(m, "README no longer states a test count")
        claimed = int(m.group(1))
        # Count the suite, not this file.  The deterministic property corpus
        # deliberately lives in its own module so generated contracts do not
        # make this already-large behavioural suite harder to navigate.
        actual = unittest.defaultTestLoader.discover(
            os.path.join(ROOT, "tests")).countTestCases()
        self.assertLessEqual(claimed, actual,
                             "README claims %d tests, there are %d"
                             % (claimed, actual))

    def test_every_spec_field_the_loader_reads_is_documented(self):
        """The reference must cover the whole surface, not the popular part.

        Half these fields were documented nowhere at all -- a reader could
        only find `replicates` or `seam_timeout_ms` by reading spec.py. This
        enumerates what the loader actually consumes, so a field added later
        cannot ship undocumented.
        """
        import re
        with open(os.path.join(DOCS, "src", "harness_workbench", "spec.py"),
                  encoding="utf-8") as fh:
            source = fh.read()
        read_by_loader = set(re.findall(r'raw\.get\("(\w+)"', source))
        read_by_loader |= set(re.findall(r'raw\["(\w+)"\]', source))
        text = doc("docs", "the-spec.md")
        for field in sorted(read_by_loader):
            self.assertIn("`%s`" % field, text,
                          "the-spec.md does not document the %r field" % field)

    def test_every_relative_link_in_every_doc_resolves(self):
        """A dead link is the cheapest possible broken promise.

        Checked across the whole tree rather than doc-by-doc, because the
        links that break are the ones between files somebody moved -- which
        is exactly the case no single doc's own test would catch.

        Anchors are resolved too. A link to a heading that was later reworded
        silently lands the reader at the top of the page -- no error, nothing
        to notice -- which is worse than a dead file link, because a 404 at
        least tells them they missed.
        """
        import re

        def headings(text):
            found = set()
            for line in text.splitlines():
                if not line.startswith("#"):
                    continue
                title = line.lstrip("#").strip().lower()
                found.add(re.sub(r"\s+", "-",
                                 re.sub(r"[^\w\s-]", "", title).strip()))
            return found

        slugs = {}
        bad = []
        for root, dirs, files in os.walk(DOCS):
            dirs[:] = [d for d in dirs
                       if d not in (".git", "build", "dist", "__pycache__")]
            for f in files:
                if not f.endswith(".md"):
                    continue
                path = os.path.join(root, f)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                for m in re.finditer(r'\[[^\]]+\]\(([^)#]*)(?:#([^)]*))?\)',
                                     text):
                    target, anchor = m.group(1), m.group(2)
                    if target.startswith(("http://", "https://", "mailto:")):
                        continue
                    resolved = (os.path.normpath(os.path.join(root, target))
                                if target else path)
                    if not os.path.exists(resolved):
                        bad.append("%s -> %s" % (
                            os.path.relpath(path, DOCS), target))
                        continue
                    if not anchor or os.path.isdir(resolved):
                        continue
                    if resolved not in slugs:
                        with open(resolved, encoding="utf-8") as fh:
                            slugs[resolved] = headings(fh.read())
                    if anchor not in slugs[resolved]:
                        bad.append("%s -> %s#%s (no heading with that slug)"
                                   % (os.path.relpath(path, DOCS),
                                      target or os.path.basename(path),
                                      anchor))
        self.assertEqual([], bad, "dead relative link(s) in the docs")

    def test_documented_powers_are_the_live_ones(self):
        text = doc("docs", "writing-a-feature.md")
        from harness_workbench import seams
        for power in seams.POWERS:
            self.assertIn("`%s`" % power, text)
        self.assertIn("dormant", text.lower(),
                      "grant is dormant and the doc must say so")


class TestTheRecordDocMatchesARealRun(Base):
    """The two doc checks that need a real run, so they live with the
    fixtures that produce one rather than reimplementing them."""

    def test_every_record_key_is_documented(self):
        # Written from a real run rather than from a list, so a key the
        # runner starts emitting shows up here rather than in a reader's
        # confusion.
        p = os.path.join(self.tmp, "rec.json")
        write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                  "features": [{"name": "timing"}],
                  "steps": [{"id": "01", "argv": ["./probe.sh"],
                             "inputs": ["in.txt"]}]})
        sp = specmod.load(p)
        rec = runner.execute(sp, features.resolve(sp), self.runs)
        text = doc("docs", "the-record.md")
        for key in sorted(rec):
            self.assertIn("`%s`" % key, text,
                          "the-record.md does not document record key %r" % key)

    def test_the_documented_run_dir_layout_is_the_real_one(self):
        p = os.path.join(self.tmp, "lay.json")
        write(p, {"schema": "hwbspec/v0.1", "run_class": "discovery",
                  "features": [{"name": "timing"}],
                  "steps": [{"id": "01", "argv": ["./probe.sh"], "inputs": []}]})
        sp = specmod.load(p)
        rec = runner.execute(sp, features.resolve(sp), self.runs)
        text = doc("docs", "the-record.md")
        for name in ("record.json", "attempts.jsonl", "integrity.json",
                     "spec.json", "features"):
            self.assertTrue(
                os.path.exists(os.path.join(self.runs, rec["run_id"], name)),
                "%s is documented but not written" % name)
            self.assertIn(name, text)


DOC_FILES = (
    "README.md",
    os.path.join("docs", "measuring.md"),
    os.path.join("docs", "measuring-your-own-code.md"),
    os.path.join("docs", "campaign-manifests.md"),
    os.path.join("docs", "the-record.md"),
    os.path.join("docs", "the-spec.md"),
    os.path.join("docs", "writing-a-feature.md"),
    os.path.join("examples", "flaky", "README.md"),
)

# Transcripts the suite re-runs, keyed by the command exactly as the doc
# prints it after the "$ ". A block is registered only if it can run with no
# arguments the reader would have to invent.
_FLAKY = os.path.join("examples", "flaky")
_ATTACH = os.path.join("examples", "attaching")

# `exit` defaults to 0. `run_first` names a spec to run before the command so
# an id-taking transcript can be checked: its id replaces "$RUN".
REGISTERED_TRANSCRIPTS = {
    "hwb run noretry.json": {"cwd": _FLAKY},
    "hwb run retry.json": {"cwd": _FLAKY},
    "hwb catch stable.json": {"cwd": _FLAKY},
    "hwb steady stable.json": {"cwd": _FLAKY, "exit": 1},
    "hwb interrupt stable.json": {"cwd": _FLAKY},
    "hwb sweep stable.json": {"cwd": _FLAKY},
    "hwb steady noretry.json": {"cwd": _FLAKY, "exit": 1},
    # measuring-your-own-code.md. `examples/attaching/` holds exactly the files
    # that guide shows, which is what makes its transcripts checkable at all.
    "hwb run workload.json": {"cwd": _ATTACH},
    "hwb steady mine.json": {"cwd": _ATTACH},
    "hwb efficacy mine.json": {"cwd": _ATTACH},
    "hwb blast mine.json": {"cwd": _ATTACH},
    # Exits 1 on purpose: nothing in that spec detects anything, so `caught 0/3`
    # is the honest verdict and a zero exit would be the lie.
    "hwb catch mine.json": {"cwd": _ATTACH, "exit": 1},
    # The cwd is arbitrary: `subjects` copies out of the installed package and
    # reads nothing from the working directory. It needs *a* sandbox, not a
    # fixture, so it reuses one rather than adding an example that exists only
    # to be a destination.
    "hwb subjects --into ./subjects": {"cwd": _FLAKY},
    'hwb show "$RUN"': {"cwd": _ATTACH, "run_first": "mine.json"},
    'hwb confine "$RUN"': {"cwd": _ATTACH, "run_first": "mine.json"},
}

# Setup lines allowed to share a block with a registered command. The sandbox
# starts clean, so these are already true and are not executed.
INERT_SETUP = ("rm -f .flaky-state",)

# A CEILING that may only fall, in the style of the test-count check above.
# These blocks are unverified prose: they take <placeholder> arguments, or a
# run id that does not exist until something has been run. Registering one is
# the way to bring this number down; pasting a new one without registering it
# is the way to fail this test.
UNREGISTERED_CONSOLE_BLOCKS = 19

_RUN_ID = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{6}(?:-[0-9a-f]{4})?")
_MILLIS = re.compile(r"\b\d+(?:\.\d+)?\s?ms\b")
_VAR_COUNT = re.compile(r"\b\d+ variable name")
_CONSOLE = re.compile(r"^```console\n(.*?)^```", re.M | re.S)


def _normalise(line):
    """Flatten what legitimately varies between two runs of the same command.

    Run ids carry a timestamp and a random suffix, timings are timings, and
    the environment variable count belongs to whoever is running the tests.
    Spec digests are deliberately NOT normalised -- those are determined by
    the spec, so a digest that stops matching is real drift, not noise.
    """
    text = _RUN_ID.sub("<ID>", line)
    text = _MILLIS.sub("<MS>", text)
    text = _VAR_COUNT.sub("<N> variable name", text)
    return " ".join(text.split())


def _console_blocks(text):
    return [m.group(1).splitlines() for m in _CONSOLE.finditer(text)]


def _commands_in(block):
    return [ln.strip()[2:].split("#")[0].strip()
            for ln in block if ln.startswith("$ ")]


STORE_DIRS = ("runs", "sweeps", "blasts", "catches",
              "efficacy", "sensitivity", "replays", "steadies", "effects",
              "interrupts")


class TestRunStoresDoNotShip(unittest.TestCase):
    """Run stores are outputs. They must not reach git or an sdist.

    They were 908 of 981 tracked files once, and every record.json lists the
    environment variable NAMES present at capture -- a fingerprint of the
    machine that produced it. Two separate mechanisms have to agree here,
    which is exactly the kind of pair that drifts: .gitignore reads the repo,
    MANIFEST.in reads the filesystem, and a store ignored by one is not
    ignored by the other.
    """

    def test_no_run_store_is_tracked(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT,
                                 stdout=subprocess.PIPE).stdout.decode()
        bad = [p for p in tracked.splitlines()
               if any("/%s/" % d in "/" + p for d in STORE_DIRS)]
        self.assertEqual([], bad[:10],
                         "%d run-store file(s) are tracked by git" % len(bad))

    def test_manifest_prunes_every_store_on_disk(self):
        with open(os.path.join(ROOT, "MANIFEST.in"), encoding="utf-8") as fh:
            pruned = {ln.split(None, 1)[1].strip().rstrip("/")
                      for ln in fh if ln.startswith("prune ")}
        missing = []
        for root, dirs, _ in os.walk(os.path.join(ROOT, "examples")):
            for d in list(dirs):
                if d not in STORE_DIRS:
                    continue
                dirs.remove(d)
                rel = os.path.relpath(os.path.join(root, d), ROOT)
                if rel not in pruned:
                    missing.append(rel)
        self.assertEqual(
            [], sorted(missing),
            "store director(ies) on disk with no `prune` line in MANIFEST.in "
            "-- an sdist would ship them, and ship them incomplete")


class TestTheDocumentedTranscriptsAreReal(unittest.TestCase):
    """Re-run documented commands and hold the transcripts to their output.

    The coverage tests above catch a documented thing going MISSING. Nothing
    caught a documented thing becoming WRONG, and it happened: two `catch`
    blocks showed four rows of a ten-row table above a summary line that
    counted all ten. Both contradicted themselves on their own face, one of
    them in the file the README sends every newcomer to first, and the suite
    was green throughout.

    The contract asserted here is that a transcript is an ABRIDGEMENT, not a
    paraphrase. Every line shown must appear in what the tool prints today,
    in order, and an interior gap must be marked `...`. Starting late and
    stopping early are allowed -- editing output down to the part under
    discussion is normal and honest. A silent hole in the middle is the one
    thing that is not, because it is indistinguishable from output the tool
    no longer produces.
    """

    def _invoke(self, work, argv):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(ROOT, "src")
        proc = subprocess.run([sys.executable, "-m", "harness_workbench"] + argv,
                              cwd=work, env=env,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        return proc.returncode, proc.stdout.decode("utf-8", "replace")

    def _run(self, cmd, spec):
        sandbox = tempfile.mkdtemp(prefix="hwbdoc-")
        self.addCleanup(shutil.rmtree, sandbox, True)
        work = os.path.join(sandbox, "work")
        shutil.copytree(os.path.join(ROOT, spec["cwd"]), work)
        for residue in (".flaky-state", "runs", "sweeps"):
            path = os.path.join(work, residue)
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)

        argv = shlex.split(cmd)[1:]
        if spec.get("run_first"):
            code, first = self._invoke(work, ["run", spec["run_first"]])
            self.assertEqual(0, code,
                             "setup run for %r failed:\n%s" % (cmd, first))
            run_id = first.splitlines()[0].split()[0]
            argv = [run_id if a == "$RUN" else a for a in argv]

        code, output = self._invoke(work, argv)
        # Not always 0. `hwb run` reports the harness rather than the workload,
        # so a failing step still exits clean -- but a campaign that missed
        # everything exits non-zero, and a doc showing `caught 0/3` is
        # documenting exactly that. The expectation is per transcript.
        self.assertEqual(spec.get("exit", 0), code,
                         "documented command %r exited %d, expected %d:\n%s"
                         % (cmd, code, spec.get("exit", 0), output))
        return output.splitlines()

    def _assert_abridgement(self, where, cmd, shown, actual):
        real = [_normalise(ln) for ln in actual if ln.strip()]
        cursor = 0
        gap_marked = False
        for raw in shown:
            if not raw.strip():
                continue
            if raw.strip() in ("...", "…"):
                gap_marked = True
                continue
            want = _normalise(raw)
            found = None
            for k in range(cursor, len(real)):
                if real[k].startswith(want):
                    found = k
                    break
            self.assertIsNotNone(
                found,
                "%s: the `%s` transcript shows a line the tool does not "
                "print:\n  %s\nreal output was:\n  %s"
                % (where, cmd, want, "\n  ".join(real)))
            if cursor > 0 and found > cursor and not gap_marked:
                self.fail(
                    "%s: the `%s` transcript drops %d line(s) of real output "
                    "before\n  %s\nwithout marking the gap. Add a `...` or "
                    "show them. Dropped:\n  %s"
                    % (where, cmd, found - cursor, want,
                       "\n  ".join(real[cursor:found])))
            cursor = found + 1
            gap_marked = False

    def test_registered_transcripts_still_match_the_tool(self):
        checked = 0
        for path in DOC_FILES:
            for block in _console_blocks(doc(path)):
                commands = _commands_in(block)
                if not commands or commands[-1] not in REGISTERED_TRANSCRIPTS:
                    continue
                for setup in commands[:-1]:
                    self.assertIn(
                        setup, INERT_SETUP,
                        "%s: %r shares a block with a registered command but "
                        "is not a known-inert setup line" % (path, setup))
                self._assert_abridgement(
                    path, commands[-1],
                    [ln for ln in block if not ln.startswith("$ ")],
                    self._run(commands[-1], REGISTERED_TRANSCRIPTS[commands[-1]]))
                checked += 1
        self.assertGreaterEqual(
            checked, len(REGISTERED_TRANSCRIPTS),
            "only %d registered transcript(s) were found in the docs -- a "
            "page was renamed, or a command was reworded out of its block"
            % checked)

    def test_the_unverified_transcript_count_only_falls(self):
        total = unregistered = 0
        for path in DOC_FILES:
            for block in _console_blocks(doc(path)):
                total += 1
                commands = _commands_in(block)
                if not commands or commands[-1] not in REGISTERED_TRANSCRIPTS:
                    unregistered += 1
        self.assertLessEqual(
            unregistered, UNREGISTERED_CONSOLE_BLOCKS,
            "%d of %d console blocks are unverified prose, up from %d. A "
            "pasted transcript nothing re-runs is a claim with no evidence: "
            "register it, or lower the ceiling deliberately."
            % (unregistered, total, UNREGISTERED_CONSOLE_BLOCKS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
