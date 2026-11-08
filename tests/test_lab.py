"""`hwb setup`, `hwb try --json` and the `hwb lab` MVP, offline.

Nothing here contacts Ollama or launches Pi: Ollama's inventory endpoint is a
fake opener and the Reality Check run is a fake `run_check`, so these tests
pin the Lab's own contract -- what it reports, what it refuses, and the one
file it is allowed to write -- not model behaviour.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from harness_workbench import cli, discover, lab, reality

ROOT = Path(__file__).resolve().parents[1]
GOOD = "a" * 64
OTHER = "b" * 64


def fake_ollama(models):
    """An opener that answers /api/tags with the given (name, digest) pairs."""
    payload = {"models": [{"name": n, "digest": d, "size": 1024} for n, d in models]}

    def opener(url, timeout=None):
        assert url.endswith("/api/tags"), url
        return io.BytesIO(json.dumps(payload).encode())
    return opener


def dead_ollama(url, timeout=None):
    raise urllib.error.URLError("connection refused")


def which_all(name):
    return f"/usr/bin/{name}"


def shipped_model():
    selection = json.loads(
        (Path(discover.__file__).parent / "subjects" / "model_selection.json").read_text())
    local = selection["profiles"]["local-ollama"]
    return local["models"]["pi"], local["model_digest"]


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.recipe = Path(self.tmp.name) / "recipe"

    def tearDown(self):
        self.tmp.cleanup()

    def run_discover(self, opener, which=which_all):
        with mock.patch.object(discover, "tool",
                               lambda name, which=which: {
                                   "name": name, "found": bool(which(name)),
                                   "path": which(name), "version": "1.0" if which(name) else None}):
            return discover.discover(self.recipe, which=which, opener=opener)

    def test_no_recipe_is_ready_whatever_digest_shipped(self):
        """Regression: v0.2.0 treated the shipped digest as everyone's model."""
        model, digest = shipped_model()
        doc = self.run_discover(fake_ollama([(model, OTHER)]))
        self.assertEqual(doc["schema"], discover.SCHEMA)
        self.assertEqual(doc["recipe"]["source"], "shipped default")
        self.assertTrue(doc["ready"], doc["blockers"])
        self.assertEqual((doc["blockers"], doc["notes"]), ([], []))
        self.assertEqual(doc["recipe"]["live_digest"], OTHER)

    def test_changed_model_is_a_note_not_a_blocker(self):
        model, _ = shipped_model()
        with mock.patch.object(reality, "_local_model_digest", return_value=GOOD):
            reality.prepare_recipe(self.recipe)
        doc = self.run_discover(fake_ollama([(model, OTHER)]))
        self.assertTrue(doc["ready"], doc["blockers"])
        self.assertEqual([n["code"] for n in doc["notes"]], ["model_changed"])
        self.assertEqual(doc["recipe"]["recorded_digest"], GOOD)

    def test_matching_digest_and_tools_is_ready(self):
        model, digest = shipped_model()
        doc = self.run_discover(fake_ollama([(model, digest)]))
        self.assertTrue(doc["ready"], doc["blockers"])
        self.assertEqual(doc["blockers"], [])

    def test_unreachable_ollama_and_missing_tools_are_blockers(self):
        doc = self.run_discover(dead_ollama, which=lambda name: None)
        codes = {b["code"] for b in doc["blockers"]}
        self.assertEqual(codes, {"ollama_unreachable", "pi_missing", "node_missing"})
        self.assertFalse(doc["ollama"]["reachable"])

    def test_missing_model_is_a_blocker(self):
        doc = self.run_discover(fake_ollama([("other:1b", GOOD)]))
        self.assertIn("model_missing", {b["code"] for b in doc["blockers"]})

    def test_discovery_writes_nothing(self):
        model, digest = shipped_model()
        self.run_discover(fake_ollama([(model, digest)]))
        self.assertFalse(self.recipe.exists())


class LabServer:
    """A Lab on an ephemeral localhost port with a controllable fake run."""

    def __init__(self, runs_root, recipe, opener):
        self.release = threading.Event()
        self.calls = []

        def run_check(check, *, runs_root, recipe_dir):
            self.calls.append((check, runs_root, recipe_dir))
            self.release.wait(5)
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1"),
                    "facts": [], "setup": {}}

        self.lab = lab.Lab(runs_root, recipe, run_check=run_check,
                           discover=lambda r, opener=None: {"ready": True, "recipe": str(r)},
                           opener=opener)
        self.server = lab.make_server(self.lab, port=0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, method="GET", body=None, ctype="application/json"):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None and ctype:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, resp.headers.get("Content-Type"), raw
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type"), error.read()

    def json(self, *args, **kwargs):
        status, _, raw = self.request(*args, **kwargs)
        return status, json.loads(raw)


class LabApiTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(reality, "_local_model_digest", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.runs = str(base / "runs")
        self.recipe = base / ".hwb" / "recipes" / "coding-python-bug-fix"
        model, _ = shipped_model()
        self.model = model
        self.srv = LabServer(self.runs, self.recipe,
                             fake_ollama([(model, GOOD), ("qwen3:14b", OTHER), ("short:1b", "abc")]))

    def tearDown(self):
        self.srv.close()
        self.tmp.cleanup()

    def test_serves_the_page_and_setup(self):
        status, ctype, raw = self.srv.request("/")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn(b"Harness Workbench Lab", raw)
        self.assertEqual(self.srv.json("/api/setup")[1]["ready"], True)

    def test_server_binds_localhost_only(self):
        self.assertEqual(self.srv.server.server_address[0], "127.0.0.1")

    def test_writes_require_json(self):
        status, payload = self.srv.json("/api/runs", "POST", b"x=1", ctype="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(self.srv.calls, [])

    def test_one_run_at_a_time_then_done(self):
        status, job = self.srv.json("/api/runs", "POST", {})
        self.assertEqual((status, job["state"]), (202, "running"))
        self.assertEqual(self.srv.json("/api/runs", "POST", {})[0], 409)
        self.assertEqual(self.srv.json("/api/runs/current")[1]["state"], "running")
        self.srv.release.set()
        for _ in range(100):
            current = self.srv.json("/api/runs/current")[1]
            if current["state"] != "running":
                break
            threading.Event().wait(0.02)
        self.assertEqual(current["state"], "done")
        self.assertEqual(current["run_id"], "R1")
        self.assertEqual(self.srv.calls[0][0], reality.CHECK)
        self.assertEqual(self.srv.calls[0][2], str(self.recipe))

    def test_failed_run_is_reported_not_swallowed(self):
        def boom(check, *, runs_root, recipe_dir):
            raise reality.RealityError("recipe directory exists without recipe.json")
        self.srv.lab._run_check = boom
        self.srv.json("/api/runs", "POST", {})
        for _ in range(100):
            current = self.srv.json("/api/runs/current")[1]
            if current["state"] != "running":
                break
            threading.Event().wait(0.02)
        self.assertEqual(current["state"], "failed")
        self.assertIn("recipe.json", current["error"])

    def test_set_model_records_live_digest_and_keeps_recipe_valid(self):
        status, recipe = self.srv.json("/api/recipe/model", "POST", {"model": "qwen3:14b"})
        self.assertEqual(status, 200, recipe)
        self.assertEqual((recipe["model"], recipe["recorded_digest"]), ("qwen3:14b", OTHER))
        selection = json.loads((self.recipe / "model_selection.json").read_text())
        local = selection["profiles"]["local-ollama"]
        self.assertEqual(local["models"]["pi"], "qwen3:14b")
        self.assertEqual(local["model_digest"], OTHER)
        self.assertEqual(local["baseline_digest"], OTHER)
        reality._validate_recipe(self.recipe.resolve())  # the run's own gate

    def test_set_model_refuses_unknown_or_partial_digest_models(self):
        self.assertEqual(self.srv.json("/api/recipe/model", "POST", {"model": "nope:7b"})[0], 422)
        self.assertEqual(self.srv.json("/api/recipe/model", "POST", {"model": "short:1b"})[0], 422)
        self.assertEqual(self.srv.json("/api/recipe/model", "POST", {"model": 7})[0], 400)
        self.assertFalse(self.recipe.exists())

    def test_set_model_refused_while_running(self):
        self.srv.json("/api/runs", "POST", {})
        self.assertEqual(self.srv.json("/api/recipe/model", "POST", {"model": "qwen3:14b"})[0], 409)

    def test_run_detail_rejects_bad_and_missing_ids(self):
        self.assertEqual(self.srv.json("/api/runs/..%2Fetc")[0], 400)
        self.assertEqual(self.srv.json("/api/runs/NOPE")[0], 404)

    def test_history_lists_ordinary_runs(self):
        self.assertEqual(self.srv.json("/api/runs")[1], {"runs": []})
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--root", self.runs, "run", str(ROOT / "examples" / "smoke.json")])
        runs = self.srv.json("/api/runs")[1]["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["lifecycle"], "complete")
        self.assertFalse(runs[0]["reality_check"])


class CliTests(unittest.TestCase):
    def test_try_json_prints_the_result_document(self):
        result = {"schema": reality.RESULT_SCHEMA, "verdict": "pass", "facts": []}
        out = io.StringIO()
        with mock.patch.object(reality, "run_check", return_value=result), \
                contextlib.redirect_stdout(out):
            code = cli.main(["try", "coding/python-bug-fix", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), result)

    def test_setup_json_exit_code_follows_readiness(self):
        for ready, expected in ((True, 0), (False, 1)):
            out = io.StringIO()
            with mock.patch.object(discover, "discover", return_value={"ready": ready}), \
                    contextlib.redirect_stdout(out):
                code = cli.main(["setup", "--json"])
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(out.getvalue())["ready"], ready)


if __name__ == "__main__":
    unittest.main()
