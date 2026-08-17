"""Mechanical checks for release policy that must not depend on GitHub."""
from __future__ import annotations

import fnmatch
import gzip
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import harness_workbench  # noqa: E402
import normalize_sdist  # noqa: E402
import release_checksums  # noqa: E402
import verify_release_artifacts  # noqa: E402
import verify_release_tag  # noqa: E402


class TestReleaseVersion(unittest.TestCase):
    def test_candidate_version_and_public_tag_are_exact(self):
        self.assertEqual("0.1.0rc1", harness_workbench.__version__)
        self.assertEqual("0.1.0rc1", verify_release_tag.source_version())
        verify_release_tag.verify("v0.1.0-rc.1", harness_workbench.__version__)

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
        rejected = ("v0.1.0", "0.1.0-rc.1", "v0.1.0rc1", "v0.1.0-rc.01")
        for tag in rejected:
            with self.subTest(tag=tag), self.assertRaises(verify_release_tag.TagError):
                verify_release_tag.verify(tag, "0.1.0rc1")

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
        self.assertEqual("hwb 0.1.0rc1\n", completed.stdout)


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
            self.assertEqual(0o751, members["harness-workbench/file.txt"].mode)
            self.assertTrue(members["harness-workbench/subdir"].isdir())
            self.assertEqual("file.txt", members["harness-workbench/link"].linkname)
            self.assertTrue(members["harness-workbench/link"].issym())
            self.assertEqual(
                "harness-workbench/file.txt",
                members["harness-workbench/hardlink"].linkname,
            )
            self.assertTrue(members["harness-workbench/hardlink"].islnk())

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
            if not any(fnmatch.fnmatch(relative, glob) for glob in subject_globs):
                uncovered.append(relative)
        self.assertEqual([], uncovered)

    def test_public_identity_and_minimal_verification_provenance_are_explicit(self):
        with (ROOT / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)["project"]
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        record = (ROOT / "docs" / "release-conformance-0.1.0rc1.md").read_text(
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
            "maintainer-side author-context verification on 2026-08-11",
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
        record = (ROOT / "docs" / "release-conformance-0.1.0rc1.md").read_text(
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
        self.assertIn("Frozen candidate record — NOT RELEASED", record)
        self.assertIn(
            "https://github.com/explorefailure/harness-workbench/actions/runs/31625746283",
            record,
        )
        self.assertIn(
            "https://github.com/explorefailure/harness-workbench/actions/runs/31625748519",
            record,
        )
        self.assertIn("No CodeQL pass or uploaded result is claimed", record)

    def test_ci_tag_check_is_read_only_and_tag_only(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("startsWith(github.ref, 'refs/tags/')", workflow)
        self.assertIn(
            'python tools/verify_release_tag.py "$GITHUB_REF_NAME"', workflow
        )

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
        record_path = ROOT / "docs" / "release-conformance-0.1.0rc1.md"
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
        link = "docs/release-conformance-0.1.0rc1.md"
        self.assertIn(f"]({link})", readme)
        self.assertIn(f"]({link})", releasing)

    def test_conformance_record_pins_standards_and_stays_pre_release(self):
        record = (ROOT / "docs" / "release-conformance-0.1.0rc1.md").read_text(
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
