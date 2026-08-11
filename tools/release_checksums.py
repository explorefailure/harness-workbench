#!/usr/bin/env python3
"""Write or verify the SHA-256 manifest for one wheel and one sdist."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re


MANIFEST = "SHA256SUMS"
LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^/]+)$")


class ChecksumError(ValueError):
    pass


def artifacts(dist: Path) -> list[Path]:
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ChecksumError(
            "expected exactly one wheel and one sdist, found "
            f"{len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    return sorted(wheels + sdists, key=lambda path: path.name)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected(dist: Path) -> str:
    return "".join(f"{sha256(path)}  {path.name}\n" for path in artifacts(dist))


def write(dist: Path) -> Path:
    path = dist / MANIFEST
    content = expected(dist)
    path.write_text(content, encoding="ascii")
    return path


def check(dist: Path) -> Path:
    path = dist / MANIFEST
    if not path.is_file():
        raise ChecksumError(f"missing checksum manifest: {path}")
    content = path.read_text(encoding="ascii")
    lines = content.splitlines()
    if not lines or any(LINE.fullmatch(line) is None for line in lines):
        raise ChecksumError(f"malformed checksum manifest: {path}")
    wanted = expected(dist)
    if content != wanted:
        raise ChecksumError("checksum manifest disagrees with the release artifacts")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("write", "check"))
    parser.add_argument("dist", type=Path, help="release artifact directory")
    args = parser.parse_args()
    dist = args.dist.resolve()
    try:
        path = write(dist) if args.action == "write" else check(dist)
    except (ChecksumError, OSError, UnicodeError) as error:
        raise SystemExit("release checksum operation failed: " + str(error))
    verb = "wrote" if args.action == "write" else "verified"
    print(f"{verb} {path}")


if __name__ == "__main__":
    main()
