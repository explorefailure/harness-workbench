#!/usr/bin/env python3
"""Deterministic workspace/effect archives with fail-closed tree reads."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any
import zipfile

from agent_task_schema import (
    ContractError,
    EFFECTS_SCHEMA,
    MAX_ARCHIVE_BYTES,
    MAX_EFFECTS_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_FILE_BYTES,
    WORKSPACE_SCHEMA,
    bytes_sha256,
    canonical_bytes,
    require_relative_path,
    validate_archive_manifest,
)


FIXED_DATE = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644


class ArchiveError(ContractError):
    """An archive or tree violates the finite v0.1 representation."""


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_DATE)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = FILE_MODE << 16
    info.extra = b""
    info.comment = b""
    return info


def _stable_file(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ArchiveError(f"unsupported non-regular file: {path}")
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ArchiveError(f"file identity changed while opening: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ArchiveError(f"file exceeds declared byte bound: {path}")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final_path = path.lstat()
    identity = lambda row: (
        row.st_dev, row.st_ino, stat.S_IFMT(row.st_mode), row.st_size,
        row.st_mtime_ns,
    )
    if identity(before) != identity(after) or identity(after) != identity(final_path):
        raise ArchiveError(f"file changed during stable read: {path}")
    return b"".join(chunks)


def snapshot_tree(
    root: Path,
    *,
    maximum_files: int = MAX_FILES,
    maximum_file_bytes: int = MAX_FILE_BYTES,
    maximum_total_bytes: int = MAX_TOTAL_FILE_BYTES,
    include_content: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ArchiveError(f"workspace root is not a directory: {root}")
    entries: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    total = 0
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names = sorted(directories + files)
        directories[:] = sorted(directories)
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            require_relative_path(relative, "workspace path")
            metadata = path.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                raise ArchiveError(f"symlink nodes are unsupported: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                entries.append({"path": relative, "kind": "directory", "mode": mode})
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ArchiveError(f"special nodes are unsupported: {relative}")
            data = _stable_file(path, maximum=maximum_file_bytes)
            total += len(data)
            if total > maximum_total_bytes:
                raise ArchiveError("workspace exceeds total file-byte bound")
            digest = bytes_sha256(data)
            entries.append({
                "path": relative,
                "kind": "file",
                "mode": mode,
                "size": len(data),
                "sha256": digest,
            })
            if include_content:
                blobs[digest] = data
            if len(entries) > maximum_files:
                raise ArchiveError("workspace exceeds node-count bound")
    entries.sort(key=lambda row: row["path"])
    return entries, blobs


def _archive_bytes(archive_doc: dict[str, Any], blobs: dict[str, bytes]) -> bytes:
    manifest_bytes = canonical_bytes(archive_doc)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for digest in sorted(blobs):
            archive.writestr(_zip_info("blobs/" + digest.removeprefix("sha256:")), blobs[digest])
        archive.writestr(_zip_info("manifest.json"), manifest_bytes)
    return stream.getvalue()


def build_workspace_archive(root: Path, *, maximum: int = MAX_ARCHIVE_BYTES) -> bytes:
    entries, blobs = snapshot_tree(root, include_content=True)
    raw = _archive_bytes({"schema": WORKSPACE_SCHEMA, "entries": entries}, blobs)
    if len(raw) > maximum:
        raise ArchiveError("workspace archive exceeds declared byte bound")
    validate_archive(raw, WORKSPACE_SCHEMA, maximum=maximum)
    return raw


def build_workspace_archive_from_entries(
    entries: list[tuple[str, str, int, bytes | None]],
    *,
    maximum: int = MAX_ARCHIVE_BYTES,
) -> bytes:
    """Build the canonical workspace archive without touching the filesystem.

    Each tuple is ``(path, kind, mode, content)``. Directories require
    ``content is None`` and regular files require bytes. This is the plan-only
    codec: live assembly can bind the exact archive bytes without creating a
    destination or a temporary fixture tree.
    """
    rows: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    for path, kind, mode, content in entries:
        require_relative_path(path, "workspace path")
        if kind == "directory":
            if content is not None:
                raise ArchiveError("directory archive entries cannot have content")
            rows.append({"path": path, "kind": kind, "mode": mode})
        elif kind == "file":
            if type(content) is not bytes:
                raise ArchiveError("file archive entries require byte content")
            if len(content) > MAX_FILE_BYTES:
                raise ArchiveError("file archive entry exceeds the byte bound")
            digest = bytes_sha256(content)
            rows.append({
                "path": path, "kind": kind, "mode": mode,
                "size": len(content), "sha256": digest,
            })
            blobs[digest] = content
        else:
            raise ArchiveError(f"unsupported workspace entry kind: {kind}")
    rows.sort(key=lambda row: row["path"])
    archive_doc = {"schema": WORKSPACE_SCHEMA, "entries": rows}
    validate_archive_manifest(archive_doc, WORKSPACE_SCHEMA)
    raw = _archive_bytes(archive_doc, blobs)
    if len(raw) > maximum:
        raise ArchiveError("workspace archive exceeds declared byte bound")
    validate_archive(raw, WORKSPACE_SCHEMA, maximum=maximum)
    return raw


def _read_zip(raw: bytes, *, maximum: int) -> tuple[dict[str, Any], dict[str, bytes]]:
    if len(raw) > maximum:
        raise ArchiveError("archive exceeds declared byte bound")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), "r")
    except zipfile.BadZipFile as error:
        raise ArchiveError(f"invalid ZIP archive: {error}") from error
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ArchiveError("ZIP members must be unique and sorted")
        if "manifest.json" not in names:
            raise ArchiveError("archive has no manifest.json")
        for info in infos:
            if (
                info.compress_type != zipfile.ZIP_STORED
                or info.date_time != FIXED_DATE
                or info.extra
                or info.comment
                or info.is_dir()
            ):
                raise ArchiveError(f"noncanonical ZIP member: {info.filename}")
            if info.file_size > maximum or info.compress_size != info.file_size:
                raise ArchiveError(f"invalid member size: {info.filename}")
        try:
            manifest_raw = archive.read("manifest.json")
            archive_doc = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
            raise ArchiveError(f"invalid archive manifest: {error}") from error
        if manifest_raw != canonical_bytes(archive_doc):
            raise ArchiveError("archive manifest is not canonical JSON")
        blobs = {
            "sha256:" + name.removeprefix("blobs/"): archive.read(name)
            for name in names
            if name.startswith("blobs/")
        }
    return archive_doc, blobs


def validate_archive(
    raw: bytes, expected_schema: str, *, maximum: int | None = None
) -> dict[str, Any]:
    if expected_schema not in {WORKSPACE_SCHEMA, EFFECTS_SCHEMA}:
        raise ArchiveError(f"unsupported archive contract: {expected_schema}")
    bound = maximum or (
        MAX_ARCHIVE_BYTES if expected_schema == WORKSPACE_SCHEMA else MAX_EFFECTS_BYTES
    )
    archive_doc, blobs = _read_zip(raw, maximum=bound)
    validate_archive_manifest(archive_doc, expected_schema)
    expected_blobs: set[str] = set()
    total = 0
    for row in archive_doc["entries"]:
        digest = row.get("sha256")
        if digest is None:
            continue
        expected_blobs.add(digest)
        data = blobs.get(digest)
        if data is None:
            raise ArchiveError(f"missing content blob: {digest}")
        if bytes_sha256(data) != digest or len(data) != row["size"]:
            raise ArchiveError(f"content blob disagrees with manifest: {digest}")
        total += len(data)
    if set(blobs) != expected_blobs:
        raise ArchiveError("archive contains an unreferenced or malformed blob")
    if len(archive_doc["entries"]) > MAX_FILES or total > MAX_TOTAL_FILE_BYTES:
        raise ArchiveError("archive expands beyond v0.1 resource bounds")
    return archive_doc


def extract_workspace_archive(raw: bytes, destination: Path) -> list[dict[str, Any]]:
    if destination.exists():
        raise ArchiveError(f"extraction destination already exists: {destination}")
    archive_doc, blobs = _read_zip(raw, maximum=MAX_ARCHIVE_BYTES)
    validate_archive(raw, WORKSPACE_SCHEMA)
    destination.mkdir(mode=0o700)
    for row in archive_doc["entries"]:
        target = destination.joinpath(*PurePosixPath(row["path"]).parts)
        if row["kind"] == "directory":
            target.mkdir(parents=True, exist_ok=False)
            os.chmod(target, row["mode"])
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(target, flags, row["mode"])
            try:
                data = blobs[row["sha256"]]
                offset = 0
                while offset < len(data):
                    offset += os.write(descriptor, data[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(target, row["mode"])
    entries, _ = snapshot_tree(destination)
    if entries != archive_doc["entries"]:
        raise ArchiveError("extracted workspace does not reproduce its manifest")
    return entries


def build_effects_archive(
    before: list[dict[str, Any]], after_root: Path, *, maximum: int = MAX_EFFECTS_BYTES
) -> tuple[bytes, list[dict[str, Any]], list[dict[str, Any]]]:
    after, blobs = snapshot_tree(after_root, include_content=True)
    before_map = {row["path"]: row for row in before}
    after_map = {row["path"]: row for row in after}
    operations: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    for path in sorted(set(before_map) | set(after_map)):
        old = before_map.get(path)
        new = after_map.get(path)
        if old is None:
            operation = {"op": "create", **new}
        elif new is None:
            operation = {
                "op": "delete", "path": path, "kind": old["kind"],
                "mode": old["mode"],
            }
        elif old["kind"] != new["kind"]:
            raise ArchiveError(f"node kind changes are unsupported in v0.1: {path}")
        elif old == new:
            continue
        else:
            operation = {"op": "modify", **new}
        operations.append(operation)
        digest = operation.get("sha256")
        if digest is not None:
            payloads[digest] = blobs[digest]
    archive_doc = {"schema": EFFECTS_SCHEMA, "entries": operations}
    raw = _archive_bytes(archive_doc, payloads)
    if len(raw) > maximum:
        raise ArchiveError("effects archive exceeds declared byte bound")
    validate_archive(raw, EFFECTS_SCHEMA, maximum=maximum)
    return raw, operations, after


def apply_effects_archive(raw: bytes, workspace: Path) -> list[dict[str, Any]]:
    archive_doc, blobs = _read_zip(raw, maximum=MAX_EFFECTS_BYTES)
    validate_archive(raw, EFFECTS_SCHEMA)
    rows = archive_doc["entries"]
    for row in sorted(
        (item for item in rows if item["op"] == "delete"),
        key=lambda item: (len(PurePosixPath(item["path"]).parts), item["path"]),
        reverse=True,
    ):
        target = workspace.joinpath(*PurePosixPath(row["path"]).parts)
        if row["kind"] == "directory":
            target.rmdir()
        else:
            target.unlink()
    for row in sorted(
        (item for item in rows if item["op"] != "delete"),
        key=lambda item: (len(PurePosixPath(item["path"]).parts), item["path"]),
    ):
        target = workspace.joinpath(*PurePosixPath(row["path"]).parts)
        if row["kind"] == "directory":
            if row["op"] == "create":
                target.mkdir(parents=True, exist_ok=False)
            os.chmod(target, row["mode"])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if row["op"] == "create":
            flags |= os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags, row["mode"])
        try:
            data = blobs[row["sha256"]]
            offset = 0
            while offset < len(data):
                offset += os.write(descriptor, data[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(target, row["mode"])
    after, _ = snapshot_tree(workspace)
    return after
