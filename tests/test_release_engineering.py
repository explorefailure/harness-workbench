"""Mechanical checks for release policy that must not depend on GitHub."""
from __future__ import annotations

import os
from pathlib import Path
import re
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

    def test_secret_scan_allowlist_is_limited_to_the_documented_fake_token(self):
        config = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
        self.assertIn("useDefault = true", config)
        self.assertEqual(1, config.count("[[allowlists]]"))
        self.assertIn("notakey-live-[0-9a-f]{12}", config)
        self.assertNotIn("paths =", config)
        releasing = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
        self.assertIn("GITLEAKS_VERSION=8.30.1", releasing)
        self.assertIn("--log-opts='--all'", releasing)
        self.assertIn("--max-archive-depth 2 dist", releasing)


if __name__ == "__main__":
    unittest.main()
