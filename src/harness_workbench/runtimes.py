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
import subprocess
import sys
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
EXTRACT_LIMIT = 1_073_741_824


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


def _entries(catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Harnesses and their prerequisites (Node, uv) share one namespace in the store."""
    merged = dict(catalog.get("prerequisites", {}))
    merged.update(catalog["harnesses"])
    return merged


def _harness(catalog: Dict[str, Any], name: str) -> Dict[str, Any]:
    entries = _entries(catalog)
    if name not in entries:
        raise RuntimeStoreError("unknown harness %r; known: %s" % (name, ", ".join(sorted(entries))))
    return entries[name]


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
    if key:
        data = data["versions"][key] if "versions" in data and key in data["versions"] else data[key]
    return data


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
    """`bin/` of each harness's (and prerequisite's) default, else newest-named, store install.

    Callers APPEND these to PATH, so anything already on PATH still wins.
    """
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    dirs = []
    for name, entry in sorted(_entries(catalog).items()):
        installs = installed(root, name)
        if not installs:
            continue
        preferred = [i for i in installs if i["version"] == entry["default"]] or installs[-1:]
        candidate = Path(preferred[0]["path"]) / "bin"
        if candidate.is_dir():
            dirs.append(str(candidate))
    return dirs


INSTALLER_KINDS = ("binary", "tree", "npm", "uv-source")


def _has_installer(entry: Dict[str, Any]) -> bool:
    return bool(entry.get("installer")) and entry["kind"] in INSTALLER_KINDS


def available_here(entry: Dict[str, Any], version: str, key: Optional[str] = None,
                   catalog: Optional[Dict[str, Any]] = None) -> bool:
    if not _has_installer(entry):
        return False
    key = key or platform_key()
    spec = entry["versions"][version]
    if entry["kind"] in ("npm", "uv-source"):
        catalog = load_catalog() if catalog is None else catalog
        tool = "node" if entry["kind"] == "npm" else "uv"
        return available_here(catalog["prerequisites"][tool], spec[tool], key, catalog)
    return key in _pin(spec)["platforms"]


def _row(name: str, entry: Dict[str, Any], root: Path, catalog: Dict[str, Any],
         which: Callable[[str], Optional[str]]) -> Dict[str, Any]:
    default = entry["default"]
    return {
        "harness": name,
        "display": entry.get("display", name),
        "kind": entry["kind"],
        "default_version": default,
        "versions": sorted(entry["versions"]),
        "installed": [{"version": i["version"], "path": i["path"]} for i in installed(root, name)],
        "on_path": which(entry["executable"]),
        "installable_here": available_here(entry, default, catalog=catalog),
        "sign_in": entry.get("sign_in"),
        "modes": entry.get("modes", {}),
    }


def describe(root: Optional[Path] = None, catalog: Optional[Dict[str, Any]] = None,
             which: Callable[[str], Optional[str]] = shutil.which) -> List[Dict[str, Any]]:
    """One row per catalog harness for `hwb harness list` / setup / the Lab."""
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    return [_row(n, e, root, catalog, which) for n, e in sorted(catalog["harnesses"].items())]


def describe_prerequisites(root: Optional[Path] = None, catalog: Optional[Dict[str, Any]] = None,
                           which: Callable[[str], Optional[str]] = shutil.which) -> List[Dict[str, Any]]:
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    return [_row(n, e, root, catalog, which)
            for n, e in sorted(catalog.get("prerequisites", {}).items())]


def tool_version_on_path(tool: str, which: Callable[[str], Optional[str]] = shutil.which
                         ) -> Optional[Dict[str, str]]:
    """`node --version` -> v22.22.3; `uv --version` -> uv 0.12.21 (...)."""
    path = which(tool)
    if not path:
        return None
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    words = done.stdout.split()
    if done.returncode != 0 or not words:
        return None
    version = (words[1] if tool == "uv" and len(words) > 1 else words[0]).lstrip("v")
    return {"path": path, "version": version}


def node_version_on_path(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[Dict[str, str]]:
    return tool_version_on_path("node", which)


def _tool_for(tool: str, wanted: str, root: Path, on_path: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """The exact pinned version on PATH, else the store's copy of it."""
    if on_path and on_path["version"] == wanted:
        return {"version": wanted, "source": "path", "bin": str(Path(on_path["path"]).parent)}
    stored = version_dir(root, tool, wanted)
    return {"version": wanted, "source": "store", "bin": str(stored / "bin"),
            "installed": read_install_record(stored) is not None}


def _node_for(spec: Dict[str, Any], root: Path,
              path_node: Optional[Dict[str, str]]) -> Dict[str, Any]:
    return _tool_for("node", spec["node"], root, path_node)


def plan(name: str, version: Optional[str] = None, root: Optional[Path] = None,
         catalog: Optional[Dict[str, Any]] = None, key: Optional[str] = None,
         path_node: Any = "probe") -> Dict[str, Any]:
    """What `add` would download and where. Never touches the network."""
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    entry = _harness(catalog, name)
    chosen = _version(entry, name, version)
    destination = version_dir(root, name, chosen)
    key = key or platform_key()
    result = {"harness": name, "version": chosen, "kind": entry["kind"], "platform": key,
              "destination": str(destination),
              "already_installed": read_install_record(destination) is not None,
              "downloads": [], "prerequisites": []}
    if not _has_installer(entry):
        raise RuntimeStoreError(
            "%s cannot be installed by Workbench yet (%s runtime); install it yourself and "
            "Workbench will find it on PATH" % (name, entry["kind"]))
    spec = entry["versions"][chosen]
    if entry["kind"] in ("binary", "tree"):
        pin = _pin(spec)
        if key not in pin["platforms"]:
            raise RuntimeStoreError("%s %s has no pinned build for %s (pinned: %s)"
                                    % (name, chosen, key, ", ".join(sorted(pin["platforms"]))))
        asset = pin["platforms"][key]
        result["downloads"].append({"url": asset["url"], "sha256": asset["archive_sha256"]})
    elif entry["kind"] == "uv-source":
        uv = _tool_for("uv", spec["uv"], root, tool_version_on_path("uv"))
        result["uv"] = uv
        if uv["source"] == "store" and not uv["installed"] and not result["already_installed"]:
            prerequisite = plan("uv", uv["version"], root, catalog, key)
            result["prerequisites"].append(prerequisite)
            result["downloads"].extend(prerequisite["downloads"])
        pin = _pin(spec)
        result["downloads"].append({"url": "git clone --depth 1 --branch %s %s"
                                           % (pin["release_tag"], spec["repository"]),
                                    "sha256": "commit %s, tag object %s, uv.lock %s"
                                              % (pin["source_commit"][:12], pin["tag_object"][:12],
                                                 pin["uv_lock_sha256"][:12])})
        result["downloads"].append({"url": "uv sync --frozen: Python + packages from uv.lock",
                                    "sha256": "per-package hashes in uv.lock"})
    else:  # npm: the lockfile's registry downloads, each checked by npm against its integrity
        if path_node == "probe":
            path_node = node_version_on_path()
        node = _node_for(spec, root, path_node)
        result["node"] = node
        if node["source"] == "store" and not node["installed"] and not result["already_installed"]:
            prerequisite = plan("node", node["version"], root, catalog, key)
            result["prerequisites"].append(prerequisite)
            result["downloads"].extend(prerequisite["downloads"])
        lock = json.loads((PACKAGE / spec["lock_dir"] / "package-lock.json").read_text(encoding="utf-8"))
        packages = [p for p in lock.get("packages", {}) if p]
        result["downloads"].append({"url": "npm registry: %d packages from %s/package-lock.json"
                                           % (len(packages), spec["lock_dir"]),
                                    "sha256": "per-package sha512 integrity in the lockfile"})
    return result


def _default_fetch(url: str) -> bytes:
    from .opencode_builder.install_runtime import download
    return download(url)


def _fetch_verified(asset: Dict[str, Any], fetch: Callable[[str], bytes]) -> bytes:
    raw = fetch(asset["url"])
    if len(raw) > ARCHIVE_LIMIT or hashlib.sha256(raw).hexdigest() != asset["archive_sha256"]:
        raise RuntimeStoreError("downloaded archive does not match the pinned SHA-256; nothing installed")
    return raw


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


def _extract_tree(raw: bytes, strip: str, target: Path) -> int:
    """Unpack a .tar.gz whose members all sit under `strip/` into `target`.

    Regular files, directories and symlinks only. A member name may not be
    absolute or contain `..`; a symlink's target must resolve inside `target`.
    Modes are reduced to 0644/0755. Returns the number of regular files.
    """
    files = 0
    total = 0
    links = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for info in archive.getmembers():
            name = PurePosixPath(info.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeStoreError("unsafe archive member name: %r" % info.name)
            if not name.parts or name.parts[0] != strip:
                raise RuntimeStoreError("archive member outside %s/: %r" % (strip, info.name))
            relative = PurePosixPath(*name.parts[1:])
            if not relative.parts:
                continue
            path = target.joinpath(*relative.parts)
            if info.isdir():
                path.mkdir(mode=0o755, parents=True, exist_ok=True)
            elif info.isfile():
                total += info.size
                if info.size > MEMBER_LIMIT or total > EXTRACT_LIMIT:
                    raise RuntimeStoreError("archive exceeds the extraction size bound")
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                stream = archive.extractfile(info)
                if stream is None:
                    raise RuntimeStoreError("archive member could not be read: %r" % info.name)
                mode = 0o755 if info.mode & 0o111 else 0o644
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(stream, out)
                files += 1
            elif info.issym():
                links.append((path, info.linkname, relative))
            else:
                raise RuntimeStoreError("archive member is not a file, directory or symlink: %r" % info.name)
    resolved_target = target.resolve()
    for path, linkname, relative in links:
        if PurePosixPath(linkname).is_absolute():
            raise RuntimeStoreError("absolute symlink in archive: %s -> %s" % (relative, linkname))
        landing = (path.parent / linkname).resolve()
        if landing != resolved_target and resolved_target not in landing.parents:
            raise RuntimeStoreError("symlink escapes the install: %s -> %s" % (relative, linkname))
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.symlink(linkname, path)
    return files


def _tree_digest(root: Path) -> str:
    """Path + content of every regular file, and path + target of every link, under root."""
    parts = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            parts.append("L %s %s" % (relative, os.readlink(path)))
        elif path.is_file():
            parts.append("F %s %s" % (relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _commit(root: Path, name: str, version: str, populate: Callable[[Path], Dict[str, Any]],
            record_base: Dict[str, Any]) -> Dict[str, Any]:
    """Build the install in .partial/, write install.json last, rename into place."""
    destination = version_dir(root, name, version)
    partial_root = root / ".partial"
    partial_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=name + "-", dir=partial_root))
    try:
        extra = populate(staging)
        from . import __version__
        record = dict(record_base)
        record.update(extra)
        record.update({
            "schema": RECORD_SCHEMA, "harness": name, "version": version,
            "catalog_sha256": catalog_digest(),
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "workbench_version": __version__,
        })
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


def _install_binary(name, entry, spec, steps, root, fetch):
    asset = _pin(spec)["platforms"][steps["platform"]]
    raw = _fetch_verified(asset, fetch)
    binary = _extract_member(raw, asset["url"], spec["member"])
    binary_sha = hashlib.sha256(binary).hexdigest()
    if binary_sha != asset["binary_sha256"]:
        raise RuntimeStoreError("extracted binary does not match the pinned SHA-256; nothing installed")

    def populate(staging: Path) -> Dict[str, Any]:
        (staging / "bin").mkdir(mode=0o755)
        fd = os.open(staging / "bin" / entry["executable"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        with os.fdopen(fd, "wb") as stream:
            stream.write(binary)
        return {"files": {"bin/" + entry["executable"]: binary_sha}}

    base = {"kind": "binary", "platform": steps["platform"],
            "downloads": [{"url": asset["url"], "sha256": asset["archive_sha256"], "bytes": len(raw)}]}
    return _commit(root, name, steps["version"], populate, base)


def _install_tree(name, entry, spec, steps, root, fetch):
    asset = _pin(spec)["platforms"][steps["platform"]]
    raw = _fetch_verified(asset, fetch)

    def populate(staging: Path) -> Dict[str, Any]:
        into = staging / asset["into"] if asset.get("into") else staging
        into.mkdir(mode=0o755, parents=True, exist_ok=True)
        count = _extract_tree(raw, asset["strip"], into)
        executable = staging / "bin" / entry["executable"]
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeStoreError("%s archive has no executable bin/%s" % (name, entry["executable"]))
        return {"files_count": count, "tree_sha256": _tree_digest(staging)}

    base = {"kind": "tree", "platform": steps["platform"],
            "downloads": [{"url": asset["url"], "sha256": asset["archive_sha256"], "bytes": len(raw)}]}
    return _commit(root, name, steps["version"], populate, base)


def _install_npm(name, entry, spec, steps, root, catalog, fetch, key):
    node = steps["node"]
    prerequisites = []
    if node["source"] == "store" and not read_install_record(version_dir(root, "node", node["version"])):
        prerequisites.append(add("node", node["version"], root=root, catalog=catalog, fetch=fetch, key=key))
    node_bin = Path(node["bin"])
    npm = node_bin / "npm"
    if not npm.exists():
        raise RuntimeStoreError("npm not found next to node in %s" % node_bin)
    lock_dir = PACKAGE / spec["lock_dir"]
    pin = _pin(spec)

    def populate(staging: Path) -> Dict[str, Any]:
        for filename in ("package.json", "package-lock.json"):
            shutil.copyfile(lock_dir / filename, staging / filename)
        env = dict(os.environ)
        env["PATH"] = str(node_bin) + os.pathsep + env.get("PATH", "")
        env["npm_config_cache"] = str(root / ".cache" / "npm")
        env["npm_config_update_notifier"] = "false"
        done = subprocess.run([str(npm), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                              cwd=staging, env=env, capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            raise RuntimeStoreError("npm ci failed (exit %d): %s" % (done.returncode, done.stderr[-2000:]))
        launcher = staging / "node_modules" / ".bin" / entry["executable"]
        identity = _verify_npm_harness(name, launcher, pin)
        (staging / "bin").mkdir(mode=0o755)
        os.symlink(os.path.join("..", "node_modules", ".bin", entry["executable"]),
                   staging / "bin" / entry["executable"])
        identity.pop("package_root", None)
        return {"identity": identity}

    base = {"kind": "npm", "platform": steps["platform"],
            "node": {k: node[k] for k in ("version", "source", "bin")},
            "lockfile_sha256": hashlib.sha256((lock_dir / "package-lock.json").read_bytes()).hexdigest(),
            "prerequisites_installed": [p["path"] for p in prerequisites if p["installed"]]}
    return _commit(root, name, steps["version"], populate, base)


_PI_VERIFY = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from adapter import AdapterError, verify_pi_install
try:
    print(json.dumps(verify_pi_install(Path(sys.argv[2]), json.loads(sys.argv[3]))))
except (AdapterError, OSError, ValueError, KeyError) as error:
    print(str(error), file=sys.stderr)
    sys.exit(3)
"""


def _verify_npm_harness(name: str, launcher: Path, pin: Dict[str, Any]) -> Dict[str, Any]:
    """Check the installed package with the builder's own verifier against its existing pin.

    The builder modules load by path (`from normalizer import ...`, and every
    builder has a `normalizer`), so the verifier runs in its own interpreter.
    """
    if name != "pi":
        raise RuntimeStoreError("no install verifier for %s" % name)
    done = subprocess.run([sys.executable, "-c", _PI_VERIFY, str(PACKAGE / "pi_builder"),
                           str(launcher), json.dumps(pin)],
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeStoreError("installed Pi does not match its pin: %s"
                                % (done.stderr.strip()[-1000:] or "verifier exit %d" % done.returncode))
    return json.loads(done.stdout)


def _git(source: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if done.returncode != 0:
        raise RuntimeStoreError("git %s failed: %s" % (" ".join(args), done.stderr.strip()[-500:]))
    return done.stdout.strip()


def _source_tree_digest(source: Path) -> Dict[str, Any]:
    """Tracked files' working bytes (link targets for symlinks); the Hermes builder's rule."""
    names = sorted(filter(None, _git(source, "ls-files", "-z").split("\0")))
    digest = hashlib.sha256()
    for name in names:
        path = source / name
        content = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return {"source_file_count": len(names), "source_tree_sha256": digest.hexdigest()}


def _verify_hermes_source(source: Path, pin: Dict[str, Any]) -> Dict[str, Any]:
    commit = _git(source, "rev-parse", "HEAD")
    if commit != pin["source_commit"]:
        raise RuntimeStoreError("Hermes source commit %s does not match the pin" % commit)
    if _git(source, "rev-parse", pin["release_tag"] + "^{tag}") != pin["tag_object"]:
        raise RuntimeStoreError("Hermes release tag object does not match the pin")
    if _git(source, "rev-parse", pin["release_tag"] + "^{commit}") != commit:
        raise RuntimeStoreError("Hermes release tag does not point at the source commit")
    if _git(source, "status", "--porcelain", "--untracked-files=normal"):
        raise RuntimeStoreError("Hermes source checkout is dirty")
    lock = hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest()
    if lock != pin["uv_lock_sha256"].removeprefix("sha256:"):
        raise RuntimeStoreError("Hermes uv.lock does not match the pin")
    identity = {"source_commit": commit, "tag_object": pin["tag_object"], "uv_lock_sha256": lock}
    if "source_tree_sha256" in pin:
        identity.update(_source_tree_digest(source))
        if identity["source_tree_sha256"] != pin["source_tree_sha256"]:
            raise RuntimeStoreError("Hermes tracked source does not match the pin")
    return identity


def _install_uv_source(name, entry, spec, steps, root, catalog, fetch, key):
    """Hermes: pinned git tag + `uv sync --frozen`, built IN PLACE.

    uv writes absolute paths into the venv (script shebangs, pyvenv.cfg), so a
    venv cannot be built in .partial/ and renamed. The version dir is built
    where it will live, install.json is written last, and any failure removes
    the whole directory.
    """
    if not shutil.which("git"):
        raise RuntimeStoreError("git is required to install %s; install git and try again" % name)
    uv = steps["uv"]
    if uv["source"] == "store" and not read_install_record(version_dir(root, "uv", uv["version"])):
        add("uv", uv["version"], root=root, catalog=catalog, fetch=fetch, key=key)
    uv_bin = Path(uv["bin"]) / "uv"
    pin = _pin(spec)
    destination = version_dir(root, name, steps["version"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        source = destination / "source"
        done = subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", pin["release_tag"],
                               spec["repository"], str(source)], capture_output=True, text=True,
                              timeout=600, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        if done.returncode != 0:
            raise RuntimeStoreError("git clone failed: %s" % done.stderr.strip()[-1000:])
        identity = _verify_hermes_source(source, pin)  # before running anything from the checkout
        env = dict(os.environ)
        env.pop("UV_OFFLINE", None)
        env.pop("VIRTUAL_ENV", None)
        env.update({"UV_CACHE_DIR": str(root / ".cache" / "uv"),
                    "UV_PYTHON_INSTALL_DIR": str(destination / "python"),
                    "UV_PYTHON_PREFERENCE": "only-managed"})
        command = [str(uv_bin), "sync", "--frozen", "--no-dev"]
        for extra in pin.get("extras", []):
            command += ["--extra", extra]
        if pin.get("python_version"):
            command += ["--python", pin["python_version"]]
        done = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True, timeout=1800)
        if done.returncode != 0:
            raise RuntimeStoreError("uv sync failed (exit %d): %s" % (done.returncode, done.stderr[-2000:]))
        launcher = source / ".venv" / "bin" / entry["executable"]
        if not launcher.is_file():
            raise RuntimeStoreError("uv sync produced no %s launcher" % entry["executable"])
        (destination / "bin").mkdir()
        os.symlink(os.path.join("..", "source", ".venv", "bin", entry["executable"]),
                   destination / "bin" / entry["executable"])
        probe = subprocess.run([str(destination / "bin" / entry["executable"]), "--version"],
                               capture_output=True, text=True, timeout=120,
                               env=dict(env, HERMES_HOME=str(destination / ".probe-home")))
        shutil.rmtree(destination / ".probe-home", ignore_errors=True)
        words = probe.stdout.split()
        reported = words[2].lstrip("v") if len(words) > 2 else ""
        if probe.returncode != 0 or reported != steps["version"]:
            raise RuntimeStoreError("installed %s reports %r, expected %s"
                                    % (name, probe.stdout.strip()[:200], steps["version"]))
        from . import __version__
        record = {
            "schema": RECORD_SCHEMA, "harness": name, "version": steps["version"],
            "kind": "uv-source", "platform": steps["platform"], "catalog_sha256": catalog_digest(),
            "repository": spec["repository"], "release_tag": pin["release_tag"],
            "identity": identity, "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
            "uv": {k: uv[k] for k in ("version", "source", "bin")},
            "source_root": str(source),
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "workbench_version": __version__,
        }
        (destination / "install.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                                                  encoding="utf-8")
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {"installed": True, "path": str(destination), "install_record": record}


def add(name: str, version: Optional[str] = None, root: Optional[Path] = None,
        catalog: Optional[Dict[str, Any]] = None, fetch: Callable[[str], bytes] = _default_fetch,
        key: Optional[str] = None, path_node: Any = "probe") -> Dict[str, Any]:
    """Install one pinned runtime into the store. Idempotent for a complete install."""
    root = store_root() if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    steps = plan(name, version, root, catalog, key, path_node=path_node)
    destination = Path(steps["destination"])
    if steps["already_installed"]:
        return {"installed": False, "path": str(destination), "install_record": read_install_record(destination)}
    if destination.exists() or destination.is_symlink():
        raise RuntimeStoreError("%s exists without a valid install record (an interrupted or foreign "
                                "install); remove it and try again" % destination)
    entry = _harness(catalog, name)
    spec = entry["versions"][steps["version"]]
    if entry["kind"] == "binary":
        return _install_binary(name, entry, spec, steps, root, fetch)
    if entry["kind"] == "tree":
        return _install_tree(name, entry, spec, steps, root, fetch)
    if entry["kind"] == "uv-source":
        return _install_uv_source(name, entry, spec, steps, root, catalog, fetch, steps["platform"])
    return _install_npm(name, entry, spec, steps, root, catalog, fetch, steps["platform"])


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


def _default_install(root: Path, name: str, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    installs = installed(root, name)
    if not installs:
        return None
    return ([i for i in installs if i["version"] == entry["default"]] or installs[-1:])[0]


def _hermes_path_matches(environment: Dict[str, str], commit: str) -> bool:
    """A PATH Hermes is usable for an exact-pin run only if its source is at `commit`.

    The certified Hermes adapter reads the checkout at HERMES_AGENT_ROOT (else
    ~/.hermes/hermes-agent) and refuses any other commit, so a PATH copy of
    another version could only be refused.
    """
    configured = environment.get("HERMES_AGENT_ROOT")
    source = Path(configured).expanduser() if configured else Path.home() / ".hermes" / "hermes-agent"
    try:
        return _git(source, "rev-parse", "HEAD") == commit
    except (RuntimeStoreError, OSError, subprocess.SubprocessError):
        return False


def subject_environment(environment: Dict[str, str], root: Optional[Path] = None,
                        catalog: Optional[Dict[str, Any]] = None,
                        versions: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Make store harnesses findable for a subject process, without overriding PATH copies.

    For each catalog harness: if its executable is already on `environment`'s
    PATH it wins and nothing changes. Otherwise, if the store holds it, its
    `bin/` goes IN FRONT of PATH together with the Node it was installed and
    verified with (an npm harness runs `#!/usr/bin/env node`), and a store
    Hermes gets `HERMES_AGENT_ROOT` unless one is already set. Returns
    {"environment": new_env, "used": [...]} and never touches os.environ.

    `versions` names the store version a caller needs per harness (a Reality
    Check needs the certified Hermes). For Hermes, whose certified adapter
    refuses any other source commit, a PATH copy then wins only when its
    source is at that version's pinned commit; otherwise the store's version
    is used and its source becomes `HERMES_AGENT_ROOT`.
    """
    root = store_root(environment) if root is None else root
    catalog = load_catalog() if catalog is None else catalog
    versions = versions or {}
    env = dict(environment)
    original = env.get("PATH", os.defpath)
    front: List[str] = []
    used = []
    for name, entry in sorted(catalog["harnesses"].items()):
        wanted = versions.get(name)
        exact_hermes = name == "hermes" and wanted is not None
        if shutil.which(entry["executable"], path=original):
            if not exact_hermes:
                continue
            try:
                commit = _pin(entry["versions"][wanted])["source_commit"]
            except (KeyError, OSError, ValueError):
                continue
            if _hermes_path_matches(env, commit):
                continue
        if wanted is not None:
            install = next((i for i in installed(root, name) if i["version"] == wanted), None)
        else:
            install = _default_install(root, name, entry)
        if install is None:
            continue
        bin_dir = Path(install["path"]) / "bin"
        if not (bin_dir / entry["executable"]).exists():
            continue
        record = install["install_record"]
        dirs = [str(bin_dir)]
        node = record.get("node") if isinstance(record.get("node"), dict) else None
        if node and node.get("bin"):
            dirs.append(str(node["bin"]))
        for directory in dirs:
            if directory not in front:
                front.append(directory)
        if record.get("kind") == "uv-source" and record.get("source_root"):
            if exact_hermes or not env.get("HERMES_AGENT_ROOT"):
                env["HERMES_AGENT_ROOT"] = record["source_root"]
        used.append({"harness": name, "version": install["version"], "path": str(bin_dir),
                     "node": node.get("version") if node else None})
    if front:
        env["PATH"] = os.pathsep.join(front + [original])
    return {"environment": env, "used": used}
