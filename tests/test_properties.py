"""Deterministic generative checks for the workbench's pure contracts.

This is deliberately stdlib-only.  The fixed seed makes every failure
reproducible on the Python versions the package supports; when a generated
case exposes a defect, its smallest useful shape belongs here as an ordinary
regression test too.
"""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from hwb import conform, features, interrupt, runner, spec as specmod  # noqa: E402
from hwb.canon import canon_bytes, digest_obj  # noqa: E402


SEED = 0x485742
JSON_CASES = 128
SPEC_CASES = 64
MANIFEST_CASES = 48

TEXT = (
    "", "plain", "café", "cafe\u0301", "雪", "مرحبا", "עברית",
    "emoji-🧪", "line\nfeed", "tab\tvalue", "\u2028separator",
)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def generated_json(rng, depth=0):
    """A bounded generator for values admitted by standards-compliant JSON."""
    primitives = [None, True, False, rng.randint(-10**9, 10**9),
                  rng.uniform(-10**6, 10**6), rng.choice(TEXT)]
    if depth >= 3:
        return rng.choice(primitives)
    choice = rng.randrange(8)
    if choice < 5:
        return rng.choice(primitives)
    if choice == 5:
        return [generated_json(rng, depth + 1) for _ in range(rng.randrange(5))]
    count = rng.randrange(5)
    keys = ["k%d-%s" % (i, rng.choice(TEXT)) for i in range(count)]
    return {key: generated_json(rng, depth + 1) for key in keys}


def reordered(value, rng):
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {key: reordered(item, rng) for key, item in items}
    if isinstance(value, list):
        return [reordered(item, rng) for item in value]
    return value


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hwb-property-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestCanonicalProperties(unittest.TestCase):
    def test_generated_json_round_trips_and_ignores_mapping_order(self):
        rng = random.Random(SEED)
        for case in range(JSON_CASES):
            value = generated_json(rng)
            shuffled = reordered(value, rng)
            with self.subTest(case=case, seed=SEED):
                encoded = canon_bytes(value)
                self.assertEqual(encoded, canon_bytes(json.loads(encoded)))
                self.assertEqual(encoded, canon_bytes(shuffled))
                self.assertEqual(digest_obj(value), digest_obj(shuffled))

    def test_non_finite_numbers_are_not_canonical_json(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canon_bytes({"value": value})


class TestGeneratedSpecs(TempCase):
    def _load(self, raw, name="spec.json"):
        path = os.path.join(self.tmp, name)
        write_json(path, raw)
        return specmod.load(path)

    def test_generated_valid_specs_load_and_duplicate_ids_fail_closed(self):
        rng = random.Random(SEED)
        names = ("step", "café", "雪", "בדיקה", "🧪")
        for case in range(SPEC_CASES):
            count = rng.randint(1, 4)
            steps = []
            for i in range(count):
                steps.append({
                    "id": "%s-%d-%d" % (rng.choice(names), case, i),
                    "argv": [sys.executable, "-c", "pass", rng.choice(TEXT)],
                    "inputs": [rng.choice(TEXT)] if rng.randrange(2) else [],
                    "future_step_field": generated_json(rng),
                })
            raw = {
                "schema": "hwbspec/v0.1",
                "run_class": rng.choice(specmod.RUN_CLASSES),
                "features": [],
                "env": ["HWB_PROPERTY_%d" % case],
                "steps": steps,
                "future_top_level_field": generated_json(rng),
            }
            with self.subTest(case=case, seed=SEED):
                loaded = self._load(raw, "valid-%d.json" % case)
                self.assertEqual([s.id for s in loaded.steps],
                                 [s["id"] for s in steps])
                self.assertEqual(loaded.digest, digest_obj(raw))
                duplicate = json.loads(json.dumps(raw, ensure_ascii=False))
                duplicate["steps"].append(dict(duplicate["steps"][0]))
                with self.assertRaises(specmod.SpecError):
                    self._load(duplicate, "duplicate-%d.json" % case)

    def test_malformed_known_fields_raise_spec_error_not_python_errors(self):
        base = {"schema": "hwbspec/v0.1",
                "steps": [{"id": "one", "argv": ["command"]}]}
        cases = (
            {"steps": "not-a-list"},
            {"features": {}},
            {"features": ["feature"]},
            {"features": [{"name": "x", "config": []}]},
            {"env": "PATH"},
            {"features_root": 42},
            {"steps": [{"id": [], "argv": ["command"]}]},
            {"steps": [{"id": "one", "argv": [42]}]},
            {"steps": [{"id": "one", "argv": [""]}]},
            {"steps": [{"id": "one", "argv": ["command"], "inputs": "x"}]},
        )
        for i, changes in enumerate(cases):
            raw = dict(base)
            raw.update(changes)
            with self.subTest(case=i):
                with self.assertRaises(specmod.SpecError):
                    self._load(raw, "bad-%d.json" % i)

    def test_storage_component_ids_reject_traversal_but_allow_unicode(self):
        for bad in ("", ".", "..", "../escape", "a/b", "a\\b", "nul\x00id"):
            for field in ("step", "feature"):
                raw = {"schema": "hwbspec/v0.1", "features": [],
                       "steps": [{"id": "safe", "argv": ["command"]}]}
                if field == "step":
                    raw["steps"][0]["id"] = bad
                else:
                    raw["features"] = [{"name": bad}]
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(specmod.SpecError):
                        self._load(raw, "%s-%s.json" % (field, len(bad)))

        loaded = self._load(
            {"schema": "hwbspec/v0.1", "features": [],
             "steps": [{"id": "測試-🧪", "argv": [sys.executable, "-c", "pass"]}]},
            "unicode.json")
        self.assertEqual(loaded.steps[0].id, "測試-🧪")

    def test_python_json_extensions_are_rejected_at_the_file_boundary(self):
        path = os.path.join(self.tmp, "nan.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"schema":"hwbspec/v0.1","steps":'
                     '[{"id":"one","argv":["x"]}],"future":NaN}')
        with self.assertRaises(specmod.SpecError):
            specmod.load(path)


class TestGeneratedManifests(TempCase):
    def _feature(self, directory, **changes):
        root = os.path.join(self.tmp, directory)
        os.makedirs(root, exist_ok=True)
        raw = {"name": directory, "version": "0.1.0", "power": "annotate",
               "seams": ["before_run"],
               "seam_contract": ">=0.2.0,<0.3.0"}
        raw.update(changes)
        write_json(os.path.join(root, "FEATURE.json"), raw)
        with open(os.path.join(root, "feature.py"), "w", encoding="utf-8") as fh:
            fh.write("def before_run(*args):\n    return {}\n")
        return root

    def _spec(self, refs, name):
        path = os.path.join(self.tmp, name)
        write_json(path, {"schema": "hwbspec/v0.1", "features_root": ".",
                          "features": [{"name": ref} for ref in refs],
                          "steps": [{"id": "one", "argv": ["command"]}]})
        return specmod.load(path)

    def test_generated_unknown_fields_are_additive(self):
        rng = random.Random(SEED)
        for case in range(MANIFEST_CASES):
            name = "feature-%d" % case
            root = self._feature(name, **{
                "future_field_%d" % case: generated_json(rng),
                "another_future_field": generated_json(rng),
            })
            with self.subTest(case=case, seed=SEED):
                manifest = features.read_manifest(root)
                self.assertEqual(manifest.name, name)

    def test_malformed_known_fields_raise_feature_error(self):
        cases = (
            {"name": 1}, {"name": "../escape"}, {"power": []},
            {"seams": "before_run"}, {"seams": []},
            {"provides": "cap"}, {"requires": [1]},
            {"seam_contract": 2},
        )
        for case, changes in enumerate(cases):
            root = self._feature("bad-%d" % case, **changes)
            with self.subTest(case=case):
                with self.assertRaises(features.FeatureError):
                    features.read_manifest(root)

    def test_dependency_graphs_fail_closed_before_import(self):
        capability = "content-雪"
        self._feature("consumer", requires=[capability])
        with self.assertRaises(features.FeatureError) as missing:
            features.resolve(self._spec(["consumer"], "missing.json"))
        self.assertIn(capability, str(missing.exception))

        self._feature("late", provides=[capability], seams=["after_run"])
        with self.assertRaises(features.FeatureError) as backwards:
            features.resolve(self._spec(["late", "consumer"], "backwards.json"))
        self.assertIn("backwards", str(backwards.exception))

        self._feature("early", provides=[capability], seams=["on_spec_loaded"])
        loaded = features.resolve(self._spec(["early", "consumer"], "valid.json"))
        self.assertEqual([item.name for item in loaded], ["early", "consumer"])

    def test_directory_and_manifest_names_must_agree(self):
        root = self._feature("requested")
        path = os.path.join(root, "FEATURE.json")
        raw = read_json(path)
        raw["name"] = "different"
        write_json(path, raw)
        with self.assertRaises(features.FeatureError) as cm:
            features.resolve(self._spec(["requested"], "mismatch.json"))
        self.assertIn("different", str(cm.exception))


class TestStoreAndPartialCloseProperties(TempCase):
    def _complete_unicode_run(self):
        input_name = "café-雪.txt"
        with open(os.path.join(self.tmp, input_name), "w", encoding="utf-8") as fh:
            fh.write("input 🧪\n")
        path = os.path.join(self.tmp, "run.json")
        write_json(path, {
            "schema": "hwbspec/v0.1", "features": [],
            "steps": [{"id": "測試-🧪", "argv": [sys.executable, "-c",
                       "print('résultat-雪')"], "inputs": [input_name]}],
        })
        runs = os.path.join(self.tmp, "runs")
        subject = specmod.load(path)
        record = runner.execute(subject, features.resolve(subject), runs)
        run_dir = os.path.join(runs, record["run_id"])
        with open(os.path.join(run_dir, "attempts.jsonl"),
                  "r", encoding="utf-8") as fh:
            attempts = [json.loads(line) for line in fh if line.strip()]
        return run_dir, record, attempts

    def test_unicode_step_and_input_paths_survive_the_real_store(self):
        run_dir, record, attempts = self._complete_unicode_run()
        self.assertEqual(record["steps"][0]["id"], "測試-🧪")
        conform.validate_record(record, attempts, run_dir)
        self.assertEqual(interrupt.inspect_state(run_dir)["state"],
                         interrupt.COMPLETE)

    def test_generated_store_mutations_need_and_fail_the_store_witness(self):
        original, record, attempts = self._complete_unicode_run()
        mutations = ("delete_stdout", "rewrite_stdout", "hide_execution",
                     "invent_attempt_directory", "rewrite_spec")
        for mutation in mutations:
            case_dir = os.path.join(self.tmp, "mutated-" + mutation)
            shutil.copytree(original, case_dir)
            rec = json.loads(json.dumps(record, ensure_ascii=False))
            ats = json.loads(json.dumps(attempts, ensure_ascii=False))
            adir = os.path.join(case_dir, "steps", "測試-🧪", "attempts", "0")
            if mutation == "delete_stdout":
                os.remove(os.path.join(adir, "stdout.bin"))
            elif mutation == "rewrite_stdout":
                with open(os.path.join(adir, "stdout.bin"), "ab") as fh:
                    fh.write(b"changed")
            elif mutation == "hide_execution":
                ats[0]["executed"] = False
            elif mutation == "invent_attempt_directory":
                os.makedirs(os.path.join(case_dir, "steps", "測試-🧪",
                                         "attempts", "1"))
            elif mutation == "rewrite_spec":
                spec_path = os.path.join(case_dir, "spec.json")
                raw = read_json(spec_path)
                raw["run_class"] = "confirmation"
                write_json(spec_path, raw)

            with self.subTest(mutation=mutation):
                # In-memory shape alone cannot see any of these filesystem
                # facts.  Choosing weak mode remains explicit and honest.
                conform.validate_record(rec, ats, None)
                with self.assertRaises(conform.NonConforming):
                    conform.validate_record(rec, ats, case_dir)

    def test_truncated_closure_files_are_never_classified_complete(self):
        original, _, _ = self._complete_unicode_run()
        for filename in ("record.json", "attempts.jsonl", "integrity.json"):
            with open(os.path.join(original, filename), "rb") as fh:
                body = fh.read()
            cutpoints = sorted(set((0, 1, len(body) // 4, len(body) // 2,
                                    max(0, len(body) - 1))))
            for cut in cutpoints:
                case_dir = os.path.join(self.tmp, "truncated-%s-%d"
                                        % (filename, cut))
                shutil.copytree(original, case_dir)
                with open(os.path.join(case_dir, filename), "wb") as fh:
                    fh.write(body[:cut])
                with self.subTest(file=filename, cut=cut):
                    self.assertNotEqual(interrupt.inspect_state(case_dir)["state"],
                                        interrupt.COMPLETE)

    def test_missing_close_files_are_never_classified_complete(self):
        original, _, _ = self._complete_unicode_run()
        for filename in ("record.json", "attempts.jsonl", "integrity.json"):
            case_dir = os.path.join(self.tmp, "missing-" + filename)
            shutil.copytree(original, case_dir)
            os.remove(os.path.join(case_dir, filename))
            with self.subTest(file=filename):
                self.assertNotEqual(interrupt.inspect_state(case_dir)["state"],
                                    interrupt.COMPLETE)


if __name__ == "__main__":
    unittest.main()
