"""Observe bounded filesystem effects around one ordinary run.

Family 13. ``confine`` checks the record channel; it cannot see a feature
that opens a file behind the recorder's back.  This campaign checks a
different, deliberately smaller relation:

    Every endpoint change under an explicitly watched root is inside an
    explicitly allowed path.

This is not a syscall tracer.  Two portable tree snapshots can observe files
that exist before or after the run, their content, type, and mode.  They
cannot observe an ephemeral create/delete between snapshots, anything beyond
the watched roots, or process and network effects.  A passing verdict is
therefore ``within_envelope``, never ``clean``.
"""
from __future__ import annotations

import hashlib
import os
import stat
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .canon import canon_bytes, digest_file
from .runner import _stamp

WITHIN_ENVELOPE = "within_envelope"
BREACH = "BREACH"
UNINTERPRETABLE = "uninterpretable"
SETUP_ERROR = "setup_error"
INSTRUMENT_ERROR = "instrument_error"

SENSOR = "portable-endpoint-tree-snapshot/0.1"
OBSERVED_CLASSES = (
    "path creation and removal visible at either endpoint",
    "regular-file content digests and byte sizes",
    "symbolic-link targets without following them",
    "path types and permission modes",
)
UNOBSERVED_CLASSES = (
    "changes outside the explicitly watched roots",
    "files created and removed between the two endpoint snapshots",
    "filesystem reads and attempted writes that leave no endpoint change",
    "timestamps, ownership, extended attributes, ACLs, and file locks",
    "process creation, descendant lifetime, signals, and IPC",
    "network, DNS, sockets, and remote service effects",
)


class EffectsError(Exception):
    """The declared measurement envelope is not safe or well formed."""


def _inside(path: str, root: str, strict: bool = False) -> bool:
    try:
        common = os.path.commonpath((path, root))
    except ValueError:
        return False
    return common == root and (not strict or path != root)


def _overlap(a: str, b: str) -> bool:
    return _inside(a, b) or _inside(b, a)


def _display(path: str, base: str) -> str:
    # macOS exposes /tmp through /private/tmp. Contract resolution uses
    # realpath so the display base must use the same spelling; otherwise an
    # in-tree path appears as ../../private/... and can never match its
    # allowance even though both resolve to the same file.
    return os.path.relpath(path, os.path.realpath(base)).replace(os.sep, "/")


def _resolve_contract(spec_dir: str, watches: Iterable[str],
                      allowances: Iterable[str], runs_root: str,
                      effects_root: str) -> Tuple[List[Dict[str, str]],
                                                  List[Dict[str, str]]]:
    base = os.path.realpath(spec_dir)
    watch_rows: List[Dict[str, str]] = []
    seen = set()
    for raw in watches:
        absolute = os.path.realpath(os.path.join(base, raw))
        if not _inside(absolute, base, strict=True):
            raise EffectsError(
                "watch %r must be an existing subdirectory of the spec "
                "directory; the spec directory itself and broader roots "
                "are refused" % raw)
        if absolute in seen:
            continue
        if not os.path.isdir(absolute) or os.path.islink(absolute):
            raise EffectsError("watch %r is not an existing ordinary directory"
                               % raw)
        seen.add(absolute)
        watch_rows.append({"path": _display(absolute, base),
                           "absolute": absolute})
    if not watch_rows:
        raise EffectsError(
            "effects needs at least one explicit --watch subdirectory; "
            "there is no default root")

    for i, row in enumerate(watch_rows):
        for other in watch_rows[i + 1:]:
            if _overlap(row["absolute"], other["absolute"]):
                raise EffectsError("watched roots overlap: %s and %s" %
                                   (row["path"], other["path"]))

    stores = (("run store", os.path.realpath(runs_root)),
              ("effects store", os.path.realpath(effects_root)))
    for row in watch_rows:
        for label, store in stores:
            if _overlap(row["absolute"], store):
                raise EffectsError(
                    "watch %s overlaps the %s at %s; instrument-owned "
                    "writes must not enter the subject envelope"
                    % (row["path"], label, store))

    allow_rows: List[Dict[str, str]] = []
    allowed_seen = set()
    for raw in allowances:
        # realpath is useful even when the final leaf does not exist: it
        # resolves every existing parent and prevents ``..`` escaping.
        absolute = os.path.realpath(os.path.join(base, raw))
        owners = [row for row in watch_rows if _inside(absolute, row["absolute"])]
        if len(owners) != 1:
            raise EffectsError(
                "allow %r must be inside exactly one watched root" % raw)
        if absolute in allowed_seen:
            continue
        allowed_seen.add(absolute)
        allow_rows.append({"path": _display(absolute, base),
                           "absolute": absolute,
                           "watch": owners[0]["path"]})
    return watch_rows, allow_rows


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special"


def _fingerprint(path: str) -> Dict[str, Any]:
    st = os.lstat(path)
    kind = _kind(st.st_mode)
    row: Dict[str, Any] = {
        "type": kind,
        "mode": "%04o" % stat.S_IMODE(st.st_mode),
    }
    if kind == "regular":
        row["bytes"] = st.st_size
        row["digest"] = digest_file(path)
    elif kind == "symlink":
        target = os.readlink(path)
        row["target"] = target
        row["digest"] = "sha256:" + hashlib.sha256(
            os.fsencode(target)).hexdigest()
    elif kind == "special":
        # Existence and type are visible, content/traffic is not.  The caller
        # makes this an uninterpretable scoped verdict instead of pretending
        # the unchanged stat record proves the node did nothing.
        row["content_observed"] = False
    return row


def snapshot(watches: List[Dict[str, str]], base: str
             ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Snapshot without following symlinked directories."""
    state: Dict[str, Dict[str, Any]] = {}
    special: List[str] = []

    def fail(error: OSError) -> None:
        raise error

    for watch in watches:
        root = watch["absolute"]
        # The root is part of the envelope too. If the subject deletes it,
        # the endpoint difference is evidence, not a failed after-snapshot.
        if not os.path.lexists(root):
            continue
        root_rel = _display(root, base)
        root_cell = _fingerprint(root)
        state[root_rel] = root_cell
        if root_cell["type"] == "special":
            special.append(root_rel)
        if root_cell["type"] != "directory":
            continue
        for dirpath, dirnames, filenames in os.walk(
                root, followlinks=False, onerror=fail):
            # os.walk puts symlinked directories in dirnames. Record them as
            # links, then remove them so their targets stay outside scope.
            names = sorted(dirnames + filenames)
            for name in names:
                full = os.path.join(dirpath, name)
                rel = _display(full, base)
                cell = _fingerprint(full)
                state[rel] = cell
                if cell["type"] == "special":
                    special.append(rel)
            dirnames[:] = sorted(
                name for name in dirnames
                if not os.path.islink(os.path.join(dirpath, name)))
    return state, sorted(special)


def _allowed(path: str, allowances: List[Dict[str, str]], base: str) -> bool:
    absolute = os.path.realpath(os.path.join(base, path))
    return any(_inside(absolute, row["absolute"])
               for row in allowances)


def compare(before: Dict[str, Dict[str, Any]],
            after: Dict[str, Dict[str, Any]],
            allowances: List[Dict[str, str]], base: str
            ) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if old is None:
            change = "added"
        elif new is None:
            change = "removed"
        elif old.get("type") != new.get("type"):
            change = "type_changed"
        elif old.get("digest") != new.get("digest"):
            change = "content_changed"
        elif old.get("mode") != new.get("mode"):
            change = "mode_changed"
        else:
            change = "changed"
        rows.append({"path": path, "change": change,
                     "allowed": _allowed(path, allowances, base),
                     "before": old, "after": new})
    return rows


def classify(changes: List[Dict[str, Any]], special_paths: List[str],
             setup_error: Optional[str] = None,
             instrument_error: Optional[str] = None) -> str:
    if instrument_error:
        return INSTRUMENT_ERROR
    if setup_error:
        return SETUP_ERROR
    if special_paths:
        return UNINTERPRETABLE
    if any(not row["allowed"] for row in changes):
        return BREACH
    return WITHIN_ENVELOPE


def campaign(spec_path: str, runs_root: str, effects_root: str,
             watches: Iterable[str], allowances: Iterable[str]
             ) -> Dict[str, Any]:
    from . import features as featmod, runner, spec as specmod

    try:
        base_spec = specmod.load(spec_path)
    except specmod.SpecError as e:
        raise EffectsError(str(e))
    watch_rows, allow_rows = _resolve_contract(
        base_spec.dir, watches, allowances, runs_root, effects_root)

    campaign_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(effects_root, campaign_id)
    try:
        os.makedirs(cdir)
    except OSError as e:
        raise EffectsError("cannot create effects campaign store at %s: %s"
                           % (cdir, e))

    run_id: Optional[str] = None
    setup_error: Optional[str] = None
    instrument_error: Optional[str] = None
    before: Dict[str, Dict[str, Any]] = {}
    after: Dict[str, Dict[str, Any]] = {}
    before_special: List[str] = []
    after_special: List[str] = []
    try:
        before, before_special = snapshot(watch_rows, base_spec.dir)
    except OSError as e:
        instrument_error = "before snapshot failed: %s" % e

    if instrument_error is None:
        try:
            # Resolution happens inside the measured interval. A feature can
            # perform effects at import time, before any seam is dispatched.
            current = specmod.load(spec_path)
            record = runner.execute(current, featmod.resolve(current), runs_root)
            run_id = record["run_id"]
        except (specmod.SpecError, featmod.FeatureError,
                runner.HarnessError) as e:
            setup_error = "subject could not execute: %s" % e
        try:
            after, after_special = snapshot(watch_rows, base_spec.dir)
        except OSError as e:
            instrument_error = "after snapshot failed: %s" % e

    changes = (compare(before, after, allow_rows, base_spec.dir)
               if not instrument_error else [])
    special_paths = sorted(set(before_special) | set(after_special))
    verdict = classify(changes, special_paths, setup_error, instrument_error)
    manifest: Dict[str, Any] = {
        "schema": "hwbeffects/v0.1",
        "campaign_id": campaign_id,
        "base_spec": os.path.abspath(spec_path),
        "base_spec_digest": base_spec.digest,
        "run_id": run_id,
        "watched_roots": [{"path": row["path"]} for row in watch_rows],
        "allowed_paths": [{"path": row["path"], "watch": row["watch"]}
                          for row in allow_rows],
        "sensor": {
            "name": SENSOR,
            "observed": list(OBSERVED_CLASSES),
            "unobserved": list(UNOBSERVED_CLASSES),
            "unobserved_special_paths": special_paths,
        },
        "changes": changes,
        "allowed_changes": [row for row in changes if row["allowed"]],
        "breaches": [row for row in changes if not row["allowed"]],
        "verdict": verdict,
        "setup_error": setup_error,
        "instrument_error": instrument_error,
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest
