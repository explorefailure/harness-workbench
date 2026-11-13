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
import time
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


def fake_llamacpp(alias="qwen3.8:27b", path="/blobs/sha256-" + "c" * 64,
                  build="b12110-cd959bae9"):
    """An opener that answers llama-server's /props."""
    payload = {"model_alias": alias, "model_path": path, "build_info": build}

    def opener(url, timeout=None):
        assert url.endswith("/props"), url
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

    def run_discover(self, opener, which=which_all, profile="local-ollama"):
        with mock.patch.object(discover, "tool",
                               lambda name, which=which: {
                                   "name": name, "found": bool(which(name)),
                                   "path": which(name), "version": "1.0" if which(name) else None}):
            return discover.discover(self.recipe, profile=profile, which=which, opener=opener)

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


    # -- llama.cpp ----------------------------------------------------------
    def test_llamacpp_profile_is_ready_with_alias_and_content_addressed_file(self):
        doc = self.run_discover(fake_llamacpp(), profile="local-llamacpp")
        self.assertTrue(doc["ready"], doc["blockers"])
        self.assertEqual("llama.cpp", doc["runtime"]["kind"])
        self.assertEqual("b12110-cd959bae9", doc["runtime"]["build"])
        self.assertIsNone(doc["ollama"])
        self.assertEqual("local-llamacpp", doc["recipe"]["profile"])
        self.assertEqual("c" * 64, doc["recipe"]["live_digest"])

    def test_owner_overlay_profile_drives_setup_with_its_actual_alias(self):
        overlay = Path(self.tmp.name) / "profiles.json"
        overlay.write_text(json.dumps({"profiles": {"metal-small": {
            "kind": "local",
            "runtime": "llama.cpp",
            "runtime_build": "b12110-cd959bae9",
            "base_url": "http://127.0.0.1:8081/v1",
            "api_key_env": "HWB_LOCAL_KEY",
            "identity_strength": "local_content_digest",
            "verify_digest": True,
            "model_digest": "c" * 64,
            "models": {"pi": "qwen2.5-coder:1.5b"},
        }}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {
            reality.LOCAL_PROFILES_ENV: str(overlay),
        }):
            doc = self.run_discover(
                fake_llamacpp(alias="qwen2.5-coder:1.5b"), profile="metal-small"
            )
        self.assertTrue(doc["ready"], doc["blockers"])
        self.assertEqual("metal-small", doc["recipe"]["profile"])
        self.assertEqual("qwen2.5-coder:1.5b", doc["recipe"]["model"])
        self.assertEqual("c" * 64, doc["recipe"]["live_digest"])

    def test_llamacpp_blocks_unreachable_wrong_alias_and_unaddressed_file(self):
        cases = {
            "llamacpp_unreachable": dead_ollama,
            "model_missing": fake_llamacpp(alias="other"),
            "model_not_content_addressed": fake_llamacpp(path="/models/qwen.gguf"),
        }
        for code, opener in cases.items():
            with self.subTest(code=code):
                doc = self.run_discover(opener, profile="local-llamacpp")
                self.assertFalse(doc["ready"])
                self.assertIn(code, [b["code"] for b in doc["blockers"]])
        doc = self.run_discover(fake_llamacpp(alias="other"), profile="local-llamacpp")
        self.assertIn("--alias qwen3.8:27b", doc["blockers"][0]["message"])

    def test_existing_recipe_keeps_its_profile_over_the_requested_one(self):
        with mock.patch.object(reality, "_local_model_digest", return_value=GOOD):
            reality.prepare_recipe(self.recipe)  # an Ollama Recipe
        name, _ = shipped_model()
        doc = self.run_discover(fake_ollama([(name, GOOD)]), profile="local-llamacpp")
        self.assertEqual("local-ollama", doc["recipe"]["profile"])
        self.assertEqual("ollama", doc["runtime"]["kind"])

    def test_llamacpp_build_change_is_a_note_not_a_blocker(self):
        with mock.patch.object(reality, "_llamacpp_identity",
                               return_value=("c" * 64, "b100-old")):
            reality.prepare_recipe(self.recipe, "local-llamacpp")
        doc = self.run_discover(fake_llamacpp(), profile="local-llamacpp")
        self.assertTrue(doc["ready"], doc["blockers"])
        self.assertIn("build_changed", [n["code"] for n in doc["notes"]])

class LabServer:
    """A Lab on an ephemeral localhost port with a controllable fake run."""

    def __init__(self, runs_root, recipe, opener):
        self.release = threading.Event()
        self.calls = []
        self.profiles = []

        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            self.calls.append((check, runs_root, recipe_dir))
            self.profiles.append(profile)
            self.release.wait(5)
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1"),
                    "facts": [], "setup": {}}

        self.lab = lab.Lab(runs_root, recipe, run_check=run_check,
                           discover=lambda r, opener=None, profile=None: {
                               "ready": True, "recipe": str(r), "profile": profile},
                           opener=opener)
        self.server = lab.make_server(self.lab, port=0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, method="GET", body=None, ctype="application/json",
                headers=None, auth=True):
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None and ctype:
            req.add_header("Content-Type", ctype)
        if auth:
            req.add_header("Authorization", "Bearer " + self.server.lab_token)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, resp.headers.get("Content-Type"), raw
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type"), error.read()

    def json(self, *args, **kwargs):
        status, _, raw = self.request(*args, **kwargs)
        return status, json.loads(raw)


def raw_request(port, method, path, headers, body=b""):
    """Send exact header bytes (urllib normalises Host and Origin)."""
    import socket as socketmod
    lines = [f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in headers] + \
        [f"Content-Length: {len(body)}", "Connection: close", "", ""]
    with socketmod.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall("\r\n".join(lines).encode("latin-1") + body)
        chunks = []
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    head, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    found = {}
    for line in header_lines:
        key, _, value = line.partition(":")
        found.setdefault(key.strip().lower(), []).append(value.strip())
    return int(status_line.split()[1]), found, payload


def concrete(path):
    return path.replace("<id>", "R1")


class LabSecurityTests(unittest.TestCase):
    """The local-server security model: one guard in front of every route."""

    def setUp(self):
        patcher = mock.patch.object(reality, "_local_model_digest", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.recipe = base / ".hwb" / "recipes" / "coding-python-bug-fix"
        model, _ = shipped_model()
        self.srv = LabServer(str(base / "runs"), self.recipe, fake_ollama([(model, GOOD)]))
        self.port = self.srv.server.server_address[1]
        self.token = self.srv.server.lab_token
        self.host = f"127.0.0.1:{self.port}"

    def tearDown(self):
        self.srv.close()
        self.tmp.cleanup()

    def send(self, method, path, *, host=None, origin=None, bearer=None, cookie=None,
             json_body=False, extra=()):
        headers = [("Host", host if host is not None else self.host)]
        if origin is not None:
            headers.append(("Origin", origin))
        if bearer is not None:
            headers.append(("Authorization", "Bearer " + bearer))
        if cookie is not None:
            headers.append(("Cookie", f"{lab.cookie_name(self.port)}={cookie}"))
        body = b""
        if method == "POST" or json_body:
            headers.append(("Content-Type", "application/json"))
            body = b"{}"
        headers.extend(extra)
        return raw_request(self.port, method, path, headers, body)

    def assertNothingHappened(self):
        self.assertEqual([], self.srv.calls)

    def test_route_table_is_the_whole_surface(self):
        self.assertEqual(9, len(lab.ROUTES))
        for (method, path) in lab.ROUTES:
            self.assertIn(method, ("GET", "POST"))

    def test_every_route_refuses_a_missing_or_wrong_token(self):
        for (method, path) in lab.ROUTES:
            for bearer in (None, "wrong", self.token + "x", ""):
                with self.subTest(route=(method, path), bearer=bearer):
                    status, _, _ = self.send(method, concrete(path), bearer=bearer)
                    self.assertEqual(401, status)
        self.assertNothingHappened()

    def test_every_route_refuses_a_foreign_host_even_with_the_token(self):
        # DNS rebinding: a page on evil.example resolved to 127.0.0.1.
        for (method, path) in lab.ROUTES:
            for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1",
                         f"127.0.0.1:{self.port + 1 if self.port < 65535 else 1}",
                         f"0.0.0.0:{self.port}", "", f"127.0.0.1:{self.port}.evil.example"):
                with self.subTest(route=(method, path), host=host):
                    status, _, _ = self.send(method, concrete(path), host=host, bearer=self.token)
                    self.assertEqual(403, status)
        self.assertNothingHappened()

    def test_every_route_refuses_a_foreign_origin_even_with_the_token(self):
        for (method, path) in lab.ROUTES:
            for origin in ("http://evil.example", "null", f"https://127.0.0.1:{self.port}",
                           f"http://127.0.0.1:{self.port}.evil.example"):
                with self.subTest(route=(method, path), origin=origin):
                    status, _, _ = self.send(method, concrete(path), origin=origin,
                                             bearer=self.token)
                    self.assertEqual(403, status)
        self.assertNothingHappened()

    def test_localhost_name_and_same_origin_are_accepted(self):
        for host in (self.host, f"localhost:{self.port}", f"LOCALHOST:{self.port}"):
            status, _, _ = self.send("GET", "/api/setup", host=host, bearer=self.token,
                                     origin="http://" + host.lower())
            self.assertEqual(200, status, host)

    def test_token_url_becomes_a_strict_cookie_and_leaves_the_address_bar(self):
        status, headers, _ = self.send("GET", "/?token=" + self.token)
        self.assertEqual(303, status)
        self.assertEqual(["/"], headers["location"])
        cookie = headers["set-cookie"][0]
        self.assertTrue(cookie.startswith(f"{lab.cookie_name(self.port)}={self.token};"))
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        status, headers, page = self.send("GET", "/", cookie=self.token)
        self.assertEqual(200, status)
        self.assertIn(b"Harness Workbench Lab", page)
        self.assertEqual(["no-referrer"], headers["referrer-policy"])

    def test_wrong_token_url_sets_nothing(self):
        status, headers, _ = self.send("GET", "/?token=nope")
        self.assertEqual(401, status)
        self.assertNotIn("set-cookie", headers)

    def test_token_url_on_a_foreign_host_sets_nothing(self):
        status, headers, _ = self.send("GET", "/?token=" + self.token, host="evil.example")
        self.assertEqual(403, status)
        self.assertNotIn("set-cookie", headers)

    def test_cookie_writes_need_the_same_origin(self):
        status, _, _ = self.send("POST", "/api/runs", cookie=self.token)
        self.assertEqual(403, status)  # no Origin: not the Lab page
        self.assertNothingHappened()
        status, _, _ = self.send("POST", "/api/runs", cookie=self.token,
                                 origin=f"http://{self.host}")
        self.assertEqual(202, status)
        self.assertEqual(1, len(self.srv.calls) or self._wait_for_call())

    def _wait_for_call(self):
        for _ in range(100):
            if self.srv.calls:
                return len(self.srv.calls)
            threading.Event().wait(0.02)
        return 0

    def test_bearer_writes_work_without_origin_for_scripts(self):
        status, _, _ = self.send("POST", "/api/runs", bearer=self.token)
        self.assertEqual(202, status)

    def test_malformed_headers_are_refused_and_the_server_survives(self):
        cases = [
            dict(extra=[("Host", self.host)]),  # duplicate Host
            dict(host="127.0.0.1:" + "9" * 300),
            dict(host="ünï.example"),
            dict(origin="http://evil.example", extra=[("Origin", f"http://{self.host}")]),
            dict(bearer=self.token, extra=[("Cookie", "=;=;;\x7f")]),
            dict(bearer=self.token, json_body=True, extra=[("Content-Length", "abc")]),
        ]
        for case in cases:
            with self.subTest(case=case):
                status, _, _ = self.send("GET", "/api/setup", **case)
                self.assertIn(status, (200, 400, 403))
        self.assertEqual(200, self.send("GET", "/api/setup", bearer=self.token)[0])
        self.assertNothingHappened()

    def test_other_methods_never_reach_a_route_or_grant_cors(self):
        for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                status, headers, _ = self.send(method, "/api/runs", bearer=self.token,
                                               origin="http://evil.example")
                self.assertIn(status, (403, 405, 501))
                self.assertFalse(any(k.startswith("access-control-") for k in headers))
        self.assertNothingHappened()

    def test_wrong_method_on_a_known_route_is_405_after_the_guard(self):
        self.assertEqual(405, self.send("POST", "/api/setup", bearer=self.token)[0])
        self.assertEqual(401, self.send("POST", "/api/setup")[0])


class LabPortTests(unittest.TestCase):
    def test_lab_cli_distinguishes_an_omitted_profile(self):
        self.assertIsNone(cli.build_parser().parse_args(["lab"]).profile)

    def test_default_port_is_os_assigned_and_differs_per_launch(self):
        self.assertEqual(0, lab.DEFAULT_PORT)
        recipe = Path(tempfile.mkdtemp()) / "r"
        first = lab.make_server(lab.Lab("runs", recipe))
        second = lab.make_server(lab.Lab("runs", recipe))
        try:
            self.assertNotEqual(0, first.server_address[1])
            self.assertNotEqual(first.server_address[1], second.server_address[1])
            self.assertNotEqual(first.lab_token, second.lab_token)
            self.assertGreaterEqual(len(first.lab_token), 40)
        finally:
            first.server_close()
            second.server_close()

    def test_never_shares_a_port_with_another_listener(self):
        import socket as socketmod
        recipe = Path(tempfile.mkdtemp()) / "r"
        for other_addr in ("127.0.0.1", "0.0.0.0"):
            for other_reuse in (False, True):
                with self.subTest(other=other_addr, reuse=other_reuse):
                    other = socketmod.socket()
                    try:
                        if other_reuse:
                            other.setsockopt(socketmod.SOL_SOCKET, socketmod.SO_REUSEADDR, 1)
                        other.bind((other_addr, 0))
                        other.listen()
                        with self.assertRaises(OSError):
                            lab.make_server(lab.Lab("runs", recipe),
                                            port=other.getsockname()[1]).server_close()
                    finally:
                        other.close()

    def test_invalid_ports_are_refused(self):
        recipe = Path(tempfile.mkdtemp()) / "r"
        for port in (-1, 65536, "8765", True):
            with self.subTest(port=port), self.assertRaises(lab.LabError):
                lab.make_server(lab.Lab("runs", recipe), port=port)

    def test_startup_names_profile_from_existing_recipe(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        recipe = Path(root.name) / "recipe"
        with mock.patch.object(reality, "_local_model_digest", return_value=GOOD):
            reality.prepare_recipe(recipe)

        class Server:
            server_address = (lab.HOST, 1234)
            lab_token = "token"

            def serve_forever(self):
                pass

            def server_close(self):
                pass

        output = io.StringIO()
        with mock.patch.object(lab, "make_server", return_value=Server()), \
                contextlib.redirect_stdout(output):
            self.assertEqual(0, lab.serve("runs", str(recipe), 0, False))
        self.assertIn("profile local-ollama (from existing Recipe)", output.getvalue())


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
        def boom(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
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

    def test_runs_pass_the_lab_profile_to_the_check(self):
        self.srv.release.set()
        self.srv.json("/api/runs", "POST", {})
        for _ in range(100):
            if self.srv.profiles:
                break
            threading.Event().wait(0.02)
        self.assertEqual(["local-ollama"], self.srv.profiles)

    def test_llamacpp_lab_sets_model_with_file_digest_and_build(self):
        with mock.patch.object(reality, "_llamacpp_identity",
                               return_value=("c" * 64, "b12110-cd959bae9")):
            recipe = Path(self.tmp.name) / "llama-recipe"
            the_lab = lab.Lab(self.runs, recipe, opener=fake_llamacpp(),
                              profile="local-llamacpp")
            state = the_lab.set_model("qwen3.8:27b")
        self.assertEqual("c" * 64, state["recorded_digest"])
        selection = json.loads((recipe / "model_selection.json").read_text())
        self.assertEqual("local-llamacpp", selection["active"])
        local = selection["profiles"]["local-llamacpp"]
        self.assertEqual("b12110-cd959bae9", local["baseline_runtime_build"])
        with self.assertRaises(lab.LabError) as refused:
            the_lab.set_model("other:7b")
        self.assertEqual(422, refused.exception.status)

    def test_unknown_lab_profile_is_refused(self):
        with self.assertRaises(lab.LabError):
            lab.Lab(self.runs, self.recipe, profile="opencode-go")

    def test_lab_accepts_owner_overlay_profile(self):
        path = Path(self.tmp.name) / "profiles.json"
        path.write_text(json.dumps({"profiles": {"metal-small": {
            "kind": "local", "runtime": "llama.cpp",
        }}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {
            reality.LOCAL_PROFILES_ENV: str(path),
        }):
            created = lab.Lab(self.runs, self.recipe, profile="metal-small")
        self.assertEqual("metal-small", created.profile)

    def test_lab_reopens_embedded_owner_profile_when_profile_is_omitted(self):
        recipe = Path(self.tmp.name) / "embedded-recipe"
        path = Path(self.tmp.name) / "profiles.json"
        path.write_text(json.dumps({"profiles": {"metal-small": {
            "kind": "local", "runtime": "llama.cpp",
            "base_url": "http://127.0.0.1:8081/v1",
            "verify_digest": True,
            "models": {"pi": "small-coder:1.5b"},
        }}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {
            reality.LOCAL_PROFILES_ENV: str(path),
        }), mock.patch.object(reality, "_llamacpp_identity",
                              return_value=("c" * 64, "b12110-cd959bae9")):
            reality.prepare_recipe(recipe, "metal-small")
        reopened = lab.Lab(self.runs, recipe)
        self.assertEqual("metal-small", reopened.profile)

    def test_lab_accepts_matching_explicit_profile_for_existing_recipe(self):
        recipe = Path(self.tmp.name) / "matching-recipe"
        with mock.patch.object(reality, "_local_model_digest", return_value=GOOD):
            reality.prepare_recipe(recipe)
        reopened = lab.Lab(self.runs, recipe, profile="local-ollama")
        self.assertEqual("local-ollama", reopened.profile)

    def test_lab_refuses_different_explicit_profile_for_existing_recipe(self):
        recipe = Path(self.tmp.name) / "different-recipe"
        with mock.patch.object(reality, "_local_model_digest", return_value=GOOD):
            reality.prepare_recipe(recipe)
        with self.assertRaises(lab.LabError) as refused:
            lab.Lab(self.runs, recipe, profile="local-llamacpp")
        self.assertIn("local-ollama", str(refused.exception))
        self.assertIn("local-llamacpp", str(refused.exception))
        self.assertIn("omit --profile", str(refused.exception))
        self.assertIn("--recipe", str(refused.exception))

    def test_tampered_recipe_opens_with_blocker_and_run_fails(self):
        recipe = Path(self.tmp.name) / "tampered-recipe"
        with mock.patch.object(reality, "_local_model_digest", return_value=GOOD):
            reality.prepare_recipe(recipe)
        manifest = json.loads((recipe / reality.RECIPE_NAME).read_text())
        bound = next(iter(manifest["bound_file_digests"]))
        with (recipe / bound).open("ab") as stream:
            stream.write(b"\ntampered\n")

        the_lab = lab.Lab(self.runs, recipe, opener=fake_ollama([(self.model, GOOD)]))
        with mock.patch.object(discover, "tool", side_effect=lambda name, which=None: {
            "name": name, "found": True, "path": f"/usr/bin/{name}", "version": "1.0",
        }):
            setup = the_lab.setup()

        self.assertFalse(setup["ready"])
        self.assertTrue(any("Recipe" in item["message"] for item in setup["blockers"]))
        self.assertEqual("local-ollama", the_lab.profile)
        the_lab.start_run()
        self.assertTrue(wait_for(lambda: the_lab.current()["state"] != "running"))
        self.assertEqual("failed", the_lab.current()["state"])
        self.assertIn("Recipe", the_lab.current()["error"])

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


class LabPageStyleTests(unittest.TestCase):
    """Static checks on the Lab page that need no browser."""

    def test_proof_evidence_text_wraps_inside_its_column(self):
        # The sealed-record note holds a full filesystem path with no spaces.
        # Unwrappable text sets the grid column's minimum width, so it spilled
        # into the next column and scrolled the page sideways on a phone.
        import re
        css = (ROOT / "src" / "harness_workbench" / "lab_static" / "index.html").read_text()
        for selector in (".ev .v", ".ev .note"):
            rules = re.findall(r"^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css, re.M)
            self.assertTrue(any("overflow-wrap: anywhere" in r for r in rules),
                            f"{selector} must allow wrapping anywhere")


def fake_metrics(counts):
    """An opener whose /metrics serves the next of `counts` on each read."""
    values = iter(counts)

    def opener(url, timeout=None):
        if url.endswith("/slots"):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        assert url.endswith("/metrics"), url
        value = next(values, None)
        if value is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(("# HELP x\nllamacpp:prompt_tokens_total 7\n"
                           f"llamacpp:tokens_predicted_total {value}\n").encode())
    return opener


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


BACKEND_EVENT_FIELDS = {"sequence", "kind", "at", "state", "cancel_requested"}


class MetricsReaderTests(unittest.TestCase):
    def test_reads_only_the_generated_token_count(self):
        reading = discover.llamacpp_tokens_predicted("http://h:1/v1", opener=fake_metrics([42]))
        self.assertEqual({"available": True, "url": "http://h:1/metrics", "tokens": 42,
                          "reason": None}, reading)

    def test_metrics_disabled_is_explained(self):
        reading = discover.llamacpp_tokens_predicted("http://h:1/v1", opener=fake_metrics([]))
        self.assertFalse(reading["available"])
        self.assertIn("--metrics", reading["reason"])

    def test_missing_or_unusable_counter_is_unavailable(self):
        for body in (b"llamacpp:prompt_tokens_total 3\n",
                     b"llamacpp:tokens_predicted_total nan\n",
                     b"llamacpp:tokens_predicted_total -1\n",
                     b"llamacpp:tokens_predicted_total x\n"):
            with self.subTest(body=body):
                reading = discover.llamacpp_tokens_predicted(
                    "http://h:1", opener=lambda url, timeout=None, b=body: io.BytesIO(b))
                self.assertFalse(reading["available"])
                self.assertIsNone(reading["tokens"])


class SlotsReaderTests(unittest.TestCase):
    @staticmethod
    def read(payload):
        body = json.dumps(payload).encode()
        return discover.llamacpp_slots(
            "http://h:1/v1", opener=lambda url, timeout=None: io.BytesIO(body))

    def test_returns_only_progress_numbers(self):
        reading = self.read([{
            "id": 2, "id_task": 17, "is_processing": True,
            "prompt": "private prompt", "generated": "private answer",
            "next_token": [{"n_decoded": 9, "has_next_token": True}],
        }, {"id": 3, "is_processing": False}])
        self.assertEqual({
            "available": True, "url": "http://h:1/slots",
            "slots": [
                {"task": 17, "processing": True, "decoded": 9},
                {"task": None, "processing": False, "decoded": None},
            ], "reason": None,
        }, reading)
        self.assertNotIn("private", repr(reading))

    def test_oversize_body_is_unavailable(self):
        body = b"[" + b" " * (discover._SLOTS_BODY_LIMIT + 1)
        reading = discover.llamacpp_slots(
            "http://h:1", opener=lambda url, timeout=None: io.BytesIO(body))
        self.assertFalse(reading["available"])
        self.assertIn("exceeds", reading["reason"])

    def test_malformed_shapes_are_unavailable(self):
        cases = [
            {},
            [{"id_task": True, "is_processing": True,
              "next_token": [{"n_decoded": 1}]}],
            [{"id_task": -1, "is_processing": True,
              "next_token": [{"n_decoded": 1}]}],
            [{"id_task": 1, "is_processing": True}],
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertFalse(self.read(payload)["available"])

    def test_disabled_endpoint_mentions_no_slots(self):
        def disabled(url, timeout=None):
            raise urllib.error.HTTPError(url, 501, "Not Implemented", {}, None)
        reading = discover.llamacpp_slots("http://h:1", opener=disabled)
        self.assertFalse(reading["available"])
        self.assertIn("--no-slots", reading["reason"])


class LabProgressTests(unittest.TestCase):
    """Run events in the backend's lifecycle shape, phases and runtime activity."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.runs = str(base / "runs")
        self.recipe = base / "recipe"

    def lab(self, run_check, profile="local-ollama", opener=dead_ollama):
        return lab.Lab(self.runs, self.recipe, run_check=run_check, opener=opener,
                       discover=lambda r, opener=None, profile=None: {"ready": True},
                       profile=profile)

    @staticmethod
    def finished(the_lab):
        return wait_for(lambda: the_lab.current()["state"] != "running")

    @staticmethod
    def events(view):
        return [entry["event"] for entry in view["events"]["entries"]]

    def test_a_run_reports_lifecycle_and_every_phase_in_backend_shape(self):
        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            for phase in reality.PROGRESS_PHASES:
                progress(phase, {"run_id": "R7"} if phase == "run_started" else {})
            return {"verdict": "fail", "record": os.path.join(runs_root, "R7")}

        the_lab = self.lab(run_check)
        started = the_lab.start_run()
        self.assertRegex(started["id"], r"^[0-9a-f]{32}$")
        self.assertTrue(self.finished(the_lab))
        view = the_lab.current()
        events = self.events(view)
        self.assertEqual(
            [("submitted", "queued"), ("claimed", "running")]
            + [("progress", "running")] * len(reality.PROGRESS_PHASES)
            + [("completed", "succeeded")],
            [(e["kind"], e["state"]) for e in events])
        self.assertEqual(list(reality.PROGRESS_PHASES),
                         [e["phase"] for e in events if e["kind"] == "progress"])
        self.assertEqual(list(range(1, len(events) + 1)), [e["sequence"] for e in events])
        for event in events:
            extra = {"phase"} if event["kind"] == "progress" else set()
            self.assertEqual(BACKEND_EVENT_FIELDS | extra, set(event))
            self.assertFalse(event["cancel_requested"])
        page = view["events"]
        self.assertEqual((1, view["id"], None),
                         (page["schema_version"], page["job_id"], page["next_cursor"]))
        self.assertEqual([e["sequence"] for e in events],
                         [entry["sequence"] for entry in page["entries"]])
        self.assertTrue(all(entry["error"] is None for entry in page["entries"]))
        # A FAIL verdict is still a check that ran: the lifecycle succeeded.
        self.assertEqual(("done", "R7", "verifying"),
                         (view["state"], view["run_id"], view["phase"]))
        self.assertNotIn("_events", view)

    def test_a_run_that_cannot_start_completes_as_failed(self):
        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            progress("recipe", {})
            raise reality.RealityError("recipe.json is missing")

        the_lab = self.lab(run_check)
        the_lab.start_run()
        self.assertTrue(self.finished(the_lab))
        view = the_lab.current()
        self.assertEqual("failed", view["state"])
        self.assertEqual(("completed", "failed"),
                         (self.events(view)[-1]["kind"], self.events(view)[-1]["state"]))
        self.assertIsNone(the_lab.start_run()["previous_duration"])

    def test_after_returns_only_newer_events_and_pages_past_the_limit(self):
        release = threading.Event()

        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            release.wait(5)
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1")}

        the_lab = self.lab(run_check)
        the_lab.start_run()
        self.assertEqual([2], [e["sequence"] for e in self.events(the_lab.current(after=1))])
        self.assertEqual([], self.events(the_lab.current(after=2)))
        with the_lab._lock:
            for _ in range(lab.EVENTS_PAGE_LIMIT + 5):
                the_lab._emit(the_lab._job, "heartbeat", "running")
        first = the_lab.current(after=0)["events"]
        self.assertEqual(lab.EVENTS_PAGE_LIMIT, len(first["entries"]))
        self.assertEqual(str(lab.EVENTS_PAGE_LIMIT), first["next_cursor"])
        rest = the_lab.current(after=int(first["next_cursor"]))["events"]
        self.assertEqual(7, len(rest["entries"]))
        self.assertIsNone(rest["next_cursor"])
        release.set()
        self.assertTrue(self.finished(the_lab))

    def test_the_next_run_knows_how_long_the_last_one_took(self):
        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1")}

        the_lab = self.lab(run_check)
        self.assertIsNone(the_lab.start_run()["previous_duration"])
        self.assertTrue(self.finished(the_lab))
        took = the_lab.current()["finished_at"] - the_lab.current()["started_at"]
        self.assertEqual(took, the_lab.start_run()["previous_duration"])
        self.assertTrue(self.finished(the_lab))

    def test_llamacpp_activity_reports_when_metrics_go_away(self):
        release = threading.Event()

        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            release.wait(5)
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1")}

        with mock.patch.object(lab, "ACTIVITY_INTERVAL", 0.01):
            the_lab = self.lab(run_check, profile="local-llamacpp",
                               opener=fake_metrics([1000, 1000, 1250, 1600]))
            the_lab.start_run()
            self.assertTrue(wait_for(lambda: (the_lab.current()["activity"] or {})
                                     .get("available") is False))
            release.set()
            self.assertTrue(self.finished(the_lab))
        # /metrics answered, then stopped: the loss is reported with its
        # reason, not hidden behind the last good count.
        self.assertIn("--metrics", the_lab.current()["activity"]["reason"])

    def _sampled(self, profile):
        """Whether a run under `profile` starts the llama.cpp activity sampler."""
        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None):
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1")}

        with mock.patch.object(lab.Lab, "_sample_activity") as sampler:
            the_lab = self.lab(run_check, profile=profile)
            the_lab.start_run()
            self.assertTrue(self.finished(the_lab))
        # The sampler thread starts before the check runs, so by the time the
        # run has finished the decision has been made: no timing involved.
        return sampler.called

    def test_activity_follows_the_runtime_not_the_profile_name(self):
        # An owner overlay profile serving llama.cpp under another name gets
        # token activity too; a name check would silently skip it.
        overlay = Path(self.tmp.name) / "profiles.json"
        overlay.write_text(json.dumps({"profiles": {"metal-small": {
            "kind": "local", "runtime": "llama.cpp",
            "base_url": "http://127.0.0.1:8081/v1",
            "models": {"pi": "small-coder:1.5b"},
        }}}), encoding="utf-8")
        with mock.patch.dict(os.environ, {reality.LOCAL_PROFILES_ENV: str(overlay)}):
            self.assertTrue(self._sampled("metal-small"))
        self.assertTrue(self._sampled("local-llamacpp"))

    def test_an_ollama_run_samples_no_llamacpp_activity(self):
        self.assertFalse(self._sampled("local-ollama"))

    def test_llamacpp_activity_reports_a_rising_count(self):
        # Driven synchronously: each /metrics read first records what the
        # sampler reported after the previous read, so no timing is involved.
        the_lab = self.lab(lambda *a, **k: None, profile="local-llamacpp")
        job = {"state": "running", "activity": None}
        stop = threading.Event()
        counts = [1000, 1250, 1600]
        reported = []

        def opener(url, timeout=None):
            if url.endswith("/slots"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            reported.append(job["activity"] and job["activity"]["tokens_since_start"])
            if len(reported) == len(counts):
                stop.set()
            return io.BytesIO(
                f"llamacpp:tokens_predicted_total {counts[len(reported) - 1]}\n".encode())

        the_lab._opener = opener
        with mock.patch.object(lab, "ACTIVITY_INTERVAL", 0):
            the_lab._sample_activity(job, stop)
        self.assertEqual([None, 0, 250], reported)
        self.assertEqual(600, job["activity"]["tokens_since_start"])
        self.assertEqual("llama-server /metrics", job["activity"]["source"])
        self.assertIn("every client", job["activity"]["scope"])

    def test_a_restarted_server_keeps_counting_instead_of_going_negative(self):
        the_lab = self.lab(lambda *a, **k: None, profile="local-llamacpp")
        job = {"state": "running", "activity": None}
        stop = threading.Event()
        counts = [1000, 1300, 1100, 1150]
        reported = []

        def opener(url, timeout=None):
            if url.endswith("/slots"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            reported.append(job["activity"] and job["activity"]["tokens_since_start"])
            if len(reported) == len(counts):
                stop.set()
            return io.BytesIO(
                f"llamacpp:tokens_predicted_total {counts[len(reported) - 1]}\n".encode())

        the_lab._opener = opener
        with mock.patch.object(lab, "ACTIVITY_INTERVAL", 0):
            the_lab._sample_activity(job, stop)
        # 1000 -> 1300 is +300. The drop to 1100 means a restart, after which
        # the server has generated 1100 tokens from zero: +1100, then +50.
        # A first-reading baseline would have reported 100 or gone negative.
        self.assertEqual([None, 0, 300, 1400], reported)
        self.assertEqual(1450, job["activity"]["tokens_since_start"])

    def _sample_sequence(self, metrics, slots):
        the_lab = self.lab(lambda *a, **k: None, profile="local-llamacpp")
        job = {"state": "running", "activity": None}
        stop = threading.Event()
        reported = []
        tick = 0

        calls = 0

        def opener(url, timeout=None):
            nonlocal tick, calls
            calls += 1
            if calls > 4 * len(slots):  # a sampler that never reads /slots
                stop.set()
                raise urllib.error.HTTPError(url, 501, "Not Implemented", {}, None)
            if url.endswith("/metrics"):
                value = metrics[min(tick, len(metrics) - 1)]
                if value is None:
                    raise urllib.error.HTTPError(url, 501, "Not Implemented", {}, None)
                return io.BytesIO(
                    f"llamacpp:tokens_predicted_total {value}\n".encode())
            self.assertTrue(url.endswith("/slots"), url)
            if slots[tick] is None:
                tick += 1
                if tick == len(slots):
                    stop.set()
                reported.append(job["activity"] and dict(job["activity"]))
                raise urllib.error.HTTPError(url, 501, "Not Implemented", {}, None)
            result = io.BytesIO(json.dumps(slots[tick]).encode())
            tick += 1
            if tick == len(slots):
                stop.set()
            reported.append(job["activity"] and dict(job["activity"]))
            return result

        the_lab._opener = opener
        with mock.patch.object(lab, "ACTIVITY_INTERVAL", 0):
            the_lab._sample_activity(job, stop)
        return reported, job["activity"]

    @staticmethod
    def _slot(task, processing, decoded):
        return [{"id_task": task, "is_processing": processing,
                 "next_token": [{"n_decoded": decoded}]}]

    def test_slots_show_in_flight_tokens_without_double_counting_metrics(self):
        reported, activity = self._sample_sequence(
            [1000, 1000, 1120],
            [self._slot(7, True, 50), self._slot(7, True, 120),
             self._slot(7, False, 0)])
        self.assertEqual([None, 0, 70],
                         [a and a["tokens_since_start"] for a in reported])
        self.assertEqual(70, activity["tokens_since_start"])
        self.assertEqual("llama-server /metrics + /slots", activity["source"])

    def test_a_request_ending_between_the_two_reads_never_steps_back(self):
        # Tick 3: /metrics is read before the request finishes (still 1000),
        # /slots after it finished (idle, 0). Unclamped, the count would fall
        # from 120 to 0 for one tick; the next tick's /metrics restores it.
        reported, activity = self._sample_sequence(
            [1000, 1000, 1000, 1120],
            [[], self._slot(7, True, 120), self._slot(7, False, 0),
             self._slot(7, False, 0)])
        self.assertEqual([None, 0, 120, 120],
                         [a and a["tokens_since_start"] for a in reported])
        self.assertEqual(120, activity["tokens_since_start"])

    def test_slots_only_keeps_high_water_mark_and_tracks_a_new_task(self):
        reported, activity = self._sample_sequence(
            [None, None, None, None],
            [self._slot(7, True, 50), self._slot(7, True, 120),
             self._slot(7, False, 0), self._slot(8, True, 30)])
        self.assertEqual([None, 0, 70, 70],
                         [a and a["tokens_since_start"] for a in reported])
        self.assertEqual(100, activity["tokens_since_start"])
        self.assertEqual("llama-server /slots", activity["source"])
        self.assertTrue(activity["approximate"])
        self.assertIn("sampling interval", activity["note"])

    def test_activity_unavailable_names_both_endpoint_causes(self):
        _, activity = self._sample_sequence([None], [None])
        self.assertFalse(activity["available"])
        self.assertIn("--metrics", activity["reason"])
        self.assertIn("--no-slots", activity["reason"])

    def test_metrics_restart_carries_slot_progress_and_allows_reused_task_id(self):
        _, activity = self._sample_sequence(
            [1000, 1000, 0],
            [self._slot(9, True, 10), self._slot(9, True, 30),
             self._slot(1, True, 5)])
        self.assertEqual(25, activity["tokens_since_start"])

    def test_slots_only_lower_task_id_carries_progress_across_restart(self):
        _, activity = self._sample_sequence(
            [None, None, None],
            [self._slot(9, True, 10), self._slot(9, True, 30),
             self._slot(1, True, 5)])
        self.assertEqual(25, activity["tokens_since_start"])

    def test_ollama_runs_report_no_activity(self):
        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1")}

        the_lab = self.lab(run_check, opener=fake_metrics([1]))
        the_lab.start_run()
        self.assertTrue(self.finished(the_lab))
        self.assertIsNone(the_lab.current()["activity"])


class LabShutdownTests(unittest.TestCase):
    """A Lab that is told to stop ends its running check first."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.runs, self.recipe = str(base / "runs"), base / "recipe"

    def lab(self, run_check):
        return lab.Lab(self.runs, self.recipe, run_check=run_check, opener=dead_ollama,
                       discover=lambda r, opener=None, profile=None: {"ready": True})

    def test_shutdown_stops_the_running_check_and_returns_its_end(self):
        seen = {}

        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            seen["stopped"] = stop.wait(5)
            return {"verdict": "invalid", "record": os.path.join(runs_root, "R9")}

        the_lab = self.lab(run_check)
        the_lab.start_run()
        final = the_lab.shutdown(wait=5)
        self.assertTrue(seen["stopped"])
        self.assertEqual(("done", "R9"), (final["state"], final["run_id"]))

    def test_shutdown_with_nothing_running_does_nothing(self):
        self.assertIsNone(self.lab(lambda *a, **k: None).shutdown(wait=0.1))

    def test_a_check_that_will_not_stop_is_reported_as_running(self):
        release = threading.Event()

        def run_check(check, *, runs_root, recipe_dir, profile=None, progress=None, stop=None):
            release.wait(5)  # ignores stop
            return {"verdict": "pass", "record": os.path.join(runs_root, "R1")}

        the_lab = self.lab(run_check)
        the_lab.start_run()
        self.assertEqual("running", the_lab.shutdown(wait=0.2)["state"])
        release.set()
        self.assertTrue(wait_for(lambda: the_lab.current()["state"] == "done"))


class LabProgressHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.srv = LabServer(str(base / "runs"), base / "recipe", dead_ollama)

    def tearDown(self):
        self.srv.close()
        self.tmp.cleanup()

    def test_after_is_read_from_the_query(self):
        self.assertEqual(202, self.srv.json("/api/runs", "POST", {})[0])
        status, view = self.srv.json("/api/runs/current?after=1")
        self.assertEqual(200, status)
        self.assertEqual([2], [e["sequence"] for e in view["events"]["entries"]])

    def test_a_malformed_after_is_refused(self):
        self.srv.json("/api/runs", "POST", {})
        for query in ("after=-1", "after=x", "after=1&after=2", "after=", "after=01"):
            with self.subTest(query=query):
                self.assertEqual(400, self.srv.json("/api/runs/current?" + query)[0])

    def test_the_query_does_not_bypass_the_guard(self):
        self.assertEqual(401, self.srv.request("/api/runs/current?after=0", auth=False)[0])
        self.assertEqual(403, self.srv.request(
            "/api/runs/current?after=0", headers={"Origin": "http://evil.test"})[0])


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
