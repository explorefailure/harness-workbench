"""Read-only local setup discovery for the packaged Reality Check.

`hwb setup` and the Lab's Discover screen answer one question: can this
machine attempt `coding/python-bug-fix` right now, and if not, what is
missing?  Discovery never writes, installs, pulls, or starts anything.

It deliberately probes the same things a real run will use -- `pi` and `node`
on PATH, the Ollama endpoint declared by the `local-ollama` profile, and the
digest rule the adapter enforces -- so "ready" predicts what the run will
check instead of describing the machine in general.  It is still a prediction:
the run itself remains the only evidence.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import urlopen

from . import __version__

SCHEMA = "hwb-setup/v0.1"
CHECK = "coding/python-bug-fix"
PROFILE = "local-ollama"
_PROBE_TIMEOUT = 5


def _shipped_selection() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "subjects" / "model_selection.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _tags_url(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3]
    return endpoint + "/api/tags"


def _sysctl(name: str) -> Optional[str]:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True,
                             text=True, timeout=_PROBE_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value or None


def machine() -> dict[str, Any]:
    """Platform, CPU and memory, from the OS without third-party probes."""
    info: dict[str, Any] = {
        "system": platform.system(),
        "release": platform.release(),
        "arch": platform.machine(),
        "cpu": None,
        "model": None,
        "memory_bytes": None,
    }
    if info["system"] == "Darwin":
        info["cpu"] = _sysctl("machdep.cpu.brand_string")
        info["model"] = _sysctl("hw.model")
        mem = _sysctl("hw.memsize")
        info["memory_bytes"] = int(mem) if mem and mem.isdigit() else None
    elif info["system"] == "Linux":
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    info["memory_bytes"] = int(line.split()[1]) * 1024
                    break
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
        except (OSError, ValueError, IndexError):
            pass
    return info


def tool(name: str, which=shutil.which) -> dict[str, Any]:
    """Locate an executable on PATH and ask it for its version."""
    path = which(name)
    if not path:
        return {"name": name, "found": False, "path": None, "version": None}
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=_PROBE_TIMEOUT, check=False)
        text = (out.stdout or out.stderr).strip().splitlines()
        version = text[0].strip() if text else None
    except (OSError, subprocess.SubprocessError):
        version = None
    return {"name": name, "found": True, "path": path, "version": version}


def ollama(base_url: str, opener=urlopen) -> dict[str, Any]:
    """List local models from Ollama's own inventory endpoint."""
    url = _tags_url(base_url)
    try:
        with opener(url, timeout=_PROBE_TIMEOUT) as response:
            payload = json.load(response)
    except (URLError, OSError, ValueError) as error:
        return {"reachable": False, "url": url, "models": [], "error": str(error)}
    models = []
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            models.append({
                "name": item["name"],
                "digest": str(item.get("digest") or ""),
                "size_bytes": item.get("size") if isinstance(item.get("size"), int) else None,
            })
    return {"reachable": True, "url": url, "models": models, "error": None}


def recipe_state(recipe_dir: Path) -> dict[str, Any]:
    """The model a run would use: the Recipe's, or the shipped default."""
    selection_path = recipe_dir / "model_selection.json"
    exists = (recipe_dir / "recipe.json").is_file()
    source = "recipe" if exists else "shipped default"
    try:
        selection = (json.loads(selection_path.read_text(encoding="utf-8"))
                     if exists else _shipped_selection())
        local = selection["profiles"][PROFILE]
        model = str(local["models"]["pi"])
        # A Recipe's reference is its baseline (this machine's digest when the
        # Recipe was made or the model was chosen).  The shipped default has no
        # meaningful baseline: a new Recipe takes the live digest.
        digest = str(local.get("baseline_digest") or "") if exists else ""
        base_url = str(local["base_url"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"path": str(recipe_dir), "exists": exists, "source": source,
                "model": None, "recorded_digest": None, "base_url": None,
                "error": f"model selection unreadable: {error}"}
    return {"path": str(recipe_dir), "exists": exists, "source": source,
            "model": model, "recorded_digest": digest, "base_url": base_url,
            "error": None}


def discover(recipe_dir: Path, *, which=shutil.which, opener=urlopen) -> dict[str, Any]:
    """Return the setup document used by `hwb setup` and the Lab."""
    recipe = recipe_state(recipe_dir)
    base = recipe["base_url"] or "http://127.0.0.1:11434/v1"
    inventory = ollama(base, opener=opener)
    tools = {name: tool(name, which=which) for name in ("pi", "node")}

    blockers: list[dict[str, str]] = []
    notes: list[dict[str, str]] = []
    if recipe["error"]:
        blockers.append({"code": "recipe_unreadable", "message": recipe["error"]})
    if not tools["pi"]["found"]:
        blockers.append({"code": "pi_missing",
                         "message": "Pi coding agent (`pi`) was not found on PATH."})
    if not tools["node"]["found"]:
        blockers.append({"code": "node_missing",
                         "message": "Node.js (`node`) was not found on PATH; Pi needs it."})
    live_digest = None
    if not inventory["reachable"]:
        blockers.append({"code": "ollama_unreachable",
                         "message": f"Ollama did not answer at {inventory['url']}."})
    elif recipe["model"]:
        match = next((m for m in inventory["models"] if m["name"] == recipe["model"]), None)
        if match is None:
            blockers.append({"code": "model_missing",
                             "message": f"Ollama does not have {recipe['model']!r}."})
        else:
            live_digest = match["digest"]
            if recipe["exists"] and not recipe["recorded_digest"]:
                notes.append({"code": "baseline_pending",
                              "message": "The Recipe has no model baseline yet; the next run "
                                         "adopts Ollama's current digest."})
            elif recipe["exists"] and live_digest != recipe["recorded_digest"]:
                notes.append({"code": "model_changed",
                              "message": (f"{recipe['model']!r} changed since the Recipe's "
                                          "baseline. The run will proceed and record the new "
                                          "digest; re-prove before relying on earlier results.")})
    recipe["live_digest"] = live_digest

    return {
        "schema": SCHEMA,
        "check": CHECK,
        "ready": not blockers,
        "blockers": blockers,
        "notes": notes,
        "machine": machine(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "workbench": {"version": __version__},
        "ollama": inventory,
        "tools": tools,
        "recipe": recipe,
        "note": "Discovery predicts readiness; only a run produces evidence.",
    }


def default_recipe_dir() -> Path:
    return Path(os.getcwd()) / ".hwb" / "recipes" / "coding-python-bug-fix"


def print_setup(doc: dict[str, Any]) -> None:
    m = doc["machine"]
    mem = m["memory_bytes"]
    print(f"Setup for Reality Check {doc['check']}")
    print(f"  Machine   {m.get('model') or m['system']} · {m.get('cpu') or m['arch']}"
          + (f" · {mem / 2**30:.0f} GB" if mem else ""))
    print(f"  Python    {doc['python']['version']} · Workbench {doc['workbench']['version']}")
    o = doc["ollama"]
    print(f"  Ollama    {'reachable' if o['reachable'] else 'not reachable'} at {o['url']}"
          + (f" · {len(o['models'])} model(s)" if o["reachable"] else ""))
    for t in doc["tools"].values():
        print(f"  {t['name']:<9} {t['version'] or 'found' if t['found'] else 'not found'}")
    r = doc["recipe"]
    print(f"  Model     {r['model'] or 'unknown'} ({r['source']})")
    print("Ready" if doc["ready"] else "Not ready")
    for b in doc["blockers"]:
        print(f"  - {b['message']}")
    for n in doc.get("notes", []):
        print(f"  note: {n['message']}")
    print(doc["note"])
