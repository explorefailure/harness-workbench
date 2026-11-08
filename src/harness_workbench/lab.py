"""`hwb lab` -- a minimal local web GUI over the packaged Reality Check.

MVP scope: Discover, Try, Proof, Recipe and History for
`coding/python-bug-fix`.  The Lab composes existing machinery and owns no
evidence of its own:

* runs execute through `reality.run_check`, exactly as `hwb try` does;
* every verdict shown for a stored run is re-derived from its sealed record by
  `reality.verify_run`, never cached from the Lab's own memory;
* the only file the Lab writes is the Recipe's editable
  `model_selection.json`, validated by the same rules `hwb try` applies.

Deliberately NOT designed yet (tracked on the project page as deferred design):
live step-by-step progress (the runner emits no events before the result
exists), the security model for a local server that can launch an agent, and
where the GUI finally lives.  Interim defaults only: bind to 127.0.0.1, and
accept writes only as `application/json`.  These are not a security design.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from . import discover as discovermod, interrupt, reality

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATIC = Path(__file__).resolve().parent / "lab_static"
_MAX_BODY = 64 * 1024
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LabError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Lab:
    """State and operations behind the HTTP surface (testable without HTTP)."""

    def __init__(self, runs_root: str, recipe_dir: Path, *,
                 run_check: Callable[..., dict[str, Any]] = reality.run_check,
                 discover: Callable[..., dict[str, Any]] = discovermod.discover,
                 opener=urlopen):
        self.runs_root = runs_root
        self.recipe_dir = recipe_dir
        self._run_check = run_check
        self._discover = discover
        self._opener = opener
        self._lock = threading.Lock()
        self._job: Optional[dict[str, Any]] = None
        self._job_seq = 0

    # -- discovery ---------------------------------------------------------
    def setup(self) -> dict[str, Any]:
        return self._discover(self.recipe_dir, opener=self._opener)

    # -- runs --------------------------------------------------------------
    def start_run(self) -> dict[str, Any]:
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise LabError(409, "A Reality Check is already running.")
            self._job_seq += 1
            job = {"id": self._job_seq, "state": "running",
                   "check": reality.CHECK, "started_at": time.time(),
                   "finished_at": None, "result": None, "error": None}
            self._job = job
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return dict(job)

    def _execute(self, job: dict[str, Any]) -> None:
        try:
            result = self._run_check(reality.CHECK, runs_root=self.runs_root,
                                     recipe_dir=str(self.recipe_dir))
            update = {"state": "done", "result": result,
                      "run_id": Path(result["record"]).name}
        except reality.RealityError as error:
            update = {"state": "failed", "error": str(error)}
        except Exception as error:  # surface, never swallow, unexpected faults
            update = {"state": "failed", "error": f"{type(error).__name__}: {error}"}
        with self._lock:
            job.update(update, finished_at=time.time())

    def current(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._job) if self._job else {"state": "idle"}

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
        """Select a local model and record its current Ollama digest."""
        if not isinstance(model, str) or not model or len(model) > 200:
            raise LabError(400, "Send {\"model\": \"<ollama model name>\"}.")
        state = discovermod.recipe_state(self.recipe_dir)
        inventory = discovermod.ollama(state["base_url"] or "http://127.0.0.1:11434/v1",
                                       opener=self._opener)
        if not inventory["reachable"]:
            raise LabError(503, f"Ollama did not answer at {inventory['url']}.")
        match = next((m for m in inventory["models"] if m["name"] == model), None)
        if match is None or len(match["digest"]) != 64:
            raise LabError(422, f"Ollama has no model {model!r} with a full digest.")
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise LabError(409, "Wait for the running check before changing the model.")
            try:
                reality.prepare_recipe(self.recipe_dir.resolve())
                path = self.recipe_dir / reality.EDITABLE_RECIPE_FILE
                selection = json.loads(path.read_text(encoding="utf-8"))
                local = selection["profiles"]["local-ollama"]
                local["models"]["pi"] = model
                # Choosing a model sets the person's baseline; the run itself
                # refreshes the checked digest to whatever Ollama serves then.
                local["baseline_digest"] = match["digest"]
                local["model_digest"] = match["digest"]
                path.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
                reality._validate_recipe(self.recipe_dir.resolve())
            except (reality.RealityError, OSError, ValueError, KeyError, TypeError) as error:
                raise LabError(422, f"Recipe not updated: {error}") from error
        return self.recipe()


def _handler(lab: Lab):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hwb-lab"

        def log_message(self, fmt, *args):  # keep the terminal for the URL
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, error: LabError) -> None:
            self._json(error.status, {"error": str(error)})

        def _body(self) -> Any:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                raise LabError(415, "Writes must be application/json.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                raise LabError(413, "Request body too large.")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                return json.loads(raw or b"{}")
            except ValueError as error:
                raise LabError(400, f"Body is not JSON: {error}") from error

        def do_GET(self):  # noqa: N802 (stdlib naming)
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    body = (STATIC / "index.html").read_bytes()
                    return self._send(200, body, "text/html; charset=utf-8")
                if path == "/api/setup":
                    return self._json(200, lab.setup())
                if path == "/api/runs":
                    return self._json(200, {"runs": lab.list_runs()})
                if path == "/api/runs/current":
                    return self._json(200, lab.current())
                if path.startswith("/api/runs/"):
                    return self._json(200, lab.run_detail(path[len("/api/runs/"):]))
                if path == "/api/recipe":
                    return self._json(200, lab.recipe())
                raise LabError(404, "Not found.")
            except LabError as error:
                self._error(error)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                body = self._body()
                if path == "/api/runs":
                    return self._json(202, lab.start_run())
                if path == "/api/recipe/model":
                    model = body.get("model") if isinstance(body, dict) else None
                    return self._json(200, lab.set_model(model))
                raise LabError(404, "Not found.")
            except LabError as error:
                self._error(error)

    return Handler


def make_server(lab: Lab, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((HOST, port), _handler(lab))
    server.daemon_threads = True
    return server


def serve(runs_root: str, recipe_dir: Optional[str], port: int, open_browser: bool) -> int:
    recipe = Path(recipe_dir).expanduser() if recipe_dir else discovermod.default_recipe_dir()
    lab = Lab(runs_root, recipe)
    try:
        server = make_server(lab, port)
    except OSError as error:
        raise LabError(1, f"could not listen on {HOST}:{port}: {error}") from error
    url = f"http://{HOST}:{server.server_address[1]}/"
    print(f"Harness Workbench Lab (MVP) on {url}")
    print(f"  runs   {os.path.abspath(runs_root)}")
    print(f"  recipe {recipe}")
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
