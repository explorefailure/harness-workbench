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
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
import tomllib
import zipfile

import normalize_sdist


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "harness_workbench"
RETIRED_PACKAGE = "hwb"
SDIST_STORE_NAMES = {
    "runs", "sweeps", "blasts", "catches", "sensitivity", "efficacy",
    "replays", "steadies", "effects", "interrupts",
}
ALLOWED_PAX_HEADERS = {"path", "linkpath"}
WHEEL_RECORD_MODE = 0o664


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
    expected = {
        "CHANGELOG.md", "CONTRIBUTING.md", "LICENSE", "MANIFEST.in", "NOTICE",
        "README.md", "RELEASING.md", "SECURITY.md", "SUPPORT.md",
        "pyproject.toml",
    }
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
    expected.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tools").rglob("*.py")
    )
    shipped_example_suffixes = {".json", ".md", ".py", ".sh", ".txt"}
    expected.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "examples").rglob("*")
        if path.is_file() and path.suffix in shipped_example_suffixes
    )
    return expected


def compact_requirement(requirement: str) -> str:
    """Normalize the harmless whitespace a metadata backend may add."""
    return re.sub(r"\s+", "", requirement)


def expected_requirements(project: dict) -> set[tuple[str, str]]:
    expected = {
        ("", compact_requirement(requirement))
        for requirement in project.get("dependencies", [])
    }
    for extra, requirements in project.get("optional-dependencies", {}).items():
        expected.update(
            (extra, compact_requirement(requirement))
            for requirement in requirements
        )
    return expected


def actual_requirements(metadata, source: str) -> set[tuple[str, str]]:
    actual = set()
    for value in metadata.get_all("Requires-Dist") or []:
        requirement, separator, marker = value.partition(";")
        extra = ""
        if separator:
            match = re.fullmatch(
                r"\s*extra\s*==\s*['\"]([^'\"]+)['\"]\s*", marker
            )
            if match is None:
                fail(f"{source} has an unexpected dependency marker: {value!r}")
            extra = match.group(1)
        actual.add((extra, compact_requirement(requirement)))
    return actual


def check_metadata(raw: bytes, source: str, project: dict, version: str) -> None:
    metadata = BytesParser(policy=default).parsebytes(raw)
    if metadata["Metadata-Version"] != "2.4":
        fail(f"{source} does not use Core Metadata 2.4 required by PEP 639")
    if normalized_distribution(metadata["Name"] or "") != normalized_distribution(project["name"]):
        fail(f"{source} Name metadata disagrees with pyproject.toml")
    if metadata["Version"] != version:
        fail(f"{source} Version metadata {metadata['Version']!r} != {version!r}")
    if metadata["Requires-Python"] != project["requires-python"]:
        fail(f"{source} Requires-Python metadata disagrees with pyproject.toml")
    if metadata["Summary"] != project["description"]:
        fail(f"{source} Summary metadata disagrees with pyproject.toml")
    if metadata["License-Expression"] != project["license"]:
        fail(f"{source} License-Expression metadata disagrees with pyproject.toml")
    if set(metadata.get_all("License-File") or []) != set(project["license-files"]):
        fail(f"{source} License-File metadata disagrees with pyproject.toml")
    maintainers = project.get("maintainers", [])
    expected_maintainer = ", ".join(person["name"] for person in maintainers)
    if metadata["Maintainer"] != expected_maintainer:
        fail(f"{source} Maintainer metadata disagrees with pyproject.toml")
    keywords = {
        keyword.strip() for keyword in (metadata["Keywords"] or "").split(",")
        if keyword.strip()
    }
    if keywords != set(project.get("keywords", [])):
        fail(f"{source} Keywords metadata disagrees with pyproject.toml")
    if set(metadata.get_all("Classifier") or []) != set(project.get("classifiers", [])):
        fail(f"{source} Classifier metadata disagrees with pyproject.toml")
    urls = {}
    for value in metadata.get_all("Project-URL") or []:
        label, separator, url = value.partition(",")
        if not separator:
            fail(f"{source} has a malformed Project-URL: {value!r}")
        urls[label.strip()] = url.strip()
    if urls != project.get("urls", {}):
        fail(f"{source} Project-URL metadata disagrees with pyproject.toml")
    if set(metadata.get_all("Provides-Extra") or []) != set(
        project.get("optional-dependencies", {})
    ):
        fail(f"{source} Provides-Extra metadata disagrees with pyproject.toml")
    if actual_requirements(metadata, source) != expected_requirements(project):
        fail(f"{source} Requires-Dist metadata disagrees with pyproject.toml")


def check_no_retired_package(names: set[str], source: str) -> None:
    for name in names:
        parts = PurePosixPath(name).parts
        if parts and (parts[0] == RETIRED_PACKAGE or parts[0] == RETIRED_PACKAGE + ".py"):
            fail(f"{source} still contains retired import package: {name}")


def check_wheel_member_modes(archive: zipfile.ZipFile) -> None:
    """Reject a wheel whose members carry the builder's umask.

    Nothing normalizes the wheel the way normalize_sdist.py normalizes the
    sdist: setuptools stores each file's mode as it found it in the checkout,
    and a checkout's modes come from the umask. Building the same commit under
    `umask 077` produced a wheel differing from the `umask 022` one in nothing
    but permission bits -- which the step 3 comparison against the GitHub clone
    would report as a reproducibility failure, for a cause that is neither the
    remote nor the build. Fail here, where the message can say so, rather than
    two steps later where it cannot.
    """
    for info in archive.infolist():
        stored = info.external_attr >> 16
        if not stored:
            # No POSIX mode recorded at all; nothing to disagree about.
            continue
        permissions = stored & 0o7777
        if info.is_dir():
            allowed = {normalize_sdist.NEUTRAL_DIR_MODE}
        elif PurePosixPath(info.filename).name == "RECORD":
            # The wheel writer stamps RECORD itself and always writes 0o664,
            # at any umask. That is its choice, not the environment's.
            allowed = {WHEEL_RECORD_MODE}
        elif permissions & 0o100:
            allowed = {normalize_sdist.NEUTRAL_EXEC_MODE}
        else:
            allowed = {normalize_sdist.NEUTRAL_FILE_MODE}
        if permissions not in allowed:
            expected = " or ".join(f"{mode:#o}" for mode in sorted(allowed))
            fail(
                f"wheel member {info.filename!r} has non-neutral mode "
                f"{permissions:#o}, expected {expected}; the build inherited "
                "the umask -- rebuild with `umask 022`"
            )


def check_wheel(path: Path, project: dict, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        check_wheel_member_modes(archive)
        names = {name.rstrip("/") for name in archive.namelist() if not name.endswith("/")}
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(metadata_names) != 1 or len(entry_names) != 1:
            fail("wheel must contain exactly one METADATA and one entry_points.txt")
        check_metadata(archive.read(metadata_names[0]), "wheel", project, version)
        entries = archive.read(entry_names[0]).decode("utf-8")
        metadata_root = PurePosixPath(metadata_names[0]).parent
        for license_path in project["license-files"]:
            archived = str(metadata_root / "licenses" / license_path)
            if archived in names and archive.read(archived) != (ROOT / license_path).read_bytes():
                fail(f"wheel {license_path} content disagrees with the source tree")

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
    expected_entry = f"hwb = {project['scripts']['hwb']}"
    if expected_entry not in entries:
        fail("wheel does not declare the expected hwb console entry point")
    expected_licenses = {
        str(metadata_root / "licenses" / path)
        for path in project["license-files"]
    }
    missing_licenses = sorted(expected_licenses - names)
    if missing_licenses:
        fail("wheel is missing license files: " + ", ".join(missing_licenses))


def check_sdist_archive_safety(path: Path, epoch: int) -> None:
    with path.open("rb") as stream:
        header = stream.read(10)
    if len(header) != 10 or header[:3] != b"\x1f\x8b\x08":
        fail("sdist is not a gzip-compressed archive")
    flags = header[3]
    gzip_mtime = struct.unpack("<I", header[4:8])[0]
    if flags != 0:
        fail(f"sdist gzip header contains non-neutral optional fields: flags={flags:#x}")
    if gzip_mtime != epoch:
        fail(f"sdist gzip timestamp {gzip_mtime} != SOURCE_DATE_EPOCH {epoch}")
    if header[8] != 2:
        fail(f"sdist gzip header does not declare maximum compression: XFL={header[8]}")
    if header[9] != 255:
        fail(f"sdist gzip header exposes a platform identifier: OS={header[9]}")

    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        try:
            normalize_sdist.validate_members(members)
        except normalize_sdist.NormalizationError as error:
            fail(str(error))
        if [member.name for member in members] != sorted(
            member.name for member in members
        ):
            fail("sdist members are not in canonical path order")
        for member in members:
            if (member.uid, member.gid) != (
                normalize_sdist.NEUTRAL_UID,
                normalize_sdist.NEUTRAL_GID,
            ):
                fail(
                    f"sdist member {member.name!r} has non-neutral numeric ownership "
                    f"{member.uid}:{member.gid}"
                )
            if (member.uname, member.gname) != (
                normalize_sdist.NEUTRAL_UNAME,
                normalize_sdist.NEUTRAL_GNAME,
            ):
                fail(
                    f"sdist member {member.name!r} has non-neutral named ownership "
                    f"{member.uname!r}:{member.gname!r}"
                )
            expected_mode = normalize_sdist.normalized_mode(member)
            if member.mode != expected_mode:
                fail(
                    f"sdist member {member.name!r} has non-neutral mode "
                    f"{member.mode:#o}, expected {expected_mode:#o}; modes must "
                    "not carry the builder's umask into the archive"
                )
            if member.mtime != epoch:
                fail(
                    f"sdist member {member.name!r} timestamp {member.mtime} "
                    f"!= SOURCE_DATE_EPOCH {epoch}"
                )
            unexpected_pax = set(member.pax_headers) - ALLOWED_PAX_HEADERS
            if unexpected_pax:
                fail(
                    f"sdist member {member.name!r} has non-deterministic PAX fields: "
                    + ", ".join(sorted(unexpected_pax))
                )


def check_sdist(path: Path, project: dict, version: str, epoch: int) -> None:
    check_sdist_archive_safety(path, epoch)
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
        members = {
            PurePosixPath(*PurePosixPath(member.name).parts[1:]).as_posix(): member
            for member in files
        }
        pkg_info = next((member for member in files if member.name == f"{root}/PKG-INFO"), None)
        if pkg_info is None:
            fail("sdist has no top-level PKG-INFO")
        extracted = archive.extractfile(pkg_info)
        if extracted is None:
            fail("could not read sdist PKG-INFO")
        check_metadata(extracted.read(), "sdist", project, version)
        for license_path in project["license-files"]:
            member = members.get(license_path)
            if member is None:
                continue
            archived = archive.extractfile(member)
            if archived is None or archived.read() != (ROOT / license_path).read_bytes():
                fail(f"sdist {license_path} content disagrees with the source tree")

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
    parser.add_argument(
        "--source-date-epoch",
        type=normalize_sdist.parse_epoch,
        default=None,
        help="release commit timestamp (defaults to required SOURCE_DATE_EPOCH)",
    )
    args = parser.parse_args()
    epoch = args.source_date_epoch
    if epoch is None:
        raw_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        if raw_epoch is None:
            parser.error("set SOURCE_DATE_EPOCH or pass --source-date-epoch")
        epoch = normalize_sdist.parse_epoch(raw_epoch)
    dist = args.dist.resolve()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    version = source_version()
    if "version" in project or "version" not in project.get("dynamic", []):
        fail("pyproject.toml must declare version as dynamic, not duplicate it")
    version_attr = pyproject["tool"]["setuptools"]["dynamic"]["version"].get("attr")
    if version_attr != f"{PACKAGE}.__version__":
        fail("pyproject.toml does not source version from the import package")

    wheel = one(dist, "*.whl")
    sdist = one(dist, "*.tar.gz")
    check_wheel(wheel, project, version)
    check_sdist(sdist, project, version, epoch)
    print(f"verified {wheel.name} and {sdist.name} (version {version})")


if __name__ == "__main__":
    main()
