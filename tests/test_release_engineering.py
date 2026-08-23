"""Mechanical checks for release policy that must not depend on GitHub."""
from __future__ import annotations

import ast
import fnmatch
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))


def _glob_matches(relative: str, glob: str) -> bool:
    """Match a package-data glob the way setuptools does, not the way fnmatch does.

    `fnmatch` compiles `*` to `.*`, which happily crosses `/`; setuptools
    globs a directory at a time and never does. Comparing segment counts
    first, then each segment on its own, is that difference spelled out --
    without it, `subjects/*.mjs` silently claims to cover a file nested one
    directory deeper that no wheel will ever contain.
    """
    parts = relative.split("/")
    pattern = glob.split("/")
    if len(parts) != len(pattern):
        return False
    return all(fnmatch.fnmatch(p, g) for p, g in zip(parts, pattern))


def _yaml_job(text: str, name: str) -> str:
    """Extract one two-space GitHub Actions job without accepting a substring."""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line == f"  {name}:"),
        None,
    )
    if start is None:
        raise ValueError(f"workflow has no {name!r} job")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if re.fullmatch(r"  [A-Za-z0-9_-]+:", line):
            end = index
            break
    return "\n".join(lines[start:end])


def _yaml_named_steps(job: str, name: str) -> list[dict[str, str]]:
    """Read named step keys from the small workflow subset this repository owns."""
    lines = job.splitlines()
    starts = [
        index for index, line in enumerate(lines)
        if line == f"      - name: {name}"
    ]
    steps: list[dict[str, str]] = []
    for start in starts:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if lines[index].startswith("      - "):
                end = index
                break
        fields = {"name": name}
        for line in lines[start + 1:end]:
            match = re.fullmatch(
                r"        ([A-Za-z0-9_-]+)\s*:(?:\s+(.*))?", line
            )
            if match:
                fields[match.group(1)] = match.group(2) or ""
        steps.append(fields)
    return steps


def _shell_fences(markdown: str) -> list[list[str]]:
    """Return literal lines from fenced ``sh`` blocks only."""
    fences: list[list[str]] = []
    current: list[str] | None = None
    in_comment = False
    for raw_line in markdown.splitlines():
        line = raw_line
        if current is None:
            if in_comment:
                if "-->" not in line:
                    continue
                line = line.split("-->", 1)[1]
                in_comment = False
            while "<!--" in line:
                before, after = line.split("<!--", 1)
                if "-->" in after:
                    line = before + after.split("-->", 1)[1]
                else:
                    line = before
                    in_comment = True
                    break
            if line == "```sh":
                current = []
        elif line == "```":
            fences.append(current)
            current = None
        else:
            current.append(line)
    if current is not None:
        raise ValueError("unterminated sh fence")
    return fences


def _source_gate_errors(workflow: str, releasing: str, command: str) -> list[str]:
    """Explain why either claimed source gate would not execute the suite."""
    errors: list[str] = []
    if workflow.splitlines().count("  test:") != 1:
        errors.append("workflow has no unique test job")
    try:
        job = _yaml_job(workflow, "test")
    except ValueError as error:
        return [str(error)]
    os_line = "        os: [ubuntu-latest, macos-latest]"
    python_line = '        python-version: ["3.11", "3.12", "3.13", "3.14"]'
    if (
        job.splitlines().count(os_line) != 1
        or job.splitlines().count(python_line) != 1
        or len(re.findall(r"^        os\s*:", job, flags=re.MULTILINE)) != 1
        or len(re.findall(
            r"^        python-version\s*:", job, flags=re.MULTILINE
        )) != 1
        or job.splitlines().count("    runs-on: ${{ matrix.os }}") != 1
    ):
        errors.append("source gate is not attached to the supported matrix")
    if re.search(r"^    continue-on-error\s*:", job, flags=re.MULTILINE):
        errors.append("source matrix job forgives failure")
    if re.search(r"^    if\s*:", job, flags=re.MULTILINE):
        errors.append("source matrix job is conditional")
    if re.search(r"^    needs\s*:", job, flags=re.MULTILINE):
        errors.append("source matrix job depends on another job")
    if re.search(
        r"^        (?:include|exclude)\s*:", job, flags=re.MULTILINE
    ):
        errors.append("source matrix changes the declared compatibility cells")
    steps = _yaml_named_steps(job, "Run offline subject adapter suite")
    if len(steps) != 1:
        errors.append("source matrix has no unique offline subject step")
    else:
        step = steps[0]
        if step.get("run") != command:
            errors.append("offline subject step does not run the exact command")
        if "if" in step:
            errors.append("offline subject step is conditional")
        if step.get("continue-on-error") is not None:
            errors.append("offline subject step forgives failure")

    try:
        section = releasing.split(
            "## 2. Run the source and artifact gate", 1
        )[1].split("\n## 3. ", 1)[0]
        fences = _shell_fences(section)
    except (IndexError, ValueError) as error:
        errors.append(f"release source gate cannot be parsed: {error}")
    else:
        # A one-line shell fence is executable by construction. Requiring the
        # gate to own its fence removes shell reachability from this proof:
        # there is nowhere to hide an `if`, function, heredoc, continuation,
        # short-circuit, subshell, or prior `exit`.
        occurrences = fences.count([command])
        if occurrences != 1:
            errors.append("release source gate lacks one executable command line")
    return errors

import harness_workbench  # noqa: E402
import normalize_sdist  # noqa: E402
import release_checksums  # noqa: E402
import verify_release_artifacts  # noqa: E402
import verify_installed_artifact  # noqa: E402
import verify_release_tag  # noqa: E402


class TestReleaseVersion(unittest.TestCase):
    def test_candidate_version_and_public_tag_are_exact(self):
        self.assertEqual("0.1.0rc2", harness_workbench.__version__)
        self.assertEqual("0.1.0rc2", verify_release_tag.source_version())
        verify_release_tag.verify("v0.1.0-rc.2", harness_workbench.__version__)

    def test_final_version_and_tag_policy_is_explicit(self):
        self.assertEqual(
            "v0.1.0",
            verify_release_tag.tag_for_package_version("0.1.0"),
        )
        self.assertEqual(
            "0.1.0",
            verify_release_tag.package_version_for_tag("v0.1.0"),
        )

    def test_mismatched_or_noncanonical_tags_are_rejected(self):
        rejected = ("v0.1.0", "0.1.0-rc.2", "v0.1.0rc2", "v0.1.0-rc.02",
                    "v0.1.0-rc.1")
        for tag in rejected:
            with self.subTest(tag=tag), self.assertRaises(verify_release_tag.TagError):
                verify_release_tag.verify(tag, "0.1.0rc2")

    def test_cli_reports_the_source_version(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "harness_workbench", "--version"],
            cwd=ROOT,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        self.assertEqual("hwb 0.1.0rc2\n", completed.stdout)

    def test_issue_template_version_placeholders_are_current(self):
        """A reader-facing example version must not name a superseded candidate.

        These two placeholders sat at `0.1.0rc1` through the bump because
        nothing looked at them: they are the version written where a grep for
        release surfaces does not think to go, and the only cost of being wrong
        is that every bug report is seeded with a version that no longer exists.

        The vacuity guard is per file. One counter across every template passes
        while a template that lost its placeholder line entirely -- the exact
        drift this exists to catch -- hides behind a sibling that still has one.
        Which templates must carry a placeholder is derived rather than listed:
        a template with a `version` field is asking the reporter for a version,
        so it has to show them a current one.
        """
        templates = sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
        self.assertTrue(templates, "no issue templates found")
        seeding = [
            template for template in templates
            if "id: version" in template.read_text(encoding="utf-8")
        ]
        self.assertTrue(seeding, "no template collects a version; check drifted")
        for template in seeding:
            checked = 0
            for line in template.read_text(encoding="utf-8").splitlines():
                if "placeholder:" not in line or "hwb " not in line:
                    continue
                with self.subTest(template=template.name):
                    self.assertIn(
                        f"hwb {harness_workbench.__version__}",
                        line,
                        f"{template.name} seeds reports with a stale version",
                    )
                checked += 1
            with self.subTest(template=template.name):
                self.assertTrue(
                    checked,
                    f"{template.name} has no `placeholder: hwb ...` line; the "
                    "version check is vacuous for this file",
                )


class TestReleaseChecksums(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hwb-checksums-")
        self.dist = Path(self.temp.name)
        (self.dist / "harness_workbench-0.1.0rc1-py3-none-any.whl").write_bytes(b"wheel")
        (self.dist / "harness_workbench-0.1.0rc1.tar.gz").write_bytes(b"sdist")

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_round_trip_and_tamper_rejection(self):
        manifest = release_checksums.write(self.dist)
        self.assertEqual(manifest, release_checksums.check(self.dist))
        wheel = next(self.dist.glob("*.whl"))
        wheel.write_bytes(b"changed")
        with self.assertRaises(release_checksums.ChecksumError):
            release_checksums.check(self.dist)

    def test_extra_artifacts_are_rejected(self):
        (self.dist / "duplicate.whl").write_bytes(b"other")
        with self.assertRaises(release_checksums.ChecksumError):
            release_checksums.write(self.dist)


class TestInstalledArtifactSubjectGate(unittest.TestCase):
    def test_sdist_is_built_offline_with_the_pinned_caller_backend(self):
        release_requirements = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["optional-dependencies"]["release"]
        self.assertIn(
            "setuptools==" + verify_installed_artifact.PINNED_BUILD_BACKEND,
            release_requirements,
        )
        with tempfile.TemporaryDirectory(prefix="hwb-sdist-install-") as raw:
            work = Path(raw)
            artifact = work / "harness_workbench-0.1.0rc2.tar.gz"
            artifact.write_bytes(b"sdist")
            env = {"PYTHONNOUSERSITE": "1"}

            def build(argv, *, cwd, env):
                wheelhouse = Path(argv[argv.index("--wheel-dir") + 1])
                (wheelhouse / "harness_workbench-0.1.0rc2-py3-none-any.whl").write_bytes(
                    b"wheel"
                )
                return mock.Mock(stdout="")

            with (
                mock.patch.object(
                    verify_installed_artifact.importlib.metadata,
                    "version",
                    return_value=verify_installed_artifact.PINNED_BUILD_BACKEND,
                ),
                mock.patch.object(
                    verify_installed_artifact, "run", side_effect=build
                ) as run,
            ):
                wheel = verify_installed_artifact.prepare_installable_artifact(
                    artifact, work, env
                )

            argv = run.call_args.args[0]
            self.assertEqual(sys.executable, argv[0])
            self.assertIn("--no-index", argv)
            self.assertIn("--no-deps", argv)
            self.assertIn("--no-build-isolation", argv)
            self.assertEqual("1", run.call_args.kwargs["env"]["PIP_NO_INDEX"])
            self.assertEqual(".whl", wheel.suffix)

    def test_sdist_rejects_an_unpinned_caller_backend(self):
        with tempfile.TemporaryDirectory(prefix="hwb-sdist-install-") as raw:
            work = Path(raw)
            artifact = work / "harness_workbench-0.1.0rc2.tar.gz"
            artifact.write_bytes(b"sdist")
            with mock.patch.object(
                verify_installed_artifact.importlib.metadata,
                "version",
                return_value="84.0.0",
            ):
                with self.assertRaisesRegex(SystemExit, "requires setuptools"):
                    verify_installed_artifact.prepare_installable_artifact(
                        artifact, work, {}
                    )

    def test_clean_install_materializes_and_runs_the_offline_subject_suite(self):
        with tempfile.TemporaryDirectory(prefix="hwb-installed-subjects-") as raw:
            work = Path(raw)
            env = {"PYTHONNOUSERSITE": "1"}
            with mock.patch.object(verify_installed_artifact, "run") as run:
                verify_installed_artifact.verify_materialized_subjects(
                    "/venv/bin/hwb", "/venv/bin/python", work, env
                )

            destination = work / "subjects"
            self.assertEqual(2, run.call_count)
            self.assertEqual(
                [
                    "/venv/bin/hwb", "subjects", "--into", str(destination)
                ],
                run.call_args_list[0].args[0],
            )
            self.assertEqual(
                [
                    "/venv/bin/python",
                    "-m", "unittest", "discover",
                    "-s", str(destination),
                    "-p", "test_experiment.py",
                    "-v",
                ],
                run.call_args_list[1].args[0],
            )
            for call in run.call_args_list:
                self.assertEqual(work, call.kwargs["cwd"])
                self.assertIs(env, call.kwargs["env"])


class TestSourceDistributionPrivacy(unittest.TestCase):
    EPOCH = 1_755_000_000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hwb-sdist-privacy-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self, name: str, members: list[tarfile.TarInfo]) -> Path:
        path = self.root / name
        with path.open("wb") as destination:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=destination,
                mtime=self.EPOCH,
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for member in members:
                        body = b"payload\n" if member.isfile() else None
                        if body is not None:
                            member.size = len(body)
                        archive.addfile(
                            member,
                            io.BytesIO(body) if body is not None else None,
                        )
        return path

    def member(
        self,
        name: str,
        *,
        uid: int = 0,
        gid: int = 0,
        uname: str = "root",
        gname: str = "root",
        mtime: int | None = None,
    ) -> tarfile.TarInfo:
        member = tarfile.TarInfo(name)
        member.mode = 0o644
        member.uid = uid
        member.gid = gid
        member.uname = uname
        member.gname = gname
        member.mtime = self.EPOCH if mtime is None else mtime
        return member

    def test_verifier_rejects_local_identity_metadata(self):
        leaking = self.write_archive(
            "leaking.tar.gz",
            [
                self.member(
                    "harness-workbench/file.txt",
                    uid=501,
                    gid=20,
                    uname="local-user",
                    gname="local-group",
                )
            ],
        )
        with self.assertRaisesRegex(SystemExit, "non-neutral numeric ownership"):
            verify_release_artifacts.check_sdist_archive_safety(
                leaking, self.EPOCH
            )

    def test_normalization_removes_identity_and_is_byte_reproducible(self):
        directory = self.member("harness-workbench/subdir")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        executable = self.member(
            "harness-workbench/file.txt",
            uid=501,
            gid=20,
            uname="local-user",
            gname="local-group",
            mtime=123,
        )
        executable.mode = 0o751
        symlink = self.member("harness-workbench/link")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "file.txt"
        hardlink = self.member("harness-workbench/hardlink")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "harness-workbench/file.txt"
        raw = self.write_archive(
            "raw.tar.gz",
            [directory, executable, symlink, hardlink],
        )
        first = self.root / "first.tar.gz"
        second = self.root / "second.tar.gz"
        normalize_sdist.normalize(raw, first, self.EPOCH)
        normalize_sdist.normalize(raw, second, self.EPOCH)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        verify_release_artifacts.check_sdist_archive_safety(first, self.EPOCH)
        with tarfile.open(first, "r:gz") as archive:
            for member in archive.getmembers():
                self.assertEqual((0, 0), (member.uid, member.gid))
                self.assertEqual(("root", "root"), (member.uname, member.gname))
                self.assertEqual(self.EPOCH, member.mtime)
            members = {member.name: member for member in archive.getmembers()}
            # The executable bit survives because Git tracks it; the rest of
            # the mode does not, because the rest of the mode is the builder's
            # umask and nothing else.
            self.assertEqual(0o755, members["harness-workbench/file.txt"].mode)
            self.assertEqual(0o755, members["harness-workbench/subdir"].mode)
            self.assertTrue(members["harness-workbench/subdir"].isdir())
            self.assertEqual("file.txt", members["harness-workbench/link"].linkname)
            self.assertTrue(members["harness-workbench/link"].issym())
            self.assertEqual(
                "harness-workbench/file.txt",
                members["harness-workbench/hardlink"].linkname,
            )
            self.assertTrue(members["harness-workbench/hardlink"].islnk())

    def test_normalization_does_not_carry_the_builders_umask(self):
        """The same tree built under two umasks must normalize to one archive.

        Ownership and timestamps were neutralized but modes were not, so the
        sdist was a function of the builder's umask: `umask 077` and
        `umask 022` produced different bytes for one commit. Step 3 of
        RELEASING.md compares the offline build against a rebuild from the
        GitHub clone and requires byte identity, so that difference surfaced
        there as "the build is not reproducible" -- a true statement with a
        cause nobody would find, and a gate that fails benignly gets disabled.
        """
        archives = {}
        for umask_bits, permissive in ((0o022, 0o644), (0o077, 0o600)):
            directory = self.member(f"harness-workbench/subdir{umask_bits}")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o777 & ~umask_bits
            plain = self.member(f"harness-workbench/plain{umask_bits}.txt")
            plain.mode = permissive
            script = self.member(f"harness-workbench/script{umask_bits}.sh")
            script.mode = 0o777 & ~umask_bits
            raw = self.write_archive(
                f"raw{umask_bits}.tar.gz", [directory, plain, script]
            )
            out = self.root / f"norm{umask_bits}.tar.gz"
            normalize_sdist.normalize(raw, out, self.EPOCH)
            with tarfile.open(out, "r:gz") as archive:
                archives[umask_bits] = {
                    member.name.rsplit("/", 1)[-1].replace(str(umask_bits), ""): (
                        member.mode
                    )
                    for member in archive.getmembers()
                }

        self.assertEqual(
            archives[0o022],
            archives[0o077],
            "normalized member modes still depend on the builder's umask",
        )
        self.assertEqual(
            {"subdir": 0o755, "plain.txt": 0o644, "script.sh": 0o755},
            archives[0o022],
        )

    def test_umask_leaked_member_modes_are_rejected(self):
        """The allowed modes are a constant, so both directions of drift fail.

        `0o600` is what `umask 077` leaves behind and `0o664` is what a
        group-writable checkout leaves behind. Neither is what Git records, and
        an sdist carrying either is a function of the machine that built it.
        """
        for mode in (0o600, 0o664, 0o700, 0o777):
            member = self.member("harness-workbench/file.txt")
            member.mode = mode
            archive = self.write_archive(f"mode{mode:04o}.tar.gz", [member])
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(
                SystemExit, "non-neutral mode"
            ):
                verify_release_artifacts.check_sdist_archive_safety(
                    archive, self.EPOCH
                )

    def test_unsafe_member_paths_and_links_are_rejected(self):
        traversal = self.write_archive(
            "traversal.tar.gz",
            [self.member("harness-workbench/../private.txt")],
        )
        link = self.member("harness-workbench/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../private.txt"
        unsafe_link = self.write_archive("unsafe-link.tar.gz", [link])

        for archive in (traversal, unsafe_link):
            with self.subTest(archive=archive.name), self.assertRaises(SystemExit):
                verify_release_artifacts.check_sdist_archive_safety(
                    archive, self.EPOCH
                )

    def test_normalizing_sdist_does_not_modify_wheel(self):
        raw = self.write_archive(
            "raw.tar.gz",
            [self.member("harness-workbench/file.txt", uid=501, uname="local-user")],
        )
        wheel = self.root / "harness_workbench-0.1.0rc1-py3-none-any.whl"
        wheel.write_bytes(b"unchanged wheel bytes")

        normalize_sdist.normalize(raw, self.root / "normalized.tar.gz", self.EPOCH)

        self.assertEqual(b"unchanged wheel bytes", wheel.read_bytes())


class TestArchiveExecutableAgreement(unittest.TestCase):
    """Which members may be executable is decided by the source, not the archive.

    The check this replaces asked the member's own mode what its mode should
    have been -- `0755` if the executable bit is set, `0644` otherwise -- which
    every archive satisfies by construction. Rewriting every regular member of
    a real sdist to `0755` passed it, and so did stripping the executable bit
    off the one shipped script that needs it. The expectation now comes from
    `st_mode & 0o100` on the source file, the single permission bit Git
    records, so the two can disagree and be caught disagreeing.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hwb-exec-set-")
        self.source = Path(self.temp.name)
        (self.source / "script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.source / "script.sh").chmod(0o755)
        (self.source / "notes.md").write_text("prose\n", encoding="utf-8")
        (self.source / "notes.md").chmod(0o644)

    def tearDown(self):
        self.temp.cleanup()

    def entries(self, script_executable=True, notes_executable=False,
                generated_executable=False):
        return [
            ("root/script.sh", script_executable, PurePosixPath("script.sh")),
            ("root/notes.md", notes_executable, PurePosixPath("notes.md")),
            ("root/PKG-INFO", generated_executable, None),
        ]

    def check(self, entries):
        verify_release_artifacts.check_executable_set(
            entries, self.source, "sdist"
        )

    def test_an_archive_agreeing_with_the_source_tree_passes(self):
        self.check(self.entries())

    def test_an_executable_the_source_does_not_mark_is_rejected(self):
        with self.assertRaisesRegex(
            SystemExit, r"executable in the archive but not in the source tree"
        ):
            self.check(self.entries(notes_executable=True))

    def test_a_source_executable_the_archive_lost_is_rejected(self):
        with self.assertRaisesRegex(
            SystemExit, r"executable in the source tree but not in the archive"
        ):
            self.check(self.entries(script_executable=False))

    def test_generated_members_have_no_source_and_must_not_be_executable(self):
        with self.assertRaisesRegex(
            SystemExit, r"archive but not in the source tree.*PKG-INFO"
        ):
            self.check(self.entries(generated_executable=True))

    def test_both_directions_are_reported_together(self):
        with self.assertRaises(SystemExit) as raised:
            self.check(
                self.entries(script_executable=False, notes_executable=True)
            )
        message = str(raised.exception)
        self.assertIn("executable in the archive but not in the source tree", message)
        self.assertIn("executable in the source tree but not in the archive", message)

    def test_members_resolve_to_the_source_files_they_were_built_from(self):
        """The mapping is the other half: a wrong path expects nothing.

        Every member whose source counterpart cannot be found is expected to be
        non-executable, so a mapping that silently misses would turn the whole
        check back into "nothing may be executable" -- which the real shipped
        scripts would then fail loudly, but only for shipped scripts.
        """
        self.assertEqual(
            PurePosixPath("src/harness_workbench/subjects/run_subject.sh"),
            verify_release_artifacts.wheel_source_relpath(
                "harness_workbench/subjects/run_subject.sh"
            ),
        )
        self.assertIsNone(
            verify_release_artifacts.wheel_source_relpath(
                "harness_workbench-0.1.0rc2.dist-info/RECORD"
            )
        )
        self.assertEqual(
            PurePosixPath("examples/echo.sh"),
            verify_release_artifacts.sdist_source_relpath(
                "harness_workbench-0.1.0rc2",
                "harness_workbench-0.1.0rc2/examples/echo.sh",
            ),
        )
        self.assertIsNone(
            verify_release_artifacts.sdist_source_relpath(
                "harness_workbench-0.1.0rc2", "harness_workbench-0.1.0rc2"
            )
        )
        # The source files these map onto really are marked the way the
        # verifier will read them, so the mapping is checked against the same
        # tree the gate runs on rather than against a fixture only.
        self.assertTrue(
            (ROOT / "src/harness_workbench/subjects/run_subject.sh").stat().st_mode
            & 0o100
        )
        self.assertFalse((ROOT / "README.md").stat().st_mode & 0o100)


class TestReleaseSurfaces(unittest.TestCase):
    def test_each_experiment_directory_has_a_learning_record(self):
        experiment_root = ROOT / "experiments"
        if not experiment_root.is_dir():
            self.skipTest(
                "local experiments are not part of the source distribution"
            )
        experiment_directories = sorted(
            path
            for path in experiment_root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
        self.assertTrue(experiment_directories)
        for directory in experiment_directories:
            with self.subTest(experiment=directory.name):
                self.assertTrue((directory / "README.md").is_file())
                learning_record = directory / "LEARNINGS.md"
                self.assertTrue(learning_record.is_file())
                text = learning_record.read_text(encoding="utf-8")
                for required in (
                    "**Question.**",
                    "**Evidence.**",
                    "**Learned.**",
                    "**Code consequence.**",
                    "**Limits.**",
                    "**Next.**",
                ):
                    self.assertIn(required, text)

    def test_every_subject_tree_file_is_covered_by_a_package_data_glob(self):
        """A file in the tree that no glob matches ships in git and nowhere else.

        This is the mechanised form of a rule nobody could have kept. The
        package-data list enumerated the languages the subject tree happened to
        contain the day it was written, so `guard_extension.ts` sat in the
        repository and was absent from every built wheel. Nothing caught it
        because `subject_tree.subject_files()` walks the SOURCE checkout, which
        always looks complete. Matching the two against each other is the only
        check that stays true when someone adds an interceptor in a language
        this list has never seen.
        """
        with (ROOT / "pyproject.toml").open("rb") as stream:
            data = tomllib.load(stream)
        globs = data["tool"]["setuptools"]["package-data"]["harness_workbench"]
        subject_globs = [g for g in globs if g.startswith("subjects/")]
        # Tracked files, not everything on disk. A freeze lock is generated
        # beside the spec it locks and is gitignored on purpose -- it is a
        # local artefact of a run, not source, and must NOT ship. Asking git
        # what is source keeps that distinction where it is already declared
        # instead of restating it as a second exclusion list here.
        listing = subprocess.run(
            ["git", "ls-files", "src/harness_workbench/subjects"],
            cwd=ROOT, capture_output=True, text=True,
        )
        if listing.returncode != 0:
            self.skipTest("not a git checkout")
        uncovered = []
        for line in listing.stdout.splitlines():
            if not line.strip():
                continue
            relative = line.split("src/harness_workbench/", 1)[1]
            if not any(_glob_matches(relative, glob) for glob in subject_globs):
                uncovered.append(relative)
        self.assertEqual([], uncovered)

    def test_the_coverage_check_does_not_match_across_directories(self):
        """`subjects/*.mjs` must not be read as covering `subjects/n/x.mjs`.

        The obvious spelling of the check above is `fnmatch`, and it is wrong
        in a way that restores the exact bug it was written to catch: fnmatch
        translates `*` to `.*`, which crosses `/`, while setuptools' own
        package-data globbing does not. So a tracked
        `subjects/interceptors/guard.mjs` would satisfy `subjects/*.mjs`, the
        check would pass, and the wheel would ship without it -- one directory
        further down than `guard_extension.ts` was.

        A control nobody inverts is a control nobody tested, so this asserts
        the segment rule directly rather than trusting the helper's name.
        """
        self.assertTrue(_glob_matches("subjects/guard_plugin.mjs", "subjects/*.mjs"))
        self.assertTrue(
            _glob_matches("subjects/repair_fixture/a.py", "subjects/repair_fixture/*.py")
        )
        self.assertFalse(_glob_matches("subjects/nested/guard.mjs", "subjects/*.mjs"))
        self.assertFalse(_glob_matches("subjects/a/b/c.py", "subjects/*.py"))

    def test_the_capture_run_root_cannot_be_committed(self):
        """`capture()` builds its per-run root inside the subject tree.

        It is created with `dir=HERE`, and the Codex guard arm copies
        `~/.codex/auth.json` into it -- an isolated CODEX_HOME authenticates as
        nobody otherwise. `TemporaryDirectory` removes it on any ordinary exit,
        but nothing in Python runs after a SIGKILL, and an OOM or a spend limit
        killing a run mid-flight leaves that credential in the working tree
        where the next `git add -A` would stage it.

        The ignore rule is the only link in that chain that does not depend on
        the process surviving, so it is asserted rather than trusted.
        """
        probe = "src/harness_workbench/subjects/.hwb-codex-probe/codex-home/auth.json"
        decision = subprocess.run(
            ["git", "check-ignore", "-q", probe],
            cwd=ROOT, capture_output=True, text=True,
        )
        if decision.returncode not in (0, 1):
            self.skipTest("not a git checkout")
        self.assertEqual(
            0, decision.returncode,
            "capture()'s .hwb-* run root is not gitignored; a killed Codex "
            "guard run would leave a copied credential stageable",
        )

    def test_a_materialized_subject_tree_ignores_interrupted_run_roots(self):
        """The repository's root ignore file does not travel with the tree."""
        from harness_workbench import subject_tree

        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory) / "subjects"
            tree.mkdir()
            subject_tree.materialize(str(tree))
            initialized = subprocess.run(
                ["git", "init", "-q"], cwd=tree, capture_output=True, text=True
            )
            if initialized.returncode != 0:
                self.skipTest("git is unavailable")
            credential = tree / ".hwb-codex-probe" / "codex-home" / "auth.json"
            credential.parent.mkdir(parents=True)
            credential.write_text('{"access_token":"must-not-stage"}\n', encoding="utf-8")
            decision = subprocess.run(
                ["git", "check-ignore", "-q", credential.relative_to(tree)],
                cwd=tree,
                capture_output=True,
                text=True,
            )
        self.assertEqual(
            0,
            decision.returncode,
            "materialized subject tree can stage a killed run's copied auth.json",
        )

    def test_public_identity_and_minimal_verification_provenance_are_explicit(self):
        with (ROOT / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)["project"]
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        record = (ROOT / "docs" / "release-conformance-0.1.0rc2.md").read_text(
            encoding="utf-8"
        )

        copyright_name = re.search(
            r"^Copyright \d{4} (.+)$", notice, re.MULTILINE
        )
        self.assertIsNotNone(copyright_name)
        expected_identity = [{"name": copyright_name.group(1)}]
        self.assertEqual(expected_identity, project["authors"])
        self.assertEqual(expected_identity, project["maintainers"])
        self.assertIn(
            "Approved 2026-08-12: Garrett Davis is intentionally public as "
            "copyright holder, "
            "package author, maintainer, and Git identity associated with "
            "Explore Failure",
            record,
        )
        self.assertIn(
            "existing GitHub account association in the reviewed history is "
            "intentional",
            record,
        )
        self.assertIn(
            "maintainer-side author-context verification on 2026-08-22",
            record,
        )
        self.assertIn("macOS/arm64 with CPython 3.11", record)

        for private_machine_detail in (
            r"\bmacOS\s+\d+\.\d+",
            r"\bDarwin\s+`?\d+\.\d+\.\d+",
            r"\bCPython\s+3\.(?:11|12|13|14)\.\d+",
        ):
            with self.subTest(pattern=private_machine_detail):
                self.assertNotRegex(record, private_machine_detail)
        self.assertNotIn("Codex", record)
        self.assertNotIn("release-preparation agent", record)

    def test_candidate_publication_status_is_current(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        record = (ROOT / "docs" / "release-conformance-0.1.0rc2.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("public GitHub prerelease", readme)
        self.assertIn(
            "releases/tag/v0.1.0-rc.1",
            readme,
        )
        self.assertIn("not published to PyPI", readme)
        self.assertNotIn("has not been tagged, released, or made public", readme)
        self.assertIn("0.1.0rc1 — 2026-08-12", changelog)
        self.assertIn("published as the public GitHub prerelease", changelog)
        self.assertIn("not published to PyPI", changelog)
        self.assertNotIn("Published releases\n\nNone yet", changelog)
        # The repository-owned record is intentionally the pre-publication
        # source record; the release-final record is a GitHub release asset.
        self.assertIn("Prepared candidate record — NOT RELEASED", record)
        self.assertIn(
            "f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3",
            record,
        )
        self.assertIn(
            "https://github.com/explorefailure/harness-workbench/actions/runs/32604245910",
            record,
        )
        self.assertIn(
            "https://github.com/explorefailure/harness-workbench/actions/runs/32604245892",
            record,
        )
        self.assertIn("zero open code-scanning alerts", record)
        self.assertIn("Release commit: **PENDING**", record)
        self.assertIn("Signed tag and verification: **PENDING**", record)

    def test_live_subject_prerequisites_follow_the_active_profile(self):
        selection = json.loads(
            (ROOT / "src" / "harness_workbench" / "subjects"
             / "model_selection.json").read_text(encoding="utf-8")
        )
        self.assertEqual("opencode-go", selection["active"])
        self.assertEqual("gateway", selection["profiles"]["opencode-go"]["kind"])

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        subject_readme = (
            ROOT / "src" / "harness_workbench" / "subjects" / "README.md"
        ).read_text(encoding="utf-8")
        for surface in (readme, subject_readme):
            with self.subTest(surface="root" if surface is readme else "subjects"):
                self.assertIn("`opencode-go`", surface)
                self.assertIn("`HWB_OPENCODE_KEY`", surface)
                self.assertIn("outbound network", surface)
                self.assertRegex(surface, r"paid|spend")
                self.assertIn("Hermes", surface)
                self.assertIn("`local-ollama`", surface)
        self.assertNotIn(
            "placeholder key-shaped value required by that provider profile",
            subject_readme,
        )
        self.assertNotIn(
            "Hermes uses the same pinned local Ollama model",
            subject_readme,
        )

    def test_capture_provenance_distinguishes_rc1_tag_from_development(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        record = (ROOT / "docs" / "release-conformance-0.1.0rc2.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("added after the published `0.1.0rc1` tag", changelog)
        self.assertIn("published rc1 artefacts do not contain it", changelog)
        self.assertNotIn(
            "`capture` reached the published `0.1.0rc1` candidate",
            changelog,
        )
        self.assertIn("That tag does not contain `capture.py`", record)
        self.assertNotIn("byte-identical across `0.1.0rc1`", record)
        self.assertNotIn("which does not exist yet", record)

    def test_ci_tag_check_is_read_only_and_tag_only(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("startsWith(github.ref, 'refs/tags/')", workflow)
        self.assertIn(
            'python tools/verify_release_tag.py "$GITHUB_REF_NAME"', workflow
        )

    def test_offline_subject_suite_is_in_each_source_gate(self):
        """The shipped adapter tree must travel with the supported source matrix.

        The subject tests deliberately live beside the materialized subject tree,
        outside the repository suite's discovery root. Checking the whole workflow
        would let the command drift into the one-off package job while every
        OS/Python compatibility cell stopped running it, so this reads only the
        matrix job. The maintainer procedure is scoped to its offline source gate
        for the same reason: a later example is not release protection.
        """
        command = (
            "PYTHONPATH=src python -m unittest discover "
            "-s src/harness_workbench/subjects -p 'test_experiment.py' -v"
        )
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
        self.assertEqual(
            [],
            _source_gate_errors(workflow, releasing, command),
            "both source gates must execute the exact suite without conditions",
        )

        mutations = (
            (
                "duplicate test job override",
                workflow.replace(
                    "\n  package:",
                    "\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n\n  package:",
                    1,
                ),
                releasing,
            ),
            (
                "duplicate empty OS matrix key",
                workflow.replace(
                    "        os: [ubuntu-latest, macos-latest]",
                    "        os: [ubuntu-latest, macos-latest]\n        os: []",
                    1,
                ),
                releasing,
            ),
            (
                "disabled CI step",
                workflow.replace(
                    "      - name: Run offline subject adapter suite\n"
                    f"        run: {command}",
                    "      - name: Run offline subject adapter suite\n"
                    "        if: ${{ false }}\n"
                    f"        run: {command}",
                ),
                releasing,
            ),
            (
                "forgiven CI step",
                workflow.replace(
                    f"        run: {command}",
                    f"        run: {command}\n        continue-on-error: true",
                    1,
                ),
                releasing,
            ),
            (
                "disabled CI step with spaced key",
                workflow.replace(
                    "      - name: Run offline subject adapter suite\n"
                    f"        run: {command}",
                    "      - name: Run offline subject adapter suite\n"
                    "        if : ${{ false }}\n"
                    f"        run: {command}",
                ),
                releasing,
            ),
            (
                "disabled CI job",
                workflow.replace(
                    "  test:\n",
                    "  test:\n    if: ${{ false }}\n",
                    1,
                ),
                releasing,
            ),
            (
                "disabled CI job with spaced key",
                workflow.replace(
                    "  test:\n",
                    "  test:\n    if : ${{ false }}\n",
                    1,
                ),
                releasing,
            ),
            (
                "skipped CI prerequisite",
                workflow.replace(
                    "  test:\n",
                    "  test:\n    needs: disabled-prerequisite\n",
                    1,
                ),
                releasing,
            ),
            (
                "excluded CI matrix",
                workflow.replace(
                    '        python-version: ["3.11", "3.12", "3.13", "3.14"]',
                    '        python-version: ["3.11", "3.12", "3.13", "3.14"]\n'
                    "        exclude:\n"
                    "          - os: ubuntu-latest\n"
                    '            python-version: "3.11"',
                    1,
                ),
                releasing,
            ),
            (
                "commented release command",
                workflow,
                releasing.replace(command, f"# {command}", 1),
            ),
            (
                "HTML-commented release fence",
                workflow,
                releasing.replace(
                    f"```sh\n{command}\n```",
                    f"<!--\n```sh\n{command}\n```\n-->",
                    1,
                ),
            ),
            (
                "unreachable release command",
                workflow,
                releasing.replace(
                    command,
                    f"if false; then\n  {command}\nfi",
                    1,
                ),
            ),
            (
                "multiline unreachable release command",
                workflow,
                releasing.replace(
                    command,
                    f"if false\nthen\n  {command}\nfi",
                    1,
                ),
            ),
            (
                "continued unreachable release command",
                workflow,
                releasing.replace(
                    command,
                    f"false && \\\n{command}",
                    1,
                ),
            ),
            (
                "uninvoked release function",
                workflow,
                releasing.replace(
                    command,
                    f"offline_gate() {{\n{command}\n}}",
                    1,
                ),
            ),
            (
                "hyphenated uninvoked release function",
                workflow,
                releasing.replace(
                    command,
                    f"offline-gate() {{\n{command}\n}}",
                    1,
                ),
            ),
            (
                "release heredoc body",
                workflow,
                releasing.replace(
                    command,
                    f"cat <<'GATE'\n{command}\nGATE",
                    1,
                ),
            ),
            (
                "numeric release heredoc body",
                workflow,
                releasing.replace(
                    command,
                    f"cat <<'123'\n{command}\n123",
                    1,
                ),
            ),
            (
                "short-circuited release group",
                workflow,
                releasing.replace(
                    command,
                    f"false && (\n{command}\n)",
                    1,
                ),
            ),
            (
                "release command after exit",
                workflow,
                releasing.replace(
                    command,
                    f"exit 0\n{command}",
                    1,
                ),
            ),
        )
        for name, mutated_workflow, mutated_releasing in mutations:
            with self.subTest(mutation=name):
                self.assertTrue(
                    _source_gate_errors(
                        mutated_workflow, mutated_releasing, command
                    ),
                    f"{name} bypassed the source-gate proof",
                )

    def test_evergreen_subject_commands_do_not_freeze_test_counts(self):
        """Adding a regression test must not make the usage docs false."""
        surfaces = (
            ROOT / "README.md",
            ROOT / "src" / "harness_workbench" / "subjects" / "README.md",
        )
        for path in surfaces:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotRegex(
                    text,
                    r"unittest[^\n]*#\s*\d+\s+tests",
                    "evergreen commands must not carry moving suite counts",
                )
                self.assertIn("# offline; no subject installed", text)

    def test_shared_contract_counts_all_five_subjects(self):
        contract = (
            ROOT
            / "src"
            / "harness_workbench"
            / "subjects"
            / "SHARED_ADAPTER_CONTRACT.md"
        ).read_text(encoding="utf-8")
        self.assertIn("the five sealed discovery records", contract)
        self.assertIn("Final five-subject", contract)
        self.assertNotIn("the four sealed discovery records", contract)
        self.assertNotIn("Final four-subject", contract)

    def test_execution_trust_boundary_and_replay_copy_are_explicit(self):
        required = {
            "README.md": ("not a security sandbox", "arbitrary Python code"),
            "docs/the-spec.md": ("does not contain or isolate hostile code",),
            "docs/writing-a-feature.md": ("trusted executable code",),
            "docs/measuring.md": ("not a security\nsandbox or OS isolation boundary",),
            "docs/measuring-your-own-code.md": ("trusted execution",),
            "docs/campaign-manifests.md": ("not security isolation",),
            "docs/the-record.md": ("Preservation is not containment",),
        }
        for relative, phrases in required.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for phrase in phrases:
                with self.subTest(file=relative, phrase=phrase):
                    self.assertIn(phrase, text)

        documentation = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
        )
        for misleading in (
            "Replays run in a sandbox",
            "into its sandbox",
            "isolated scratch evidence",
            "fresh isolated workload",
            "fresh isolated fixture",
        ):
            with self.subTest(misleading=misleading):
                self.assertNotIn(misleading, documentation)

    def test_security_policy_has_truthful_private_route_and_support_window(self):
        policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("No Harness Workbench version has been published yet", policy)
        self.assertIn("only the newest published release", policy)
        self.assertIn(
            "https://github.com/explorefailure/harness-workbench/security/advisories/new",
            policy,
        )
        self.assertRegex(
            policy, r"There is currently no published security email\s+address"
        )
        self.assertIn("not a hostile-code sandbox", policy)

    def test_public_project_posture_and_routes_are_explicit(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        support = (ROOT / "SUPPORT.md").read_text(encoding="utf-8")

        for link in ("CONTRIBUTING.md", "SUPPORT.md", "SECURITY.md"):
            with self.subTest(link=link):
                self.assertIn(f"]({link})", readme)
        self.assertIn("actively developed, solo maintained", readme)
        self.assertIn("best-effort\nreview", contributing)
        self.assertIn("Larger changes should start with a GitHub issue", contributing)
        self.assertIn("does not promise a response time, merge", contributing)
        self.assertIn("usage questions and non-sensitive bug reports", support)
        self.assertIn("no guaranteed\nresponse time", support)
        self.assertIn("[SECURITY.md](SECURITY.md)", support)

    def test_github_intake_routes_security_and_collects_bug_evidence(self):
        issue_root = ROOT / ".github" / "ISSUE_TEMPLATE"
        config = (issue_root / "config.yml").read_text(encoding="utf-8")
        bug = (issue_root / "bug_report.yml").read_text(encoding="utf-8")
        proposal = (issue_root / "change_proposal.yml").read_text(encoding="utf-8")
        question = (issue_root / "usage_question.yml").read_text(encoding="utf-8")
        pull_request = (ROOT / ".github" / "pull_request_template.md").read_text(
            encoding="utf-8"
        )

        private_route = (
            "https://github.com/explorefailure/harness-workbench/"
            "security/advisories/new"
        )
        self.assertIn(private_route, config)
        self.assertIn("blank_issues_enabled: false", config)
        for field_id in ("version", "python", "platform", "installation", "reproduction"):
            with self.subTest(field_id=field_id):
                self.assertIn(f"id: {field_id}", bug)
        self.assertIn("Report\n        vulnerabilities through the private route", bug)
        self.assertIn("Larger changes should begin here", proposal)
        self.assertIn("Support is best effort", question)
        self.assertIn("id: version", question)
        self.assertIn("id: environment", question)
        self.assertIn("Link the prior issue for a larger change", pull_request)
        self.assertIn("security reports follow `SECURITY.md`", pull_request)

    def test_handoff_documents_are_required_in_source_distribution(self):
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        verifier = (ROOT / "tools" / "verify_release_artifacts.py").read_text(
            encoding="utf-8"
        )
        for name in ("CONTRIBUTING.md", "SUPPORT.md"):
            with self.subTest(name=name):
                self.assertIn(f"include {name}", manifest)
                self.assertIn(f'"{name}"', verifier)

    def test_release_sdist_is_normalized_before_verification_or_upload(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for surface in (workflow, releasing):
            with self.subTest(surface=surface[:20]):
                self.assertIn("SOURCE_DATE_EPOCH", surface)
                self.assertIn("tools/normalize_sdist.py", surface)
                self.assertIn('--sdist --outdir "$RAW_SDIST_DIR"', surface)
                self.assertIn("--output-dir dist", surface)
        self.assertNotIn("python -m build --sdist --wheel", releasing)
        self.assertIn("A raw backend-built sdist must not be\nuploaded", readme)

    def test_release_toolchain_is_pinned_and_written_the_same_way_everywhere(self):
        """One toolchain, declared once, spelled identically in both procedures.

        `RELEASING.md` and the package job install literal versions rather than
        `.[release]`, because installing the project runs an in-tree build and
        leaves the `build/` directory step 2 refuses to start with. That makes
        the literals a second source of truth, so this reconstructs the install
        command from `pyproject.toml` and requires it verbatim.

        `setuptools` is in that list because it is the build backend and
        `requires` is only a floor: `python -m build` resolves a floor freshly
        per build, and two setuptools versions produced different wheel bytes
        and different member modes from one commit. A pin that the builds do
        not use is decoration, so `--no-isolation` is required too.
        """
        with (ROOT / "pyproject.toml").open("rb") as stream:
            extras = tomllib.load(stream)["project"]["optional-dependencies"]
        pins = sorted(
            requirement.replace(" ", "") for requirement in extras["release"]
        )
        for pin in pins:
            with self.subTest(pin=pin):
                self.assertRegex(
                    pin, r"^[A-Za-z0-9_.-]+==[0-9][^,;]*$",
                    "the release extra must pin exact versions, not ranges",
                )
        self.assertIn(
            "setuptools", " ".join(pins),
            "the build backend must be pinned in the release extra; a floor in "
            "[build-system].requires is resolved per build",
        )

        command = "python -m pip install --disable-pip-version-check " + " ".join(
            f"'{pin}'" for pin in pins
        )
        surfaces = {
            "RELEASING.md": 2,  # the local gate clone and the GitHub clone
            ".github/workflows/ci.yml": 1,
        }
        for relative, expected in surfaces.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            # Shell line continuations are formatting, not content.
            joined = re.sub(r"[ \t]*\\\n\s+", " ", text)
            installs = [
                line.strip() for line in joined.splitlines()
                if line.strip().startswith("python -m pip install")
            ]
            with self.subTest(surface=relative):
                self.assertEqual(
                    expected, joined.count(command),
                    f"{relative} does not install exactly the pinned release "
                    f"toolchain {pins}",
                )
                self.assertEqual(
                    [], [line for line in installs if "[release]" in line],
                    f"{relative} installs the project itself, which runs an "
                    "in-tree build and leaves a build/ directory behind",
                )
                self.assertIn("python -m build --no-isolation --wheel", joined)
                self.assertIn("python -m build --no-isolation --sdist", joined)

    def test_all_workflow_actions_are_official_and_pinned_to_full_shas(self):
        allowed = {
            "actions/checkout",
            "actions/setup-python",
            "github/codeql-action/init",
            "github/codeql-action/analyze",
        }
        uses = []
        for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            text = workflow.read_text(encoding="utf-8")
            uses.extend(
                re.findall(
                    r"^\s*uses:\s*([^@\s]+)@([^\s#]+)", text, re.MULTILINE
                )
            )
        self.assertTrue(uses)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertIn(action, allowed)
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_codeql_and_dependabot_security_configuration(self):
        codeql = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", codeql)
        self.assertIn("security-events: write", codeql)
        self.assertIn("solely so", codeql)
        self.assertNotIn("contents: write", codeql)

        dependabot = (ROOT / ".github" / "dependabot.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(1, dependabot.count("package-ecosystem: pip"))
        self.assertEqual(1, dependabot.count("package-ecosystem: github-actions"))

    def test_secret_scan_exception_is_bound_to_removed_historical_fixture(self):
        config = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
        self.assertIn("useDefault = true", config)
        self.assertEqual(1, config.count("[[allowlists]]"))
        self.assertIn('condition = "AND"', config)
        self.assertIn("fc5846dc3b3f19591ee1de1d40d48ab6e1679703", config)
        self.assertIn(r"^tests/test_workbench\.py$", config)
        self.assertIn("notakey-live-[0-9a-f]{12}", config)
        releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
        self.assertIn("GITLEAKS_VERSION=8.30.1", releasing)
        self.assertIn("--log-opts='--all'", releasing)
        self.assertIn("--max-archive-depth 2 dist", releasing)

    def test_conformance_record_routes_every_release_claim_surface(self):
        record_path = ROOT / "docs" / "release-conformance-0.1.0rc2.md"
        record = record_path.read_text(encoding="utf-8")

        # Reader-facing Markdown at the root and under docs, every shipped
        # example/demo, and GitHub's rendered workflow/intake surfaces are the
        # places a new public claim can appear without touching the package
        # implementation. Discover them instead of maintaining a second list.
        surfaces = {
            path.relative_to(ROOT).as_posix()
            for path in ROOT.glob("*.md")
        }
        surfaces.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "docs").rglob("*.md")
        )
        shipped_example_suffixes = {".json", ".md", ".py", ".sh", ".txt"}
        surfaces.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "examples").rglob("*")
            if path.is_file() and path.suffix in shipped_example_suffixes
        )
        surfaces.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / ".github").rglob("*")
            if path.is_file() and path.suffix in {".md", ".yml", ".yaml"}
        )
        surfaces.update({"LICENSE", "NOTICE", "pyproject.toml"})

        for relative in sorted(surfaces):
            with self.subTest(surface=relative):
                self.assertIn(
                    f"`{relative}`",
                    record,
                    f"claim-bearing release surface is not routed: {relative}",
                )

        from harness_workbench import commands

        for name in commands.cli_commands():
            with self.subTest(command=name):
                self.assertIn(f"`hwb {name} --help`", record)
        self.assertIn("`hwb --help`", record)
        self.assertIn("`hwb --version`", record)
        self.assertIn("`python -m harness_workbench --help`", record)
        self.assertIn("`harness_workbench.conform`", record)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
        link = "docs/release-conformance-0.1.0rc2.md"
        self.assertIn(f"]({link})", readme)
        self.assertIn(f"]({link})", releasing)

    # Modules under `src/harness_workbench/` that are implementation detail.
    # This list is the *decision*, not the discovery: a module is public until
    # somebody puts it here on purpose. Adding a module to the package without
    # touching this list fails the completeness check below, which is the point
    # -- `conform` reached a published candidate routed nowhere precisely
    # because a new module needed no decision from anyone.
    INTERNAL_MODULES = frozenset({
        "blast", "catch", "cli", "commands", "confine", "diff", "effects",
        "efficacy", "features", "fidelity", "interrupt", "replay", "runner",
        "seams", "sensitivity", "spec", "steady", "stores", "subject_tree",
        "sweep",
        # The builtin feature tree. `builtin/` carries no `__init__.py`, but a
        # namespace package nested inside a regular one is importable anyway
        # and every file here ships in the wheel, so these are as reachable as
        # any top-level module and have to be decided rather than assumed. The
        # decision is internal: a recipient reaches them through the feature
        # loader in `features.py`, which resolves them by path, and none is an
        # import surface anyone is invited to depend on.
        "builtin.freeze.feature", "builtin.freeze.invert",
        "builtin.receipt.feature", "builtin.receipt.invert",
        "builtin.redact.feature", "builtin.redact.invert",
        "builtin.retry.feature", "builtin.retry.invert",
        "builtin.sample.feature", "builtin.sample.invert",
        "builtin.timing.feature",
        # The package initializer and the `python -m` entry point. Both are
        # importable and both are decided here rather than skipped: discovery
        # used to drop every dunder path, so `__init__.py` -- the module every
        # recipient touches first -- could grow an `__all__` and a public
        # function with nothing failing.
        #
        # The decision is internal *as library modules*, and neither is
        # unrouted. `__init__` holds one public name, `__version__`, which is
        # routed by `C-HWB-01` as package identity rather than as a library
        # surface. `__main__` is the `python -m harness_workbench` entry point,
        # routed in the CLI/help manifest; it defines nothing of its own and
        # exists to call `cli.main`. If either ever declares `__all__`, rule 2
        # fails until somebody moves it into the manifest, which is the point.
        "__init__", "__main__",
    })

    # The shipped subject tree is opt-in data, not a library surface, and the
    # record says so. It is the *subject* of rule 1 below rather than something
    # rule 3 classifies.
    SHIPPED_TREE = "subjects"

    # The top-level packages under `src/` that this distribution ships. Like
    # INTERNAL_MODULES this is the decision, not the discovery.
    #
    # The routing rules below read `src/harness_workbench/` only, while
    # `[tool.setuptools.packages.find]` names `where = ["src"]` with no filter
    # -- so `src/harness_extra/` was found by the build, shipped in the wheel,
    # and classified by nothing. That is outside the letter of the manifest,
    # whose prose scopes itself to one package, but straight through its
    # purpose: adding a module to the distribution is supposed to force
    # somebody to decide what it is, and that placement forced nothing.
    #
    # Binding the set here rather than widening discovery is deliberate. Every
    # routing key, every INTERNAL_MODULES entry and the manifest itself are
    # written relative to `harness_workbench`, so widening discovery would file
    # a second package's modules under a manifest namespace that does not
    # exist and quietly reinterpret the keys that do. A declared set fails at
    # the moment the second package appears and makes the widening a decision
    # somebody takes on purpose, with the record updated to match.
    DISTRIBUTED_PACKAGES = frozenset({"harness_workbench"})

    def test_only_the_decided_top_level_packages_are_distributed(self):
        config = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        find = config["tool"]["setuptools"]["packages"]["find"]
        self.assertEqual(
            ["src"],
            find.get("where"),
            "package discovery no longer roots at src/; this check reads src/",
        )
        # An include/exclude filter would change what "found under src/ ships"
        # means, and this check may not assume the answer it was written for.
        self.assertEqual(
            [],
            sorted(set(find) - {"where"}),
            "packages.find grew a filter; re-derive what actually ships before "
            "trusting the set below",
        )
        # `packages.find` is not the only key that decides what ships.
        # `py-modules = ["loose"]` puts `src/loose.py` into the distribution
        # without `find` ever returning it and without a directory appearing
        # under `src/`, so the comparison below looked straight past it: the
        # same hole a second top-level package went through, reached through a
        # different key. The keys this check has actually reasoned about are
        # declared here, and one it has not refuses the declaration rather
        # than being assumed harmless.
        self.assertEqual(
            [],
            sorted(
                set(config["tool"]["setuptools"])
                - {
                    "package-dir",
                    "packages",
                    "package-data",
                    "include-package-data",
                    "dynamic",
                }
            ),
            "[tool.setuptools] grew a key this check has not reasoned about; "
            "decide whether it adds Python to the distribution before adding "
            "it to the set above",
        )
        self.assertEqual(
            {"": "src"},
            config["tool"]["setuptools"].get("package-dir"),
            "package-dir no longer maps the distribution root to src/, so the "
            "directories read below are not the ones that ship",
        )
        # Every directory under `src/` holding Python at any depth. That is a
        # superset of what `find_packages` returns -- it needs an `__init__.py`
        # and this does not -- on purpose: a directory of shipped Python with
        # no initializer is still a thing somebody has to decide about, and
        # failing on it costs one line in the set below.
        found = set()
        for path in sorted((ROOT / "src").iterdir()):
            if not path.is_dir() or path.name.endswith(".egg-info"):
                continue
            if any(
                "__pycache__" not in module.parts for module in path.rglob("*.py")
            ):
                found.add(path.name)
        self.assertEqual(
            sorted(self.DISTRIBUTED_PACKAGES),
            sorted(found),
            "the top-level packages under src/ are not the ones this suite "
            "routes; a package added here ships in the wheel and is classified "
            "by nothing, so add it to DISTRIBUTED_PACKAGES only together with "
            "the routing that decides its modules",
        )

    def core_modules(self) -> "dict[str, Path]":
        """Every importable module in the package except the shipped tree.

        Discovery used to be `glob("*.py")`, which reached only the top level.
        A module added one directory down -- under `builtin/`, which ships in
        the wheel and imports fine -- needed no decision from anyone. That is
        the hole `capture` went through, and it was still open underneath.

        It was open a second way: skipping every path part beginning with `__`
        excused `__init__.py`, `__main__.py`, and anything under a dunder-named
        directory from being decided at all. `__init__.py` is the first module
        any recipient imports and it already exports `__version__`. Only
        `__pycache__` is skipped now -- it is a build product, not source.
        """
        package = ROOT / "src" / "harness_workbench"
        found = {}
        for path in sorted(package.rglob("*.py")):
            relative = path.relative_to(package)
            if relative.parts[0] == self.SHIPPED_TREE:
                continue
            if "__pycache__" in relative.parts:
                continue
            found[".".join(relative.with_suffix("").parts)] = path
        return found

    @staticmethod
    def declares_all(path: Path) -> bool:
        """Whether a module assigns `__all__`, read as syntax at any nesting.

        The regex this replaces was anchored at column zero, so two spellings
        that move a module's declared surface went unread: an `__all__`
        assigned inside an `if` block, and `globals()["__all__"] = [...]`.
        Either moves what `import *` re-exports while the routing and
        exported-name rules skip the module entirely -- the same shape as the
        hole `canon` sat in before `0.1.0rc2`.

        Assignment is not the only statement that binds a name, and reading
        only assignment targets missed three that do. `for __all__ in ...`,
        `with ... as __all__` and the walrus `(__all__ := ...)` each leave
        `__all__` bound in the module namespace and each governs `import *`,
        so a module could move its declared surface past both the routing and
        the exported-name rule while spelling `__all__` in plain sight. Their
        targets go through the same leaf walk as an assignment's, so a tuple
        target binds the same way here as it does there. A comprehension's own
        target is deliberately not read -- it does not escape the
        comprehension, so it never reaches the module namespace -- while a
        walrus written inside one does escape, and is a `NamedExpr` like any
        other.

        Deliberately syntax, not `hasattr` on an imported module: importing
        every module in the package to answer this question runs import side
        effects across the whole tree, which is a worse risk than the gap.

        Scoped to module level rather than every nesting. `if`, `try` and
        `with` at module scope still count -- a conditional `__all__` is the
        module's surface, and skipping it was the original hole. A `def` or
        `class` body does not: an `__all__` bound there is a local variable
        that never reaches the module namespace, so tripping on it would force
        a decision about a surface that does not exist. Over-detection is the
        safer direction of the two, but it is not free -- a rule that fires on
        something harmless teaches the reader to route around it.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def module_scope(node):
            """Statements executing in the module namespace, not inside a scope."""
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
                ):
                    continue
                yield child
                yield from module_scope(child)

        def is_this_module(node) -> bool:
            """`sys.modules[__name__]` -- the module writing to itself."""
            return (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "modules"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "sys"
                and isinstance(node.slice, ast.Name)
                and node.slice.id == "__name__"
            )

        for node in module_scope(tree):
            # `from x import __all__`, or anything bound `as __all__`. The name
            # arrives from another module but lands in this one's namespace and
            # governs its `import *` exactly as an assignment would.
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if (alias.asname or alias.name) == "__all__":
                        return True
                continue
            # `setattr(sys.modules[__name__], "__all__", [...])`.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and is_this_module(node.args[0])
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "__all__"
            ):
                return True
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(
                node,
                (ast.AnnAssign, ast.AugAssign, ast.NamedExpr, ast.For, ast.AsyncFor),
            ):
                targets = [node.target]
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                targets = [
                    item.optional_vars
                    for item in node.items
                    if item.optional_vars is not None
                ]
            else:
                continue
            for target in targets:
                # `sys.modules[__name__].__all__ = [...]` -- the same write as
                # the setattr above, spelled as an attribute.
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "__all__"
                    and is_this_module(target.value)
                ):
                    return True
                for leaf in ast.walk(target):
                    if isinstance(leaf, ast.Name) and leaf.id == "__all__":
                        return True
                    # `globals()["__all__"] = [...]` writes the module
                    # namespace without ever naming `__all__` as a binding.
                    if (
                        isinstance(leaf, ast.Subscript)
                        and isinstance(leaf.value, ast.Call)
                        and isinstance(leaf.value.func, ast.Name)
                        and leaf.value.func.id in {"globals", "vars"}
                        and isinstance(leaf.slice, ast.Constant)
                        and leaf.slice.value == "__all__"
                    ):
                        return True
        return False

    def public_library_manifest(self) -> str:
        """The manifest section alone, not the whole 400-line record.

        Asserting a module name appears *somewhere* in the record is satisfied
        by any passing mention -- a departure that names the module, a claim
        row, even the sentence explaining the omission. Routing means the name
        is in the manifest, so read only the manifest.
        """
        record = (ROOT / "docs" / "release-conformance-0.1.0rc2.md").read_text(
            encoding="utf-8"
        )
        heading = "### Exact public library-module manifest"
        self.assertIn(heading, record, "the public library manifest is missing")
        section = record.split(heading, 1)[1]
        # Any heading ends the section. Splitting on `## ` and `### ` alone let
        # an `#### ` subheading through, so everything under it counted as
        # manifest and a module named down there read as routed.
        return re.split(r"\n#{1,6} ", section, maxsplit=1)[0]

    def manifest_entries(self) -> "dict[str, str]":
        """The manifest split per routed module, not one flat blob.

        Reading the whole section for every module lets one module's entry
        satisfy another's, and lets prose after the last bullet count as
        routing. An entry is the bullet that names the module and ends where
        that bullet ends.
        """
        manifest = self.public_library_manifest()
        entries = {}
        for block in re.split(r"\n(?=- `harness_workbench\.)", manifest):
            match = re.match(r"- `harness_workbench\.([A-Za-z0-9_.]+)`", block.lstrip())
            if match:
                entries[match.group(1)] = block.split("\n\n", 1)[0]
        return entries

    @staticmethod
    def imported_module_name(node: ast.ImportFrom, containing: "tuple[str, ...]"):
        """The module an `ImportFrom` reads, with relative levels resolved.

        `node.level` is the leading dot count and `containing` is the package
        of the file holding the statement, so level 1 is that package, level 2
        its parent, and so on. Returns `None` for a level that walks off the
        top of the source root, which is not importable at runtime either.

        Skipping relative imports outright -- which is what `not node.level`
        did -- made the whole rule optional: `from .. import runner` inside the
        shipped tree binds `harness_workbench.runner` exactly as the absolute
        spelling does, the modules resolve, and the suite stayed green. A
        spelling the rule cannot see reads as coverage and is worse than no
        rule, and that is truer of a spelling the rule deliberately discards.
        """
        if not node.level:
            return node.module or ""
        if node.level > len(containing):
            return None
        base = list(containing[: len(containing) - node.level + 1])
        if node.module:
            base.append(node.module)
        return ".".join(base)

    def core_imports_of_shipped_tree(self) -> "set[str]":
        """What the shipped tree imports from core, read as syntax not text.

        The old rule scraped two regexes over `subjects/*.py`. It captured only
        the first name of `from harness_workbench import a, b`, never saw
        `import harness_workbench.a`, and did not recurse -- so a core import
        could be added that the rule could not see while it went on passing,
        which reads as coverage and is worse than no rule.

        Absolute and package-relative spellings are both resolved to one
        absolute name before anything is decided, so the two cannot disagree.
        Relative imports that stay inside the shipped tree resolve to
        `harness_workbench.subjects.*`, which `core_modules` excludes, so the
        tree's own internal imports implicate nothing -- the exclusion does
        that work, not a filter here.

        Returns dotted paths below `harness_workbench.`, each of which may name
        a module, a package, or a name inside a module. Resolving which is
        `implicated_core_modules`'s job -- an importer cannot tell them apart
        from syntax alone, and guessing wrong silently drops the import.

        Only import *statements* are read. A module named solely through a
        dynamic call -- `importlib.import_module("harness_workbench.runner")`
        -- is not seen, which the record discloses as the boundary of the rule
        rather than claiming otherwise.
        """
        source_root = ROOT / "src"
        package = source_root / "harness_workbench"
        prefix = "harness_workbench."
        imported = set()
        for path in sorted((package / self.SHIPPED_TREE).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            # The package holding this file. Dropping the final part gives the
            # package for a module (`subjects/compare.py` -> `subjects`) and
            # for a package initializer alike (`subjects/__init__.py` is itself
            # `subjects`, whose own package for relative purposes is `subjects`).
            containing = path.relative_to(source_root).with_suffix("").parts[:-1]
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = self.imported_module_name(node, containing)
                    if module is None:
                        continue
                    if module == "harness_workbench":
                        imported.update(alias.name for alias in node.names)
                    elif module.startswith(prefix):
                        remainder = module[len(prefix):]
                        imported.add(remainder)
                        # `from harness_workbench.builtin.retry import feature`
                        # imports the module `builtin.retry.feature`, and the
                        # remainder alone is `builtin.retry` -- not a module
                        # key, so intersecting on it discarded the import and
                        # the rule passed on a working spelling.
                        imported.update(
                            f"{remainder}.{alias.name}" for alias in node.names
                        )
                elif isinstance(node, ast.Import):
                    # `alias.asname` is not read on purpose: `import x` and
                    # `import x as y` bind the same module and `alias.name` is
                    # identical for both, so an alias is a spelling of this
                    # branch rather than a second one.
                    for alias in node.names:
                        if alias.name.startswith(prefix):
                            imported.add(alias.name[len(prefix):])
        return imported

    def implicated_core_modules(
        self, candidates: "set[str]", modules: "dict[str, Path]"
    ) -> "set[str]":
        """The core modules a set of imported dotted paths reaches.

        A candidate implicates the module it names, and -- when it names a
        package -- every module under it. `from harness_workbench.builtin
        import retry` binds a package whose modules the importer can then reach
        by attribute; treating that as importing nothing let the shipped tree
        take a dependency on internal code with the rule still green.
        """
        implicated = set()
        for candidate in candidates:
            for name in modules:
                if name == candidate or name.startswith(candidate + "."):
                    implicated.add(name)
        return implicated

    def test_public_library_surface_is_routed_or_declared_internal(self):
        entries = self.manifest_entries()
        modules = self.core_modules()

        def routed(name: str) -> bool:
            return name in entries

        # 1. DERIVED. The shipped subject tree is opt-in data, but it is data a
        #    recipient receives and runs, and whatever it imports from core is
        #    load-bearing public API by use rather than by declaration. This
        #    rule needs no maintenance: it reads the imports.
        imported_by_shipped_tree = self.implicated_core_modules(
            self.core_imports_of_shipped_tree(), modules
        )
        self.assertTrue(
            imported_by_shipped_tree,
            "found no core imports in the shipped subject tree; the discovery "
            "pattern has probably drifted and is now vacuous",
        )
        for name in sorted(imported_by_shipped_tree):
            with self.subTest(imported=name):
                self.assertTrue(
                    routed(name),
                    f"the shipped subject tree imports harness_workbench.{name}, "
                    "so it is public API a recipient receives, but it is not in "
                    "the public library manifest",
                )

        # 2. DECLARED. `__all__` is the author saying "this is the public
        #    surface" in the code itself.
        for name, path in sorted(modules.items()):
            if not self.declares_all(path):
                continue
            with self.subTest(declared=name):
                self.assertTrue(
                    routed(name),
                    f"harness_workbench.{name} declares __all__ but is not in "
                    "the public library manifest",
                )

        # 3. COMPLETENESS. Every module is routed or deliberately internal.
        #    Rules 1 and 2 only catch modules that already advertise
        #    themselves; `conform` is public and does neither, which is exactly
        #    the shape that slipped through before.
        for name in sorted(modules):
            with self.subTest(module=name):
                self.assertTrue(
                    routed(name) or name in self.INTERNAL_MODULES,
                    f"harness_workbench.{name} is neither routed in the public "
                    "library manifest nor listed in INTERNAL_MODULES; decide "
                    "which it is",
                )
        stale = sorted(self.INTERNAL_MODULES - set(modules))
        self.assertEqual([], stale, f"INTERNAL_MODULES names missing modules: {stale}")
        both = sorted(name for name in self.INTERNAL_MODULES if routed(name))
        self.assertEqual([], both, f"modules both routed and internal: {both}")

    def test_exported_names_of_declared_public_modules_are_listed_exactly(self):
        """The manifest names the surface, so the surface cannot move quietly.

        Without this, "the exported names are the ones listed in the manifest"
        is a claim no test can fail: the manifest described capabilities in
        prose, and prose does not disagree with an added export.
        """
        import importlib

        entries = self.manifest_entries()
        checked = 0
        for name, path in sorted(self.core_modules().items()):
            if not self.declares_all(path):
                continue
            entry = entries.get(name)
            self.assertIsNotNone(
                entry,
                f"harness_workbench.{name} declares __all__ but has no entry in "
                "the public library manifest",
            )
            module = importlib.import_module(f"harness_workbench.{name}")
            declared = getattr(module, "__all__", None)
            self.assertIsNotNone(
                declared,
                f"harness_workbench.{name} assigns __all__ in its source but the "
                "imported module has no __all__; the assignment does not reach "
                "the module namespace",
            )
            exported = set(declared)
            for name_ in sorted(exported):
                with self.subTest(module=name, exported=name_):
                    self.assertIn(
                        f"`{name_}`",
                        entry,
                        f"harness_workbench.{name} exports {name_!r}, which its "
                        "public library manifest entry does not name",
                    )
            # Read only this module's own entry. Reading the whole section let
            # one module's exported names satisfy another module's row.
            # Only the two literals an entry legitimately writes about itself
            # are subtracted: `__all__`, which the entry prose names when it
            # says the module declares one, and the package name. Dropping the
            # whole dunder *class* instead excused every other dunder from both
            # checks below, so an entry could promise `__nonexistent__` and
            # pass -- a false positive fixed by widening the exemption past
            # what caused it.
            listed = set(
                re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", entry)
            ) - {"harness_workbench", "__all__"}
            # Nothing is subtracted here. Excusing every routed module's short
            # name punched a hole straight through both checks below: an entry
            # could name a sibling module -- `conform` inside `capture`'s row --
            # and the manifest would read as though that sibling were part of
            # this module's exported surface, with nothing failing. A sibling
            # gets named in its dotted form (`capture.digest_file`), which the
            # identifier pattern above does not match, so every bare backticked
            # identifier left here must be an export of this module.

            # Every name the entry writes must BE an export. Comparing only
            # against `dir(module)` meant a name that was not an attribute at
            # all fell outside the comparison and passed unnoticed, so the
            # manifest could promise an export that does not exist.
            absent = sorted(found for found in listed if not hasattr(module, found))
            self.assertEqual(
                [],
                absent,
                f"the manifest entry for harness_workbench.{name} names {absent}, "
                "which the module does not define at all",
            )
            unexpected = sorted(listed - exported)
            self.assertEqual(
                [],
                unexpected,
                f"the manifest entry for harness_workbench.{name} names "
                f"{unexpected}, which are not in its __all__",
            )
            checked += 1
        self.assertTrue(checked, "no module declares __all__; this check is vacuous")

    def test_conformance_record_pins_standards_and_stays_pre_release(self):
        record = (ROOT / "docs" / "release-conformance-0.1.0rc2.md").read_text(
            encoding="utf-8"
        )
        pins = (
            "EF-SRS",
            "EF-RS-REL",
            "0.4.0",
            "671379e920e64fa0c68c5086f0acac4c1512d4f6",
            "03e0211f8784c28aa87be8978108e753c6b64088",
            "d4d8d5cd278bbe0f9dffe2661ef09e851e87d028",
            "a6a89937ede2b7e672868d75d995604b0ec4c2f15169d79d7ac114477915cf85",
            "81bf7d24f775355459a0787f6f54bcc68f08fe04abffce32a12ae9d1c94347cb",
        )
        for pin in pins:
            with self.subTest(pin=pin):
                self.assertIn(pin, record)
        self.assertIn("PREPARED; NOT RELEASED; PUBLICATION BLOCKED", record)
        self.assertIn("Release commit: **PENDING**", record)
        self.assertIn("**Outside assurance:** none", record)


if __name__ == "__main__":
    unittest.main()
