#!/usr/bin/env python3
"""Inspect release archives without extracting or importing them.

This is the package-content half of the release gate.  Installation and the
documented first run are checked separately by verify_installed_artifact.py.
"""
from __future__ import annotations

import argparse
import ast
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
import re
import tarfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "harness_workbench"
RETIRED_PACKAGE = "hwb"
SDIST_STORE_NAMES = {
    "runs", "sweeps", "blasts", "catches", "sensitivity", "efficacy",
    "replays", "steadies", "effects", "interrupts",
}


def fail(message: str) -> None:
    raise SystemExit("artifact verification failed: " + message)


def one(dist: Path, pattern: str) -> Path:
    matches = sorted(dist.glob(pattern))
    if len(matches) != 1:
        fail(f"expected exactly one {pattern!r} artifact, found {len(matches)}")
    return matches[0]


def normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def source_version() -> str:
    init = ROOT / "src" / PACKAGE / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    fail(f"could not find a literal __version__ assignment in {init}")
    raise AssertionError("unreachable")


def expected_package_files() -> set[str]:
    root = ROOT / "src" / PACKAGE
    return {
        str(PurePosixPath(PACKAGE) / path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file() and (path.suffix == ".py" or path.name == "FEATURE.json")
    }


def expected_sdist_files() -> set[str]:
    expected = {"LICENSE", "MANIFEST.in", "NOTICE", "README.md", "pyproject.toml"}
    expected.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src" / PACKAGE).rglob("*")
        if path.is_file() and (path.suffix == ".py" or path.name == "FEATURE.json")
    )
    expected.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "docs").rglob("*.md")
    )
    expected.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("*.py")
    )
    shipped_example_suffixes = {".json", ".md", ".py", ".sh", ".txt"}
    expected.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "examples").rglob("*")
        if path.is_file() and path.suffix in shipped_example_suffixes
    )
    return expected


def check_metadata(raw: bytes, source: str, project: dict, version: str) -> None:
    metadata = BytesParser(policy=default).parsebytes(raw)
    if normalized_distribution(metadata["Name"] or "") != normalized_distribution(project["name"]):
        fail(f"{source} Name metadata disagrees with pyproject.toml")
    if metadata["Version"] != version:
        fail(f"{source} Version metadata {metadata['Version']!r} != {version!r}")
    if metadata["Requires-Python"] != project["requires-python"]:
        fail(f"{source} Requires-Python metadata disagrees with pyproject.toml")


def check_no_retired_package(names: set[str], source: str) -> None:
    for name in names:
        parts = PurePosixPath(name).parts
        if parts and (parts[0] == RETIRED_PACKAGE or parts[0] == RETIRED_PACKAGE + ".py"):
            fail(f"{source} still contains retired import package: {name}")


def check_wheel(path: Path, project: dict, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = {name.rstrip("/") for name in archive.namelist() if not name.endswith("/")}
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1 or len(entry_names) != 1:
            fail("wheel must contain exactly one METADATA and one entry_points.txt")
        check_metadata(archive.read(metadata_names[0]), "wheel", project, version)
        entries = archive.read(entry_names[0]).decode("utf-8")

    missing = sorted(expected_package_files() - names)
    if missing:
        fail("wheel is missing package files: " + ", ".join(missing))
    check_no_retired_package(names, "wheel")
    unexpected_roots = sorted({
        PurePosixPath(name).parts[0]
        for name in names
        if PurePosixPath(name).parts[0] != PACKAGE
        and not PurePosixPath(name).parts[0].endswith(".dist-info")
    })
    if unexpected_roots:
        fail("wheel contains unexpected top-level paths: " + ", ".join(unexpected_roots))
    if any(PurePosixPath(name).parts[0] in {"docs", "examples", "tests"} for name in names):
        fail("wheel unexpectedly contains project docs, examples, or tests")
    if "hwb = harness_workbench.cli:main" not in entries:
        fail("wheel does not declare the expected hwb console entry point")


def check_sdist(path: Path, project: dict, version: str) -> None:
    with tarfile.open(path, "r:gz") as archive:
        files = [member for member in archive.getmembers() if member.isfile()]
        roots = {PurePosixPath(member.name).parts[0] for member in files}
        if len(roots) != 1:
            fail(f"sdist must have one top-level directory, found {sorted(roots)}")
        root = next(iter(roots))
        names = {
            PurePosixPath(*PurePosixPath(member.name).parts[1:]).as_posix()
            for member in files
        }
        pkg_info = next((member for member in files if member.name == f"{root}/PKG-INFO"), None)
        if pkg_info is None:
            fail("sdist has no top-level PKG-INFO")
        extracted = archive.extractfile(pkg_info)
        if extracted is None:
            fail("could not read sdist PKG-INFO")
        check_metadata(extracted.read(), "sdist", project, version)

    missing = sorted(expected_sdist_files() - names)
    if missing:
        fail("sdist is missing project files: " + ", ".join(missing))
    check_no_retired_package({
        PurePosixPath(*PurePosixPath(name).parts[1:]).as_posix()
        if PurePosixPath(name).parts[:1] == ("src",) else name
        for name in names
    }, "sdist")
    leaked = sorted(
        name for name in names
        if set(PurePosixPath(name).parts) & SDIST_STORE_NAMES
        or PurePosixPath(name).name in {"attempts.jsonl", "integrity.json", "record.json"}
    )
    if leaked:
        fail("sdist contains generated run/campaign evidence: " + ", ".join(leaked))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist", type=Path, help="directory containing one wheel and one sdist")
    args = parser.parse_args()
    dist = args.dist.resolve()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = source_version()
    if project["version"] != version:
        fail(f"pyproject version {project['version']!r} != package __version__ {version!r}")

    wheel = one(dist, "*.whl")
    sdist = one(dist, "*.tar.gz")
    check_wheel(wheel, project, version)
    check_sdist(sdist, project, version)
    print(f"verified {wheel.name} and {sdist.name} (version {version})")


if __name__ == "__main__":
    main()
