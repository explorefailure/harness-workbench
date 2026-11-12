"""`hwb lab` -- a minimal local web GUI over the packaged Reality Check.

MVP scope: Discover, Try, Proof, Recipe and History for
`coding/python-bug-fix`.  The Lab composes existing machinery and owns no
evidence of its own:

* runs execute through `reality.run_check`, exactly as `hwb try` does;
* every verdict shown for a stored run is re-derived from its sealed record by
  `reality.verify_run`, never cached from the Lab's own memory;
* the only file the Lab writes is the Recipe's editable
  `model_selection.json`, validated by the same rules `hwb try` applies.

Local-server security model (the Lab can launch an agent, so it follows the
tools that do the same -- Jupyter, the patched MCP Inspector -- not the
dev servers that only serve assets):

* loopback only, on a port the OS assigns per launch unless ``--port`` asks
  for one; the socket never shares or takes over a port (no SO_REUSEADDR,
  SO_EXCLUSIVEADDRUSE where it exists), so a clash fails loudly;
* a per-launch secret token: the printed/opened URL carries it once, the page
  trades it for an HttpOnly SameSite=Strict cookie, and scripts may send
  ``Authorization: Bearer <token>`` instead;
* every request first passes one guard: an exact Host allowlist (defeats DNS
  rebinding), a same-origin check on any Origin header, then the token.
  Routes live in one table behind that guard, so no route can skip it.

Not covered: another program running as the same user that grabs the port
first can impersonate the Lab; that program could read your files anyway.

Still deferred: live step-by-step progress (the runner emits no events before
the result exists) and where the GUI finally lives.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from http.cookies import CookieError, SimpleCookie
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from . import discover as discovermod, interrupt, reality

HOST = "127.0.0.1"
# 0 = the OS assigns a free port per launch (RFC 8252's loopback practice):
# no clash with another program's fixed port, nothing for a page to guess.
DEFAULT_PORT = 0
STATIC = Path(__file__).resolve().parent / "lab_static"
_MAX_BODY = 64 * 1024
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LabError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# Run events use the backend's lifecycle-history shape (job_events, schema 1):
# a page of entries {sequence, event, error} whose events carry sequence, kind,
# at, state and cancel_requested. Lifecycle kinds are the backend's. The Lab adds
# one kind, "progress" (state "running"), with a `phase` from
# reality.PROGRESS_PHASES. "completed"/"succeeded" means the check reached a
# verdict, whatever that verdict is; "completed"/"failed" means it could not run.
EVENTS_SCHEMA_VERSION = 1
EVENTS_PAGE_LIMIT = 100
ACTIVITY_INTERVAL = 2.0


class Lab:
    """State and operations behind the HTTP surface (testable without HTTP)."""

    def __init__(self, runs_root: str, recipe_dir: Path, *,
                 run_check: Callable[..., dict[str, Any]] = reality.run_check,
                 discover: Callable[..., dict[str, Any]] = discovermod.discover,
                 opener=urlopen, profile: str = discovermod.PROFILE):
        if profile not in discovermod.PROFILES:
            raise LabError(1, f"unknown profile {profile!r}")
        self.runs_root = runs_root
        self.recipe_dir = recipe_dir
        self.profile = profile
        self._run_check = run_check
        self._discover = discover
        self._opener = opener
        self._lock = threading.Lock()
        self._job: Optional[dict[str, Any]] = None
        self._last_duration: Optional[float] = None

    # -- discovery ---------------------------------------------------------
    def setup(self) -> dict[str, Any]:
        return self._discover(self.recipe_dir, profile=self.profile, opener=self._opener)

    # -- runs --------------------------------------------------------------
    def start_run(self) -> dict[str, Any]:
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise LabError(409, "A Reality Check is already running.")
            job = {"id": uuid.uuid4().hex, "state": "running",
                   "check": reality.CHECK, "started_at": time.time(),
                   "finished_at": None, "result": None, "error": None,
                   "phase": None, "phases": list(reality.PROGRESS_PHASES),
                   "activity": None, "previous_duration": self._last_duration,
                   "_events": []}
            self._job = job
            self._emit(job, "submitted", "queued")
            self._emit(job, "claimed", "running")
            view = self._view(job, None)
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return view

    def _emit(self, job: dict[str, Any], kind: str, state: str, **extra: Any) -> None:
        # Caller holds self._lock.
        events = job["_events"]
        events.append({"sequence": len(events) + 1, "kind": kind, "at": time.time(),
                       "state": state, "cancel_requested": False, **extra})

    def _progress(self, job: dict[str, Any]) -> Callable[[str, dict[str, Any]], None]:
        def report(phase: str, detail: dict[str, Any]) -> None:
            with self._lock:
                if job["state"] != "running":
                    return
                job["phase"] = phase
                if phase == "run_started" and detail.get("run_id"):
                    job["run_id"] = detail["run_id"]
                self._emit(job, "progress", "running", phase=phase)
        return report

    def _execute(self, job: dict[str, Any]) -> None:
        stop = threading.Event()
        if self.profile == "local-llamacpp":
            threading.Thread(target=self._sample_activity, args=(job, stop),
                             daemon=True).start()
        try:
            result = self._run_check(reality.CHECK, runs_root=self.runs_root,
                                     recipe_dir=str(self.recipe_dir), profile=self.profile,
                                     progress=self._progress(job))
            update = {"state": "done", "result": result,
                      "run_id": Path(result["record"]).name}
        except reality.RealityError as error:
            update = {"state": "failed", "error": str(error)}
        except Exception as error:  # surface, never swallow, unexpected faults
            update = {"state": "failed", "error": f"{type(error).__name__}: {error}"}
        finally:
            stop.set()
        with self._lock:
            job.update(update, finished_at=time.time())
            self._emit(job, "completed", "succeeded" if job["state"] == "done" else "failed")
            if job["state"] == "done":
                self._last_duration = job["finished_at"] - job["started_at"]

    def _sample_activity(self, job: dict[str, Any], stop: threading.Event) -> None:
        """Track llama-server's generated-token count while the run lasts."""
        try:
            base_url = discovermod.recipe_state(self.recipe_dir, self.profile).get("base_url")
        except Exception:
            base_url = None
        base_url = base_url or discovermod.LLAMACPP_DEFAULT_URL
        previous: Optional[int] = None
        total = 0
        while not stop.is_set():
            reading = discovermod.llamacpp_tokens_predicted(base_url, opener=self._opener)
            with self._lock:
                if job["state"] != "running":
                    return
                if reading["available"]:
                    tokens = reading["tokens"]
                    if previous is not None:
                        # A drop means the server restarted and counts from 0.
                        total += tokens - previous if tokens >= previous else tokens
                    previous = tokens
                    job["activity"] = {
                        "available": True, "source": "llama-server /metrics",
                        "tokens_since_start": total,
                        "scope": "every client of this llama-server, not only this run",
                        "at": time.time()}
                else:
                    job["activity"] = {"available": False, "reason": reading["reason"]}
            stop.wait(ACTIVITY_INTERVAL)

    def _view(self, job: dict[str, Any], after: Optional[int]) -> dict[str, Any]:
        # Caller holds self._lock.
        view = {key: value for key, value in job.items() if key != "_events"}
        newer = [e for e in job["_events"] if after is None or e["sequence"] > after]
        page = newer[:EVENTS_PAGE_LIMIT]
        view["events"] = {
            "schema_version": EVENTS_SCHEMA_VERSION, "job_id": job["id"],
            "entries": [{"sequence": e["sequence"], "event": dict(e), "error": None}
                        for e in page],
            # Pass as ?after= to read the next page; None when this page is the end.
            "next_cursor": (str(page[-1]["sequence"])
                            if len(newer) > EVENTS_PAGE_LIMIT else None),
        }
        return view

    def current(self, after: Optional[int] = None) -> dict[str, Any]:
        with self._lock:
            return self._view(self._job, after) if self._job else {"state": "idle"}

    def list_runs(self) -> list[dict[str, Any]]:
        root = Path(self.runs_root)
        if not root.is_dir():
            return []
        rows = []
        for entry in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if not entry.is_dir() or not _RUN_ID.match(entry.name):
                continue
            life = interrupt.inspect_state(str(entry))
            record = life.get("record") or {}
            steps = record.get("steps") if isinstance(record.get("steps"), list) else []
            step_ids = [s.get("id") for s in steps if isinstance(s, dict)]
            rows.append({
                "run_id": entry.name,
                "lifecycle": life["state"],
                "run_class": record.get("run_class"),
                "reality_check": reality.STEP_ID in step_ids,
            })
        return rows

    def run_detail(self, run_id: str) -> dict[str, Any]:
        if not _RUN_ID.match(run_id):
            raise LabError(400, "Not a run id.")
        run_dir = Path(self.runs_root) / run_id
        if not run_dir.is_dir():
            raise LabError(404, f"No run {run_id!r} in {self.runs_root}.")
        try:
            verified = reality.verify_run(run_dir.resolve(), self.recipe_dir)
        except reality.RealityError as error:
            raise LabError(422, str(error)) from error
        return {
            "schema": reality.RESULT_SCHEMA,
            "job": reality.CHECK,
            **verified,
            "record": str(run_dir.resolve()),
            "recipe": str(self.recipe_dir),
            "recipe_contract": "editable configuration; only model_selection.json may change",
            "model_digest_contract": reality.MODEL_DIGEST_CONTRACT,
            "blind_spots": list(reality.BLIND_SPOTS),
            "rerun": f"hwb try {reality.CHECK} --recipe {self.recipe_dir}",
            "rederived": True,
        }

    # -- recipe ------------------------------------------------------------
    def recipe(self) -> dict[str, Any]:
        state = discovermod.recipe_state(self.recipe_dir)
        state["rerun"] = f"hwb try {reality.CHECK} --recipe {self.recipe_dir}"
        state["editable_file"] = reality.EDITABLE_RECIPE_FILE
        manifest = self.recipe_dir / reality.RECIPE_NAME
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                state["bound_files"] = sorted(data.get("bound_file_digests", {}))
            except (OSError, ValueError):
                state["bound_files"] = []
        else:
            state["bound_files"] = []
        return state

    def set_model(self, model: Any) -> dict[str, Any]:
        """Select a local model and record its current digest (and llama.cpp build)."""
        if not isinstance(model, str) or not model or len(model) > 200:
            raise LabError(400, "Send {\"model\": \"<model name>\"}.")
        state = discovermod.recipe_state(self.recipe_dir, self.profile)
        inventory = discovermod.runtime(state, opener=self._opener)
        label = inventory["label"]
        if not inventory["reachable"]:
            raise LabError(503, f"{label} did not answer at {inventory['url']}.")
        match = next((m for m in inventory["models"] if m["name"] == model), None)
        if match is None or len(match["digest"]) != 64:
            raise LabError(422, f"{label} serves no model {model!r} with a full digest.")
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise LabError(409, "Wait for the running check before changing the model.")
            try:
                reality.prepare_recipe(self.recipe_dir.resolve(),
                                       None if state["exists"] else self.profile)
                path = self.recipe_dir / reality.EDITABLE_RECIPE_FILE
                selection = json.loads(path.read_text(encoding="utf-8"))
                local = selection["profiles"][selection["active"]]
                local["models"]["pi"] = model
                # Choosing a model sets the person's baseline; the run itself
                # refreshes the checked digest to whatever the runtime serves then.
                local["baseline_digest"] = match["digest"]
                local["model_digest"] = match["digest"]
                if inventory.get("build") and local.get("runtime") == "llama.cpp":
                    local["baseline_runtime_build"] = inventory["build"]
                    local["runtime_build"] = inventory["build"]
                path.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
                reality._validate_recipe(self.recipe_dir.resolve())
            except (reality.RealityError, OSError, ValueError, KeyError, TypeError) as error:
                raise LabError(422, f"Recipe not updated: {error}") from error
        return self.recipe()


# The only routes. The guard in Handler._dispatch runs before this lookup, so
# a route added here cannot bypass it; the tests walk this table.
ROUTES = {
    ("GET", "/"): "page",
    ("GET", "/index.html"): "page",
    ("GET", "/api/setup"): "setup",
    ("GET", "/api/runs"): "runs",
    ("GET", "/api/runs/current"): "current",
    ("GET", "/api/runs/<id>"): "run_detail",
    ("GET", "/api/recipe"): "recipe",
    ("POST", "/api/runs"): "start_run",
    ("POST", "/api/recipe/model"): "set_model",
}


def _after(query: str) -> Optional[int]:
    values = parse_qs(query, keep_blank_values=True).get("after")
    if not values:
        return None
    if len(values) != 1 or not re.fullmatch(r"(0|[1-9][0-9]{0,8})", values[0]):
        raise LabError(400, "after must be one non-negative event sequence number.")
    return int(values[0])


def _route(method: str, path: str):
    name = ROUTES.get((method, path))
    if name is not None:
        return name, None
    if method == "GET" and path.startswith("/api/runs/"):
        return ROUTES[("GET", "/api/runs/<id>")], path[len("/api/runs/"):]
    if any(p == path for (_, p) in ROUTES) or path.startswith("/api/runs/"):
        raise LabError(405, "Method not allowed.")
    raise LabError(404, "Not found.")


def cookie_name(port: int) -> str:
    # Cookies ignore ports, so the name carries it: two Labs never overwrite
    # each other's session.
    return f"hwb_lab_{port}"


def _handler(lab: Lab):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hwb-lab"

        def log_message(self, fmt, *args):  # keep the terminal for the URL
            return

        def _send(self, status: int, body: bytes, ctype: str,
                  extra: Optional[dict] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, error: LabError) -> None:
            if urlparse(self.path).path.startswith("/api/"):
                return self._json(error.status, {"error": str(error)})
            self._send(error.status, (str(error) + "\n").encode("utf-8"),
                       "text/plain; charset=utf-8")

        def _body(self) -> Any:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                raise LabError(415, "Writes must be application/json.")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as error:
                raise LabError(400, "Bad Content-Length.") from error
            if length < 0 or length > _MAX_BODY:
                raise LabError(413, "Request body too large.")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw or b"{}")
            except ValueError as error:
                raise LabError(400, f"Body is not JSON: {error}") from error

        # ---- the guard -----------------------------------------------------
        def _origins(self) -> tuple:
            port = self.server.server_address[1]
            return (f"127.0.0.1:{port}", f"localhost:{port}")

        def _token_ok(self, candidate: Optional[str]) -> bool:
            return bool(candidate) and hmac.compare_digest(
                candidate.encode("utf-8", "replace"),
                self.server.lab_token.encode("utf-8"))

        def _guard(self, method: str) -> str:
            """Reject before routing. Returns how the caller authenticated."""
            hosts = self.headers.get_all("Host") or []
            if len(hosts) != 1 or hosts[0].strip().lower() not in self._origins():
                raise LabError(403, "Host not allowed.")
            origins = self.headers.get_all("Origin") or []
            if len(origins) > 1 or (origins and origins[0].strip().lower() not in
                                    tuple("http://" + o for o in self._origins())):
                raise LabError(403, "Cross-origin request refused.")
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer ") and self._token_ok(auth[len("Bearer "):].strip()):
                return "bearer"
            try:
                jar = SimpleCookie(self.headers.get("Cookie") or "")
            except CookieError:
                jar = SimpleCookie()
            morsel = jar.get(cookie_name(self.server.server_address[1]))
            if morsel is not None and self._token_ok(morsel.value):
                # A browser always sends Origin on a write; a cookie write
                # without one is not the Lab page.
                if method == "POST" and not origins:
                    raise LabError(403, "Cross-origin request refused.")
                return "cookie"
            raise LabError(401, "Open the URL that `hwb lab` printed; it carries "
                                "this launch's token.")

        def _bootstrap(self, query: str) -> bool:
            """Trade a valid ?token= on the page URL for the session cookie."""
            token = (parse_qs(query).get("token") or [None])[0]
            if token is None:
                return False
            if not self._token_ok(token):
                raise LabError(401, "That token is not this launch's token.")
            port = self.server.server_address[1]
            self._send(303, b"", "text/plain; charset=utf-8", {
                "Location": "/",
                "Set-Cookie": (f"{cookie_name(port)}={self.server.lab_token}; "
                               "HttpOnly; SameSite=Strict; Path=/"),
            })
            return True

        def _dispatch(self, method: str) -> None:
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                hosts = self.headers.get_all("Host") or []
                if (method == "GET" and path in ("/", "/index.html") and parsed.query
                        and len(hosts) == 1 and hosts[0].strip().lower() in self._origins()
                        and not self.headers.get("Origin")):
                    if self._bootstrap(parsed.query):
                        return
                self._guard(method)
                name, arg = _route(method, path)
                body = self._body() if method == "POST" else None
                if name == "page":
                    return self._send(200, (STATIC / "index.html").read_bytes(),
                                      "text/html; charset=utf-8")
                if name == "setup":
                    return self._json(200, lab.setup())
                if name == "runs":
                    return self._json(200, {"runs": lab.list_runs()})
                if name == "current":
                    return self._json(200, lab.current(_after(parsed.query)))
                if name == "run_detail":
                    return self._json(200, lab.run_detail(arg))
                if name == "recipe":
                    return self._json(200, lab.recipe())
                if name == "start_run":
                    return self._json(202, lab.start_run())
                if name == "set_model":
                    model = body.get("model") if isinstance(body, dict) else None
                    return self._json(200, lab.set_model(model))
                raise LabError(500, f"route {name!r} has no handler")
            except LabError as error:
                self._error(error)

        def do_GET(self):  # noqa: N802 (stdlib naming)
            self._dispatch("GET")

        def do_POST(self):  # noqa: N802
            self._dispatch("POST")

    return Handler


class _LabHTTPServer(ThreadingHTTPServer):
    # http.server turns SO_REUSEADDR on. On macOS/BSD that lets this socket
    # share a port with another program's wildcard listener and silently take
    # its loopback traffic; on Windows it can take over a port in use. Off.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows only
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(lab: Lab, port: int = DEFAULT_PORT,
                token: Optional[str] = None) -> ThreadingHTTPServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise LabError(1, f"invalid port {port!r}")
    server = _LabHTTPServer((HOST, port), _handler(lab))
    server.lab_token = token or secrets.token_urlsafe(32)
    return server


def serve(runs_root: str, recipe_dir: Optional[str], port: int, open_browser: bool,
          profile: str = discovermod.PROFILE) -> int:
    recipe = (Path(recipe_dir).expanduser() if recipe_dir
              else discovermod.default_recipe_dir(profile))
    lab = Lab(runs_root, recipe, profile=profile)
    try:
        server = make_server(lab, port)
    except OSError as error:
        hint = (" -- omit --port to use a free one, or wait a minute if a Lab"
                " just stopped on it" if port else "")
        raise LabError(1, f"could not listen on {HOST}:{port}: {error}{hint}") from error
    url = f"http://{HOST}:{server.server_address[1]}/?token={server.lab_token}"
    print(f"Harness Workbench Lab (MVP) on {url}")
    print("  scripts: send 'Authorization: Bearer <the token above>'")
    print(f"  runs   {os.path.abspath(runs_root)}")
    print(f"  recipe {recipe}")
    print(f"  profile {profile} (for a new Recipe)")
    print("  localhost only; press Ctrl-C to stop", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
