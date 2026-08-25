#!/usr/bin/env python3
"""Repack a PEP 517 source distribution with release-safe tar metadata.

The build backend remains responsible for selecting files and generating
PKG-INFO.  This tool changes only archive metadata: member order, ownership,
timestamps, and the gzip header.  It validates paths and supported member
types before writing anything and atomically replaces the destination.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile


NEUTRAL_UID = 0
NEUTRAL_GID = 0
NEUTRAL_UNAME = "root"
NEUTRAL_GNAME = "root"
NEUTRAL_FILE_MODE = 0o644
NEUTRAL_EXEC_MODE = 0o755
NEUTRAL_DIR_MODE = 0o755
NEUTRAL_LINK_MODE = 0o777
MAX_GZIP_MTIME = (1 << 32) - 1


class NormalizationError(ValueError):
    """The input cannot be normalized without changing its meaning safely."""


def parse_epoch(value: str) -> int:
    try:
        epoch = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "SOURCE_DATE_EPOCH must be a base-10 integer"
        ) from error
    if not 0 <= epoch <= MAX_GZIP_MTIME:
        raise argparse.ArgumentTypeError(
            f"SOURCE_DATE_EPOCH must be between 0 and {MAX_GZIP_MTIME}"
        )
    return epoch


def safe_member_path(name: str) -> PurePosixPath:
    """Return a canonical relative archive path or reject it."""
    if not name or "\x00" in name or "\\" in name:
        raise NormalizationError(f"unsafe archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise NormalizationError(f"unsafe archive member path: {name!r}")
    if name.rstrip("/") != path.as_posix():
        raise NormalizationError(f"non-canonical archive member path: {name!r}")
    return path


def _safe_link_target(member: tarfile.TarInfo, root: str) -> None:
    link = safe_member_path(member.linkname)
    if member.issym():
        resolved = PurePosixPath(member.name).parent / link
    else:
        resolved = link
    if not resolved.parts or resolved.parts[0] != root:
        raise NormalizationError(
            f"archive link escapes its top-level directory: "
            f"{member.name!r} -> {member.linkname!r}"
        )


def validate_members(members: list[tarfile.TarInfo]) -> None:
    if not members:
        raise NormalizationError("source distribution is empty")
    names: set[str] = set()
    roots: set[str] = set()
    for member in members:
        path = safe_member_path(member.name)
        if member.name in names:
            raise NormalizationError(f"duplicate archive member: {member.name!r}")
        names.add(member.name)
        roots.add(path.parts[0])
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise NormalizationError(
                f"unsupported archive member type for {member.name!r}"
            )
    if len(roots) != 1:
        raise NormalizationError(
            f"source distribution must have one top-level directory: {sorted(roots)}"
        )
    root = next(iter(roots))
    for member in members:
        if member.issym() or member.islnk():
            _safe_link_target(member, root)
            if member.islnk() and member.linkname not in names:
                raise NormalizationError(
                    f"hard-link target is not an archive member: "
                    f"{member.name!r} -> {member.linkname!r}"
                )


def normalized_mode(member: tarfile.TarInfo) -> int:
    """The member's mode reduced to the one bit that carries meaning.

    Modes came straight from the checkout, and a checkout's modes come from the
    builder's umask: the same commit built under `umask 077` produced a
    different archive than under `umask 022`, differing in nothing but
    permission bits. Ownership and timestamps were already neutralized, so the
    archive looked reproducible while quietly depending on an environment
    variable nobody recorded. Git tracks exactly one permission bit, so keep
    that one and fix the rest.
    """
    if member.isdir():
        return NEUTRAL_DIR_MODE
    if member.issym() or member.islnk():
        return NEUTRAL_LINK_MODE
    return NEUTRAL_EXEC_MODE if member.mode & 0o100 else NEUTRAL_FILE_MODE


def _normalized_info(member: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    info = copy.copy(member)
    info.uid = NEUTRAL_UID
    info.gid = NEUTRAL_GID
    info.uname = NEUTRAL_UNAME
    info.gname = NEUTRAL_GNAME
    info.mode = normalized_mode(member)
    info.mtime = epoch
    info.devmajor = 0
    info.devminor = 0
    info.pax_headers = {}
    return info


def normalize(source: Path, destination: Path, epoch: int) -> Path:
    """Normalize *source* into *destination* and return the destination."""
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise NormalizationError(f"source distribution does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    temporary_name: str | None = None
    try:
        with tarfile.open(source, "r:gz") as input_archive:
            members = input_archive.getmembers()
            validate_members(members)
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=temporary,
                    compresslevel=9,
                    mtime=epoch,
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed,
                        mode="w",
                        format=tarfile.PAX_FORMAT,
                    ) as output_archive:
                        for member in sorted(members, key=lambda item: item.name):
                            info = _normalized_info(member, epoch)
                            stream = (
                                input_archive.extractfile(member)
                                if member.isfile()
                                else None
                            )
                            output_archive.addfile(info, stream)
        os.replace(temporary_name, destination)
        temporary_name = None
    except (OSError, tarfile.TarError) as error:
        raise NormalizationError(f"could not normalize {source}: {error}") from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sdist",
        type=Path,
        help="raw .tar.gz built by the PEP 517 backend",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="directory that will receive the normalized archive with the same filename",
    )
    parser.add_argument(
        "--source-date-epoch",
        type=parse_epoch,
        default=None,
        help="release commit timestamp (defaults to required SOURCE_DATE_EPOCH)",
    )
    args = parser.parse_args()
    epoch = args.source_date_epoch
    if epoch is None:
        raw_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        if raw_epoch is None:
            parser.error("set SOURCE_DATE_EPOCH or pass --source-date-epoch")
        epoch = parse_epoch(raw_epoch)
    output = normalize(args.sdist, args.output_dir / args.sdist.name, epoch)
    print(f"normalized {args.sdist} -> {output} (SOURCE_DATE_EPOCH={epoch})")


if __name__ == "__main__":
    main()
