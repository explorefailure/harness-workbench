"""The capture primitive, tested where it now lives.

These moved here from the Pi experiment when the code did. A primitive whose
tests stay behind in one caller is a primitive only that caller is allowed to
change safely, which defeats the point of promoting it.

stdlib unittest -- no test dependency, so `python3 -m unittest` just works.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from harness_workbench import capture  # noqa: E402


POSIX = os.name == "posix"


class DigestTests(unittest.TestCase):
    def test_digest_bytes_is_bare_hex(self):
        self.assertEqual(
            hashlib.sha256(b"abc").hexdigest(), capture.digest_bytes(b"abc")
        )

    def test_digest_file_agrees_with_canon_minus_the_prefix(self):
        """One digest rule. The prefix is presentation, the hex is the commitment."""
        from harness_workbench import canon

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "f.bin"
            path.write_bytes(b"payload")
            self.assertEqual(
                canon.digest_file(str(path)), "sha256:" + capture.digest_file(path)
            )


class ManifestTests(unittest.TestCase):
    def test_manifest_preserves_unicode_nested_path_mode_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "nested dir" / "naïve file.txt"
            path.parent.mkdir()
            raw = "héllo\r\n".encode("utf-8")
            path.write_bytes(raw)
            path.chmod(0o640)
            entries = capture.manifest(root)
        self.assertEqual(
            [
                {
                    "path": "nested dir/naïve file.txt",
                    "size": len(raw),
                    "mode": 0o640,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ],
            entries,
        )

    @unittest.skipUnless(POSIX, "symlink contract is POSIX-only")
    def test_manifest_skips_symlinks_rather_than_following_them(self):
        """Following one would digest outside bytes under an inside path."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real.txt").write_bytes(b"real")
            (root / "link.txt").symlink_to(root / "real.txt")
            self.assertEqual(["real.txt"], [e["path"] for e in capture.manifest(root)])


class RedactionTests(unittest.TestCase):
    def test_credential_values_selects_by_name_and_ignores_short_values(self):
        env = {
            "ANTHROPIC_API_KEY": "long-enough-secret",
            "SHORT_TOKEN": "abc",
            "PATH": "/usr/bin",
        }
        self.assertEqual(("long-enough-secret",), capture.credential_values(env))

    def test_longest_value_is_redacted_first(self):
        """A short secret inside a long one must not leave the long one's tail."""
        raw = b"prefix-SECRETVALUE-suffix"
        stored, count = capture.redact_bytes(raw, ("SECRETVALUE", "SECRET"))
        self.assertEqual(1, count)
        self.assertEqual(b"prefix-[REDACTED]-suffix", stored)

    def test_json_escaped_form_is_redacted_too(self):
        secret = 'a"b\\c-secret'
        raw = ('{"k":"' + secret.replace("\\", "\\\\").replace('"', '\\"') + '"}').encode()
        stored, count = capture.redact_bytes(raw, (secret,))
        self.assertEqual(1, count)
        self.assertNotIn(b"secret", stored)

    def test_ascii_escaped_json_form_is_redacted_too(self):
        """`json.dumps` escapes non-ASCII BY DEFAULT, and that form once slipped.

        A secret with any non-ASCII byte reached stored evidence as `\\uXXXX`
        while `redaction_count` said 0 -- indistinguishable from bytes that
        never held a secret. Python's default, not an exotic encoder: every
        `json.dumps` without `ensure_ascii=False` produces this, including one
        in this repository.
        """
        secret = "sécret-value-ünicode"
        raw = json.dumps({"k": secret}).encode("utf-8")
        self.assertIn(b"\\u00e9", raw)
        stored, count = capture.redact_bytes(raw, (secret,))
        self.assertEqual(1, count)
        self.assertNotIn(b"\\u00e9", stored)
        self.assertNotIn(secret.encode("utf-8"), stored)

    def test_a_secret_is_counted_once_per_occurrence_not_once_per_variant(self):
        """Three variants are registered; one occurrence must still count one.

        Otherwise `redaction_count` inflates with the encoder list rather than
        with what was actually found, and stops describing the evidence.
        """
        secret = "sécret-value-ünicode"
        raw = b"plain=" + secret.encode("utf-8")
        _, count = capture.redact_bytes(raw, (secret,))
        self.assertEqual(1, count)

    def test_capture_bytes_digests_what_was_stored_not_what_arrived(self):
        item = capture.capture_bytes(b"tok=SECRETVALUE", redactions=("SECRETVALUE",))
        stored = base64.b64decode(item["base64"])
        self.assertEqual(hashlib.sha256(stored).hexdigest(), item["sha256"])
        self.assertEqual(1, item["redaction_count"])
        self.assertEqual(len(b"tok=SECRETVALUE"), item["source_bytes"])

    def test_capture_bytes_reports_none_text_for_invalid_utf8(self):
        self.assertIsNone(capture.capture_bytes(b"\xff\xfe")["text"])


class EnvironmentTests(unittest.TestCase):
    def test_minimal_environment_drops_host_credentials_and_redirects_home(self):
        sensitive = {
            "ANTHROPIC_API_KEY": "must-not-cross",
            "OPENAI_API_KEY": "must-not-cross",
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, sensitive, clear=False
        ):
            env = capture.minimal_environment(Path(directory), {"X": "1"})
            self.assertTrue(Path(env["HOME"]).is_dir())
        for name in sensitive:
            self.assertNotIn(name, env)
        self.assertEqual("1", env["X"])
        self.assertNotEqual(os.environ.get("HOME"), env["HOME"])


class ContainmentTests(unittest.TestCase):
    def test_traversal_and_absolute_paths_are_refused(self):
        root = Path("/tmp/root")
        for value in ("../escape", "/etc/passwd", "a/../../b", "", None):
            with self.subTest(value=value):
                with self.assertRaises(capture.CaptureError):
                    capture.contained_path(root, value)

    def test_relative_path_resolves_below_the_root(self):
        self.assertEqual(
            Path("/tmp/root/a/b"), capture.contained_path(Path("/tmp/root"), "a/b")
        )

    def test_subject_reported_outside_path_is_labelled_not_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(
                "<outside-workspace>", capture.relative_to_root("/etc/passwd", root)
            )
            self.assertEqual("a/b", capture.relative_to_root("a/b", root))
            self.assertIsNone(capture.relative_to_root(17, root))


class SidecarTests(unittest.TestCase):
    def test_boundary_and_digest_pressure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            raw = b"abcdefgh"
            path.write_bytes(raw)
            exact = capture.capture_file(
                path, required=True, format_name="binary", max_bytes=len(raw)
            )
            self.assertEqual(base64.b64encode(raw).decode("ascii"), exact["base64"])
            self.assertEqual(len(raw), exact["size"])
            self.assertEqual([], exact["errors"])

            oversized = capture.capture_file(
                path, required=True, format_name="binary", max_bytes=len(raw) - 1
            )
            self.assertIsNone(oversized["base64"])
            # Refused, not truncated -- but the file's own digest still records
            # which bytes were refused.
            self.assertEqual(
                hashlib.sha256(raw).hexdigest(), oversized["file_sha256"]
            )
            self.assertRegex(oversized["errors"][0], "exceeds 7-byte")

    def test_framing_and_encoding_matrix(self):
        cases = (
            ("crlf", b'{"value":1}\r\n{"value":2}\r\n', "jsonl", 0, 2),
            ("blank", b'{"value":1}\n\n', "jsonl", 0, 1),
            ("partial", b'{"value":', "jsonl", 1, 0),
            ("invalid-utf8", b"\xff\xfe", "utf8", 1, None),
            ("binary", b"\xff\xfe", "bytes", 0, None),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.bin"
            for name, raw, format_name, error_count, record_count in cases:
                with self.subTest(name=name):
                    path.write_bytes(raw)
                    item = capture.capture_file(
                        path, required=True, format_name=format_name, max_bytes=1024
                    )
                    self.assertEqual(error_count, len(item["errors"]), item)
                    if record_count is not None:
                        self.assertEqual(record_count, len(item["jsonl"] or []))
                    self.assertEqual(raw, base64.b64decode(item["base64"]))
                    self.assertEqual(
                        hashlib.sha256(raw).hexdigest(), item["file_sha256"]
                    )

    def test_missing_required_file_is_a_recorded_state_not_an_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            item = capture.capture_file(
                Path(directory) / "absent", required=True, format_name="jsonl"
            )
        self.assertFalse(item["exists"])
        self.assertRegex(item["errors"][0], "required evidence file")
        # Absence must not digest as empty bytes: "no evidence" and "empty
        # evidence" are different findings.
        self.assertIsNone(item["file_sha256"])

    def test_absent_and_optional_records_no_error(self):
        with tempfile.TemporaryDirectory() as directory:
            item = capture.capture_file(
                Path(directory) / "absent", required=False, format_name="jsonl"
            )
        self.assertEqual([], item["errors"])

    @unittest.skipUnless(POSIX, "symlink contract is POSIX-only")
    def test_non_regular_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"secret")
            (root / "link").symlink_to(target)
            item = capture.capture_file(
                root / "link", required=True, format_name="binary"
            )
        self.assertRegex(item["errors"][0], "not a regular file")

    def test_nonpositive_limit_is_refused(self):
        with self.assertRaises(capture.CaptureError):
            capture.capture_file(Path("/tmp/x"), required=False, max_bytes=0)


class ParseJsonlTests(unittest.TestCase):
    def test_valid_records_survive_a_truncated_final_line(self):
        """A subject killed mid-write must not cost every record before it."""
        records, errors = capture.parse_jsonl(b'{"a":1}\n{"b":')
        self.assertEqual([{"a": 1}], records)
        self.assertEqual(1, len(errors))

    def test_objects_only_rejects_scalars(self):
        records, errors = capture.parse_jsonl(b"1\n", objects_only=True)
        self.assertEqual([], records)
        self.assertEqual(1, len(errors))

    def test_non_utf8_is_reported_once(self):
        records, errors = capture.parse_jsonl(b"\xff")
        self.assertEqual([], records)
        self.assertEqual(1, len(errors))


@unittest.skipUnless(POSIX, "process-group contract is POSIX-only")
class BoundedRunTests(unittest.TestCase):
    def _run(self, script, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            return capture.run_bounded(
                [sys.executable, "-c", script],
                cwd=Path(directory),
                env=dict(os.environ),
                **kwargs,
            )

    def test_clean_run_reports_no_bound_and_no_orphan(self):
        result = self._run("print('ok')", timeout=10)
        self.assertEqual(0, result.returncode)
        self.assertIsNone(result.termination_reason)
        self.assertEqual(b"ok\n", result.stdout)
        self.assertFalse(result.group_alive_before_cleanup)
        self.assertFalse(result.group_alive_after_cleanup)

    def test_nonzero_child_exit_is_captured_without_hanging(self):
        result = self._run("raise SystemExit(7)", timeout=5)
        self.assertEqual(7, result.returncode)
        self.assertFalse(result.timed_out)
        self.assertIsNone(result.termination_reason)
        self.assertFalse(result.group_alive_after_cleanup)

    def test_output_limit_stops_and_bounds_the_owned_process(self):
        result = self._run(
            "import os,time; os.write(1, b'x' * 65536); time.sleep(30)",
            timeout=10,
            stdout_limit=1024,
            stderr_limit=1024,
            termination_grace=1.0,
        )
        self.assertEqual(capture.STDOUT_LIMIT, result.termination_reason)
        self.assertTrue(result.stdout_overflow)
        # Kept exactly the limit, and still reports how much was thrown away.
        self.assertEqual(1024, len(result.stdout))
        self.assertGreater(result.stdout_source_bytes, 1024)
        self.assertFalse(result.group_alive_after_cleanup)

    def test_timeout_kills_the_owned_process_group(self):
        result = self._run(
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "time.sleep(30)",
            timeout=0.4,
            termination_grace=1.0,
        )
        self.assertTrue(result.timed_out)
        self.assertEqual(capture.TIMEOUT, result.termination_reason)
        self.assertFalse(result.group_alive_after_cleanup)
        # The real signal-derived status, not a synthesized 124.
        self.assertNotEqual(124, result.returncode)

    def test_child_ignoring_sigterm_is_escalated_and_still_bounded(self):
        started = time.monotonic()
        result = self._run(
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(30)",
            timeout=0.3,
            termination_grace=0.5,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(capture.TIMEOUT, result.termination_reason)
        self.assertFalse(result.group_alive_after_cleanup)
        self.assertLess(elapsed, 15)

    def test_detached_descriptor_holder_does_not_hang_capture(self):
        """A grandchild in its own session must not extend a bounded run.

        It inherits the pipe, so waiting for EOF would wait on a process this
        run never controlled. The child's exit ends the read instead.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "escaped.pid"
            script = (
                "import pathlib,subprocess,sys; "
                "p=subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(30)'], start_new_session=True); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid))"
            )
            started = time.monotonic()
            result = capture.run_bounded(
                [sys.executable, "-c", script],
                cwd=root,
                env=dict(os.environ),
                timeout=10.0,
            )
            elapsed = time.monotonic() - started
            escaped = int(pid_path.read_text())
            try:
                os.kill(escaped, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.assertLess(elapsed, 5.0)
        self.assertEqual(0, result.returncode)
        self.assertFalse(result.group_alive_after_cleanup)

    def test_orphan_left_in_the_group_is_reported_not_swallowed(self):
        """The subject leaks; the adapter must be able to say so."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "orphan.pid"
            script = (
                "import pathlib,subprocess,sys,os; "
                "p=subprocess.Popen([sys.executable, '-c', "
                "'import time,sys; sys.stdout.close(); time.sleep(30)']); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid))"
            )
            result = capture.run_bounded(
                [sys.executable, "-c", script],
                cwd=root,
                env=dict(os.environ),
                timeout=10.0,
                termination_grace=1.0,
            )
            try:
                os.kill(int(pid_path.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
        # Whatever it observed before cleanup, cleanup must leave nothing.
        self.assertFalse(result.group_alive_after_cleanup)

    def test_nonpositive_bounds_are_refused(self):
        for kwargs in (
            {"timeout": 0},
            {"stdout_limit": 0},
            {"stderr_limit": -1},
            {"termination_grace": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(capture.CaptureError):
                    self._run("pass", **kwargs)

    def test_signal_forwarding_degrades_off_the_main_thread(self):
        """signal.signal is main-thread-only; a soak must still get a bound."""
        import threading

        box = {}

        def body():
            box["result"] = self._run("print('threaded')", timeout=10)

        thread = threading.Thread(target=body)
        thread.start()
        thread.join(30)
        self.assertIn("result", box)
        self.assertEqual(0, box["result"].returncode)
        self.assertEqual((), box["result"].forwarded_signals)


if __name__ == "__main__":
    unittest.main()
