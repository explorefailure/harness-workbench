"""Named model endpoints shared by the builders (`hwb model add/list/remove`).

One small file, `$XDG_CONFIG_HOME/hwb/models.json` (default
`~/.config/hwb/models.json`), maps a name to an OpenAI-compatible endpoint, a
model ID and, optionally, the NAME of the environment variable that holds its
key. A key value is never stored: the builders read the variable at run time,
the same rule their provider configs already enforce.

`provider_for(name, builder)` translates an entry into the provider block a
builder's own config validates (Pi, OpenCode or Hermes); nothing here talks to
a model.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

SCHEMA = "hwb-models/v1"
CONFIG_ENV = "XDG_CONFIG_HOME"
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BUILDERS = ("pi", "opencode", "hermes")


class ModelError(ValueError):
    """A refused model definition or lookup, with a message for the user."""


def path(environ: Optional[Dict[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    base = env.get(CONFIG_ENV) or str(Path.home() / ".config")
    return Path(base).expanduser() / "hwb" / "models.json"


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ModelError("model name must be 1-64 lowercase letters, digits, '.', '_' or '-' "
                         "and start with a letter or digit: %r" % (name,))
    return name


def validate_entry(entry: Any) -> Dict[str, str]:
    if not isinstance(entry, dict) or set(entry) - {"base_url", "model", "api_key_env"}:
        raise ModelError("a model entry has base_url, model and optionally api_key_env")
    url = entry.get("base_url")
    parts = urlsplit(url) if isinstance(url, str) else None
    if (parts is None or parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or any(c.isspace() for c in url)):
        raise ModelError("--url must be an http(s) URL without credentials, query or fragment")
    model = entry.get("model")
    if not isinstance(model, str) or not model or model.startswith("-") or any(c.isspace() for c in model):
        raise ModelError("--model must be a nonempty model ID without whitespace")
    if "api_key_env" in entry:
        name = entry["api_key_env"]
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ModelError("--api-key-env takes the NAME of an environment variable "
                             "(for example MY_MODEL_KEY), never the key itself")
    return entry


def load(environ: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, str]]:
    """Saved models by name; an absent file is an empty set, a broken one is refused."""
    file = path(environ)
    try:
        raw = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ModelError("could not read %s: %s" % (file, error)) from error
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ModelError("%s is not valid JSON: %s" % (file, error)) from error
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("models"), dict):
        raise ModelError("%s is not a %s file" % (file, SCHEMA))
    for name, entry in data["models"].items():
        _check_name(name)
        validate_entry(entry)
    return data["models"]


def _save(models: Dict[str, Dict[str, str]], environ: Optional[Dict[str, str]] = None) -> Path:
    file = path(environ)
    file.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"schema": SCHEMA, "models": models}, indent=2, sort_keys=True) + "\n"
    fd, temp = tempfile.mkstemp(prefix=".models-", suffix=".json", dir=str(file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp, 0o600)
        os.replace(temp, file)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return file


def add(name: str, base_url: str, model: str, api_key_env: Optional[str] = None,
        environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Save (or replace) a named model. Returns {"name", "entry", "replaced", "path"}."""
    _check_name(name)
    entry = {"base_url": base_url, "model": model}
    if api_key_env is not None:
        entry["api_key_env"] = api_key_env
    validate_entry(entry)
    models = load(environ)
    replaced = name in models
    models[name] = entry
    file = _save(models, environ)
    return {"name": name, "entry": entry, "replaced": replaced, "path": str(file)}


def remove(name: str, environ: Optional[Dict[str, str]] = None) -> Path:
    models = load(environ)
    if name not in models:
        raise ModelError("no saved model named %r; see `hwb model list`" % (name,))
    del models[name]
    return _save(models, environ)


def get(name: str, environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    models = load(environ)
    if name not in models:
        known = ", ".join(sorted(models)) or "none saved yet"
        raise ModelError("no saved model named %r (%s); add it with "
                         "`hwb model add %s --url URL --model ID`" % (name, known, name))
    return dict(models[name])


def describe(environ: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Rows for `hwb model list`; says whether each key variable is set, never its value."""
    env = os.environ if environ is None else environ
    rows = []
    for name, entry in sorted(load(environ).items()):
        key = entry.get("api_key_env")
        rows.append({"name": name, **entry,
                     "api_key_set": None if key is None else bool(env.get(key, "").strip())})
    return rows


def provider_for(name: str, builder: str, environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The provider block `builder`'s own config validates, for saved model `name`.

    OpenCode and Hermes: mode `local` for an endpoint without a key, else
    `compatible` with the key's variable name. Pi has no local mode: always
    `compatible`, and without a key it sends a fixed placeholder.
    """
    if builder not in BUILDERS:
        raise ModelError("unknown builder %r" % (builder,))
    entry = get(name, environ)
    provider: Dict[str, Any] = {"base_url": entry["base_url"], "model": entry["model"]}
    if builder == "pi":
        provider["mode"] = "compatible"
    else:
        provider["mode"] = "compatible" if "api_key_env" in entry else "local"
    if "api_key_env" in entry:
        provider["api_key_env"] = entry["api_key_env"]
    return provider
