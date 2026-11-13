"""Read-only local setup discovery for the packaged Reality Check.

`hwb setup` and the Lab's Discover screen answer one question: can this
machine attempt `coding/python-bug-fix` right now, and if not, what is
missing?  Discovery never writes, installs, pulls, or starts anything.

It deliberately probes the same things a real run will use -- `pi` and `node`
on PATH, the model runtime the chosen profile declares (Ollama for
`local-ollama`, llama-server for `local-llamacpp`), and the digest rule the
adapter enforces -- so "ready" predicts what the run will
check instead of describing the machine in general.  It is still a prediction:
the run itself remains the only evidence.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import urlopen

from . import __version__, reality

SCHEMA = "hwb-setup/v0.1"
CHECK = "coding/python-bug-fix"
PROFILE = reality.DEFAULT_RECIPE_PROFILE
PROFILES = reality.RECIPE_PROFILES
_CONTENT_ADDRESSED_GGUF = reality._CONTENT_ADDRESSED_GGUF
_PROBE_TIMEOUT = 5


def _shipped_selection() -> dict[str, Any]:
    return reality._available_selection()


def _tags_url(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/v1"):
        endpoint = endpoint[:-3]
    return endpoint + "/api/tags"


def _endpoint(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    return endpoint[:-3] if endpoint.endswith("/v1") else endpoint


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


def llamacpp(base_url: str, opener=urlopen) -> dict[str, Any]:
    """Read the one model a llama-server serves, from its /props endpoint.

    llama.cpp has no digest of its own: a model counts as identified only when
    the served GGUF is named by its SHA-256, the rule the adapter enforces.
    """
    url = _endpoint(base_url) + "/props"
    try:
        with opener(url, timeout=_PROBE_TIMEOUT) as response:
            payload = json.load(response)
    except (URLError, OSError, ValueError) as error:
        return {"reachable": False, "url": url, "models": [], "build": None,
                "error": str(error)}
    if not isinstance(payload, dict):
        return {"reachable": False, "url": url, "models": [], "build": None,
                "error": "llama-server returned an unexpected /props document"}
    name = str(payload.get("model_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    match = _CONTENT_ADDRESSED_GGUF.fullmatch(name)
    alias = payload.get("model_alias")
    build = payload.get("build_info")
    models = [{
        "name": alias,
        "digest": match.group(1) if match else "",
        "size_bytes": None,
    }] if isinstance(alias, str) and alias else []
    return {"reachable": True, "url": url, "models": models,
            "build": build if isinstance(build, str) and build else None, "error": None}


LLAMACPP_DEFAULT_URL = "http://127.0.0.1:8081/v1"
_TOKENS_PREDICTED = "llamacpp:tokens_predicted_total"
_SLOTS_BODY_LIMIT = 1 << 20


def llamacpp_tokens_predicted(base_url: str, opener=urlopen) -> dict[str, Any]:
    """Read llama-server's running count of generated tokens from /metrics.

    llama-server serves /metrics only when started with --metrics. The count is
    the server's total across every client, so it shows that the model is
    working, not what an agent is doing. Only that one number is read; nothing
    else from the response is returned.
    """
    url = _endpoint(base_url) + "/metrics"
    try:
        with opener(url, timeout=_PROBE_TIMEOUT) as response:
            text = response.read(1 << 20).decode("utf-8", "replace")
    except (URLError, OSError, ValueError) as error:
        reason = ("llama-server serves /metrics only when started with --metrics"
                  if getattr(error, "code", None) in (404, 501) else str(error))
        return {"available": False, "url": url, "tokens": None, "reason": reason}
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name == _TOKENS_PREDICTED:
            try:
                tokens = float(value.split()[0])
            except (ValueError, IndexError):
                break
            if tokens >= 0 and tokens == tokens and tokens != float("inf"):
                return {"available": True, "url": url, "tokens": int(tokens), "reason": None}
            break
    return {"available": False, "url": url, "tokens": None,
            "reason": f"/metrics has no usable {_TOKENS_PREDICTED} counter"}


def llamacpp_slots(base_url: str, opener=urlopen) -> dict[str, Any]:
    """Read only task progress numbers from llama-server's /slots endpoint."""
    url = _endpoint(base_url) + "/slots"
    unavailable = {"available": False, "url": url, "slots": []}
    try:
        with opener(url, timeout=_PROBE_TIMEOUT) as response:
            body = response.read(_SLOTS_BODY_LIMIT + 1)
    except (URLError, OSError, ValueError) as error:
        off = (getattr(error, "code", None) in (404, 501)
               or "does not support slots endpoint" in str(error).lower())
        reason = ("llama-server /slots is off when started with --no-slots"
                  if off else str(error))
        return {**unavailable, "reason": reason}
    if len(body) > _SLOTS_BODY_LIMIT:
        return {**unavailable, "reason": "/slots response exceeds 1 MiB limit"}
    text = body.decode("utf-8", "replace")
    if "does not support slots endpoint" in text.lower():
        return {**unavailable,
                "reason": "llama-server /slots is off when started with --no-slots"}
    try:
        payload = json.loads(text, strict=False)
    except (ValueError, TypeError):
        return {**unavailable, "reason": "llama-server returned malformed /slots JSON"}
    if not isinstance(payload, list):
        return {**unavailable, "reason": "llama-server returned unexpected /slots document"}

    slots = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("is_processing"), bool):
            return {**unavailable, "reason": "/slots contains a malformed slot"}
        task = item.get("id_task")
        if task is not None and (isinstance(task, bool) or not isinstance(task, int)
                                 or task < 0):
            return {**unavailable, "reason": "/slots contains a malformed task id"}
        decoded = None
        if task is not None:
            next_token = item.get("next_token")
            if not isinstance(next_token, list) or not next_token \
                    or not isinstance(next_token[0], dict):
                return {**unavailable, "reason": "/slots contains malformed token progress"}
            decoded = next_token[0].get("n_decoded")
            if (isinstance(decoded, bool) or not isinstance(decoded, int)
                    or decoded < 0):
                return {**unavailable, "reason": "/slots contains malformed token progress"}
        slots.append({"task": task, "processing": item["is_processing"],
                      "decoded": decoded})
    return {"available": True, "url": url, "slots": slots, "reason": None}


def runtime(state: dict[str, Any], opener=urlopen) -> dict[str, Any]:
    """The runtime block for a Recipe state: one shape for Ollama and llama.cpp."""
    kind = state.get("runtime") or "ollama"
    if kind == "llama.cpp":
        block = llamacpp(state["base_url"] or LLAMACPP_DEFAULT_URL, opener=opener)
        label = "llama.cpp"
    else:
        block = ollama(state["base_url"] or "http://127.0.0.1:11434/v1", opener=opener)
        block["build"] = None
        label = "Ollama"
    return {"kind": kind, "label": label, **block}


def recipe_state(recipe_dir: Path, profile: str = PROFILE) -> dict[str, Any]:
    """The model a run would use: the Recipe's, or the shipped default.

    An existing Recipe keeps the profile it was made with; `profile` only
    chooses which shipped profile a new Recipe would start from.
    """
    selection_path = recipe_dir / "model_selection.json"
    exists = (recipe_dir / "recipe.json").is_file()
    source = "recipe" if exists else "shipped default"
    name = profile
    try:
        if exists:
            reality._validate_recipe(recipe_dir.resolve())
        selection = (json.loads(selection_path.read_text(encoding="utf-8"))
                     if exists else _shipped_selection())
        name = str(selection["active"]) if exists else profile
        local = selection["profiles"][name]
        model = str(local["models"]["pi"])
        # A Recipe's reference is its baseline (this machine's digest when the
        # Recipe was made or the model was chosen).  The shipped default has no
        # meaningful baseline: a new Recipe takes the live digest.
        digest = str(local.get("baseline_digest") or "") if exists else ""
        build = str(local.get("baseline_runtime_build") or "") if exists else ""
        base_url = str(local["base_url"])
        kind = "llama.cpp" if local.get("runtime") == "llama.cpp" else "ollama"
    except (OSError, ValueError, KeyError, TypeError, reality.RealityError) as error:
        return {"path": str(recipe_dir), "exists": exists, "source": source,
                "profile": name, "runtime": None,
                "model": None, "recorded_digest": None, "recorded_build": None,
                "base_url": None, "error": f"Recipe unreadable: {error}"}
    return {"path": str(recipe_dir), "exists": exists, "source": source,
            "profile": name, "runtime": kind,
            "model": model, "recorded_digest": digest, "recorded_build": build,
            "base_url": base_url, "error": None}


def discover(recipe_dir: Path, *, profile: str = PROFILE, which=shutil.which,
             opener=urlopen) -> dict[str, Any]:
    """Return the setup document used by `hwb setup` and the Lab."""
    recipe = recipe_state(recipe_dir, profile)
    inventory = runtime(recipe, opener=opener)
    label = inventory["label"]
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
    llama = inventory["kind"] == "llama.cpp"
    if not inventory["reachable"]:
        blockers.append({"code": "llamacpp_unreachable" if llama else "ollama_unreachable",
                         "message": f"{label} did not answer at {inventory['url']}."})
    elif recipe["model"]:
        match = next((m for m in inventory["models"] if m["name"] == recipe["model"]), None)
        if match is None:
            served = ", ".join(str(m["name"]) for m in inventory["models"]) or "nothing"
            blockers.append({"code": "model_missing", "message": (
                f"llama-server serves {served}, not {recipe['model']!r}; start it with "
                f"--alias {recipe['model']}." if llama
                else f"Ollama does not have {recipe['model']!r}.")})
        elif llama and not match["digest"]:
            blockers.append({"code": "model_not_content_addressed", "message": (
                "llama-server's model file is not named by its SHA-256, so the run "
                "cannot identify it; serve a GGUF named sha256-<digest> or <digest>.gguf.")})
        else:
            live_digest = match["digest"]
            if recipe["exists"] and not recipe["recorded_digest"]:
                notes.append({"code": "baseline_pending",
                              "message": "The Recipe has no model baseline yet; the next run "
                                         f"adopts {label}'s current digest."})
            elif recipe["exists"] and live_digest != recipe["recorded_digest"]:
                notes.append({"code": "model_changed",
                              "message": (f"{recipe['model']!r} changed since the Recipe's "
                                          "baseline. The run will proceed and record the new "
                                          "digest; re-prove before relying on earlier results.")})
            if (llama and recipe["exists"] and recipe["recorded_build"]
                    and inventory["build"] and inventory["build"] != recipe["recorded_build"]):
                notes.append({"code": "build_changed", "message": (
                    f"llama.cpp is now {inventory['build']}; the Recipe's baseline is "
                    f"{recipe['recorded_build']}. The run will proceed and record it.")})
    recipe["live_digest"] = live_digest
    recipe["live_build"] = inventory["build"]

    return {
        "schema": SCHEMA,
        "check": CHECK,
        "ready": not blockers,
        "blockers": blockers,
        "notes": notes,
        "machine": machine(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "workbench": {"version": __version__},
        "runtime": inventory,
        # Kept for clients of the first setup schema; same block for Ollama.
        "ollama": inventory if not llama else None,
        "tools": tools,
        "recipe": recipe,
        "note": "Discovery predicts readiness; only a run produces evidence.",
    }


def default_recipe_dir(profile: str = PROFILE) -> Path:
    return reality._default_recipe(profile)


def print_setup(doc: dict[str, Any]) -> None:
    m = doc["machine"]
    mem = m["memory_bytes"]
    print(f"Setup for Reality Check {doc['check']}")
    print(f"  Machine   {m.get('model') or m['system']} · {m.get('cpu') or m['arch']}"
          + (f" · {mem / 2**30:.0f} GB" if mem else ""))
    print(f"  Python    {doc['python']['version']} · Workbench {doc['workbench']['version']}")
    o = doc["runtime"]
    print(f"  {o['label']:<9} {'reachable' if o['reachable'] else 'not reachable'} at {o['url']}"
          + (f" · {len(o['models'])} model(s)" if o["reachable"] else "")
          + (f" · build {o['build']}" if o.get("build") else ""))
    for t in doc["tools"].values():
        print(f"  {t['name']:<9} {t['version'] or 'found' if t['found'] else 'not found'}")
    r = doc["recipe"]
    print(f"  Model     {r['model'] or 'unknown'} ({r['source']}, {r.get('profile')})")
    print("Ready" if doc["ready"] else "Not ready")
    for b in doc["blockers"]:
        print(f"  - {b['message']}")
    for n in doc.get("notes", []):
        print(f"  note: {n['message']}")
    print(doc["note"])
