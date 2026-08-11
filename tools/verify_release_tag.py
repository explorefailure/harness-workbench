#!/usr/bin/env python3
"""Fail unless a release tag is the exact public spelling of the source version."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION = re.compile(
    r"^(?P<release>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)(?:rc(?P<rc>0|[1-9]\d*))?$"
)
TAG_VERSION = re.compile(
    r"^v(?P<release>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)(?:-rc\.(?P<rc>0|[1-9]\d*))?$"
)


class TagError(ValueError):
    pass


def source_version() -> str:
    init = ROOT / "src" / "harness_workbench" / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__"
                   for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise TagError(f"could not read literal __version__ from {init}")


def package_version_for_tag(tag: str) -> str:
    match = TAG_VERSION.fullmatch(tag)
    if match is None:
        raise TagError(
            f"tag {tag!r} is not vMAJOR.MINOR.PATCH or "
            "vMAJOR.MINOR.PATCH-rc.N"
        )
    base = ".".join(match.group(name) for name in ("release", "minor", "patch"))
    return base + (("rc" + match.group("rc")) if match.group("rc") else "")


def tag_for_package_version(version: str) -> str:
    match = PACKAGE_VERSION.fullmatch(version)
    if match is None:
        raise TagError(
            f"package version {version!r} is outside the release policy; "
            "expected MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCHrcN"
        )
    base = "v" + ".".join(
        match.group(name) for name in ("release", "minor", "patch")
    )
    return base + (("-rc." + match.group("rc")) if match.group("rc") else "")


def verify(tag: str, version: str) -> None:
    from_tag = package_version_for_tag(tag)
    expected_tag = tag_for_package_version(version)
    if from_tag != version or tag != expected_tag:
        raise TagError(
            f"tag {tag!r} maps to package version {from_tag!r}; "
            f"source version {version!r} requires tag {expected_tag!r}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="tag name, for example v0.1.0-rc.1")
    parser.add_argument(
        "--version", help="package version override (tests only; default: source)"
    )
    args = parser.parse_args()
    version = args.version or source_version()
    try:
        verify(args.tag, version)
    except TagError as error:
        raise SystemExit("release tag verification failed: " + str(error))
    print(f"verified tag {args.tag} <-> package version {version}")


if __name__ == "__main__":
    main()
