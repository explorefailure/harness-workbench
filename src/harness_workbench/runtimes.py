"""Shared runtime store behind `hwb harness list/add/remove`.

One versioned store per user, shared by every workspace:
``$HWB_RUNTIME_STORE`` or ``$XDG_DATA_HOME/hwb/runtimes`` or
``~/.local/share/hwb/runtimes`` (macOS too). Each install lives in
``<store>/<harness>/<version>/`` with a ``install.json`` written last.

Downloads are opt-in, pinned and checksum-verified. The checksums come from the
pins shipped inside this package (the catalog points at them rather than copying
them), so the only trust anchor at install time is the installed Workbench. An
install is assembled under ``<store>/.partial/`` and renamed into place only
after every check passes; nothing is written outside the store and nothing
needs sudo.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional

PACKAGE = Path(__file__).resolve().parent
CATALOG_PATH = PACKAGE / "harness_catalog.json"
CATALOG_SCHEMA = "hwb-harness-catalog/v1"
RECORD_SCHEMA = "hwb-runtime-install/v1"
STORE_ENV = "HWB_RUNTIME_STORE"
ARCHIVE_LIMIT = 134_217_728
MEMBER_LIMIT = 400_000_000


class RuntimeStoreError(Exception):
    """A refused or failed store operation, with a message for the user."""


def store_root(environ: Optional[Dict[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get(STORE_ENV):
        return Path(env[STORE_ENV]).expanduser()
    data = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data).expanduser() / "hwb" / "runtimes"


def platform_key() -> str:
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "arm64": "arm64", "aarch64": "aarch64"}.get(machine, machine)
    system = platform.system().lower()
    if system == "linux" and machine == "arm64":
        machine = "aarch64"
    if system == "darwin" and machine == "aarch64":
        machine = "arm64"
    return system + "-" + machine


def load_catalog(path: Path = CATALOG_PATH) -> Dict[str, Any]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise RuntimeStoreError("unknown harness catalog schema: %r" % catalog.get("schema"))
    return catalog


def catalog_digest(path: Path = CATALOG_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _harness(catalog: Dict[str, Any], name: str) -> Dict[str, Any]:
    harnesses = catalog["harnesses"]
    if name not in harnesses:
        raise RuntimeStoreError("unknown harness %r; known: %s" % (name, ", ".join(sorted(harnesses))))
    return harnesses[name]


def _version(entry: Dict[str, Any], name: str, version: Optional[str]) -> str:
    chosen = version or entry["default"]
    if chosen not in entry["versions"]:
        raise RuntimeStoreError("%s %s is not in the catalog; available: %s"
                                % (name, chosen, ", ".join(sorted(entry["versions"]))))
    return chosen


def _pin(spec: Dict[str, Any]) -> Dict[str, Any]:
    reference = spec["pin"]
    file_part, _, key = reference.partition("#")
    data = json.loads((PACKAGE / file_part).read_text(encoding="utf-8"))
    return data[key] if key else data


def _safe_component(value: str) -> str:
    if not value or value in (".", "..") or "/" in value or "\\" in value or value.startswith("."):
        raise RuntimeStoreError("unsafe store path component: %r" % value)
    return value


def version_dir(root: Path, name: str, version: str) -> Path:
    return root / _safe_component(name) / _safe_component(version)


def read_install_record(directory: Path) -> Optional[Dict[str, Any]]:
    try:
        record = json.loads((directory / "install.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("schema") == RECORD_SCHEMA else None


def installed(root: Path, name: str) -> List[Dict[str, Any]]:
    """Every complete install of `name` in the store (an install record marks complete)."""
    base = root / _safe_component(name)
    found = []
    if base.is_dir():
        for child in sorted(base.iterdir()):
            record = read_install_record(child) if child.is_dir() else None
            if record is not None:
                found.append({"version": child.name, "path": str(child), "install_record": record})
    return found


def bin_dirs(root: Optional[Path] = None, catalog: Optional[Dict[str, Any]] = None) -> List[str]:
    """`bin/` of each harness's default (else newest-named) store install.

    Callers APPEND these to PATH, so a harness already on PATH still wins.
    """
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    dirs = []
    for name, entry in sorted(catalog["harnesses"].items()):
        installs = installed(root, name)
        if not installs:
            continue
        preferred = [i for i in installs if i["version"] == entry["default"]] or installs[-1:]
        candidate = Path(preferred[0]["path"]) / "bin"
        if candidate.is_dir():
            dirs.append(str(candidate))
    return dirs


def _has_installer(entry: Dict[str, Any]) -> bool:
    """Only the `binary` installer exists so far; the others arrive in later steps."""
    return bool(entry.get("installer")) and entry["kind"] == "binary"


def available_here(entry: Dict[str, Any], version: str, key: Optional[str] = None) -> bool:
    if not _has_installer(entry):
        return False
    return (key or platform_key()) in _pin(entry["versions"][version])["platforms"]


def describe(root: Optional[Path] = None, catalog: Optional[Dict[str, Any]] = None,
             which: Callable[[str], Optional[str]] = shutil.which) -> List[Dict[str, Any]]:
    """One row per catalog harness for `hwb harness list` / setup / the Lab."""
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    rows = []
    for name, entry in sorted(catalog["harnesses"].items()):
        default = entry["default"]
        rows.append({
            "harness": name,
            "display": entry.get("display", name),
            "kind": entry["kind"],
            "default_version": default,
            "versions": sorted(entry["versions"]),
            "installed": [{"version": i["version"], "path": i["path"]} for i in installed(root, name)],
            "on_path": which(entry["executable"]),
            "installable_here": available_here(entry, default),
            "sign_in": entry.get("sign_in"),
            "modes": entry.get("modes", {}),
        })
    return rows


def plan(name: str, version: Optional[str] = None, root: Optional[Path] = None,
         catalog: Optional[Dict[str, Any]] = None, key: Optional[str] = None) -> Dict[str, Any]:
    """What `add` would download and where, without touching the network."""
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    entry = _harness(catalog, name)
    chosen = _version(entry, name, version)
    destination = version_dir(root, name, chosen)
    key = key or platform_key()
    result = {"harness": name, "version": chosen, "kind": entry["kind"], "platform": key,
              "destination": str(destination), "already_installed": read_install_record(destination) is not None,
              "downloads": []}
    if not _has_installer(entry):
        raise RuntimeStoreError(
            "%s cannot be installed by Workbench yet (%s runtime); install it yourself and "
            "Workbench will find it on PATH" % (name, entry["kind"]))
    if entry["kind"] == "binary":
        pin = _pin(entry["versions"][chosen])
        if key not in pin["platforms"]:
            raise RuntimeStoreError("%s %s has no pinned build for %s (pinned: %s)"
                                    % (name, chosen, key, ", ".join(sorted(pin["platforms"]))))
        asset = pin["platforms"][key]
        result["downloads"].append({"url": asset["url"], "sha256": asset["archive_sha256"]})
    return result


def _default_fetch(url: str) -> bytes:
    from .opencode_builder.install_runtime import download
    return download(url)


def _extract_member(raw: bytes, url: str, member_name: str) -> bytes:
    """Read exactly one regular file from an archive, bounded, no extraction to disk."""
    if PurePosixPath(member_name).is_absolute() or ".." in PurePosixPath(member_name).parts:
        raise RuntimeStoreError("unsafe archive member name: %r" % member_name)
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo(member_name)
            if info.is_dir() or info.file_size > MEMBER_LIMIT:
                raise RuntimeStoreError("archive member is not a bounded regular file")
            return archive.read(info)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        info = archive.getmember(member_name)
        if not info.isfile() or info.size > MEMBER_LIMIT:
            raise RuntimeStoreError("archive member is not a bounded regular file")
        stream = archive.extractfile(info)
        if stream is None:
            raise RuntimeStoreError("archive member could not be read")
        return stream.read()


def add(name: str, version: Optional[str] = None, root: Optional[Path] = None,
        catalog: Optional[Dict[str, Any]] = None, fetch: Callable[[str], bytes] = _default_fetch,
        key: Optional[str] = None) -> Dict[str, Any]:
    """Install one pinned runtime into the store. Idempotent for a complete install."""
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    steps = plan(name, version, root, catalog, key)
    destination = Path(steps["destination"])
    if steps["already_installed"]:
        return {"installed": False, "path": str(destination), "install_record": read_install_record(destination)}
    if destination.exists() or destination.is_symlink():
        raise RuntimeStoreError("%s exists without a valid install record (an interrupted or foreign "
                                "install); remove it and try again" % destination)
    entry = _harness(catalog, name)
    spec = entry["versions"][steps["version"]]
    pin = _pin(spec)
    asset = pin["platforms"][steps["platform"]]
    raw = fetch(asset["url"])
    if len(raw) > ARCHIVE_LIMIT or hashlib.sha256(raw).hexdigest() != asset["archive_sha256"]:
        raise RuntimeStoreError("downloaded archive does not match the pinned SHA-256; nothing installed")
    binary = _extract_member(raw, asset["url"], spec["member"])
    binary_sha = hashlib.sha256(binary).hexdigest()
    if binary_sha != asset["binary_sha256"]:
        raise RuntimeStoreError("extracted binary does not match the pinned SHA-256; nothing installed")

    partial_root = root / ".partial"
    partial_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=name + "-", dir=partial_root))
    try:
        (staging / "bin").mkdir(mode=0o755)
        target = staging / "bin" / entry["executable"]
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
        from . import __version__
        record = {
            "schema": RECORD_SCHEMA, "harness": name, "version": steps["version"],
            "kind": entry["kind"], "platform": steps["platform"],
            "catalog_sha256": catalog_digest(),
            "downloads": [{"url": asset["url"], "sha256": asset["archive_sha256"], "bytes": len(raw)}],
            "files": {"bin/" + entry["executable"]: binary_sha},
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "workbench_version": __version__,
        }
        (staging / "install.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            destination.parent.rmdir()  # only if this attempt left it empty
        except OSError:
            pass
        raise
    return {"installed": True, "path": str(destination), "install_record": record}


def remove(name: str, version: Optional[str] = None, root: Optional[Path] = None,
           catalog: Optional[Dict[str, Any]] = None) -> List[str]:
    """Delete one installed version (default: every installed version) of `name`."""
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    _harness(catalog, name)
    targets = [version_dir(root, name, version)] if version else \
        [Path(i["path"]) for i in installed(root, name)]
    resolved_root = root.resolve()
    removed = []
    for target in targets:
        if not target.exists():
            raise RuntimeStoreError("%s %s is not installed in %s" % (name, version, root))
        if target.is_symlink() or resolved_root not in target.resolve().parents:
            raise RuntimeStoreError("refusing to remove %s: not a directory inside the store" % target)
        shutil.rmtree(target)
        removed.append(str(target))
    return removed
