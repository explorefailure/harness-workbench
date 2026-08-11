"""Mechanical checks for release policy that must not depend on GitHub."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import harness_workbench  # noqa: E402
import release_checksums  # noqa: E402
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


class TestReleaseSurfaces(unittest.TestCase):
    def test_candidate_is_not_described_as_published(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("preparing v0.1.0-rc.1", readme)
        self.assertIn("has not been published", readme)
        self.assertIn("Unreleased — targeting 0.1.0rc1", changelog)
        self.assertIn("not been published or tagged", changelog)
        self.assertIn("Published releases\n\nNone yet", changelog)

    def test_ci_tag_check_is_read_only_and_tag_only(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("startsWith(github.ref, 'refs/tags/')", workflow)
        self.assertIn(
            'python tools/verify_release_tag.py "$GITHUB_REF_NAME"', workflow
        )


if __name__ == "__main__":
    unittest.main()
