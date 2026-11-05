"""Observed detached-child lifecycle through the bounded capture integration."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from harness_workbench import capture, process_tree


class OpenCodeCleanupVerdictTests(unittest.TestCase):
    def test_zero_exit_requires_explicit_true_supervised_cleanup(self):
        # Reuse the adapter loader's isolated helper bindings, not a mock verdict.
        from test_opencode_builder import adapter
        original = capture.Bounded(
            argv=['native'], returncode=0, termination_reason=None,
            stdout=b'', stderr=b'', stdout_source_bytes=0, stderr_source_bytes=0,
            stdout_overflow=False, stderr_overflow=False,
            group_alive_before_cleanup=False, group_alive_after_cleanup=False)
        self.assertTrue(adapter.clean_exit(original))  # Legacy unsupervised shape.
        for report in (None, {}, {'enabled': True}, {'passed': False},
                       {'passed': None}, {'passed': 1}, {'passed': 'true'}, [], True):
            supervised = capture._SupervisedBounded(**asdict(original),
                                                    descendant_cleanup=report)
            with self.subTest(report=report):
                self.assertFalse(adapter.clean_exit(supervised))
        successful = capture._SupervisedBounded(**asdict(original),
            descendant_cleanup={'enabled': True, 'passed': True})
        self.assertTrue(adapter.clean_exit(successful))
        # A good descendant report cannot mask native failures or group survivors.
        for changes in ({'returncode': 1}, {'termination_reason': capture.TIMEOUT},
                        {'group_alive_after_cleanup': True}):
            with self.subTest(changes=changes):
                self.assertFalse(adapter.clean_exit(replace(successful, **changes)))
                self.assertFalse(adapter.clean_exit(replace(original, **changes)))


@unittest.skipUnless(sys.platform.startswith('linux') or sys.platform == 'darwin',
                     'kernel process identity backend required')
class ObservedCaptureTests(unittest.TestCase):
    def exercise(self, root_exits, ignore_term=False):
        with tempfile.TemporaryDirectory(prefix='hwb-supervised-capture-') as directory:
            work = Path(directory)
            marker = work / 'owned.json'
            child = (
                'import os,json,signal,time;from pathlib import Path;'
                'from harness_workbench.process_tree import _backend;'
                + ('signal.signal(signal.SIGTERM,signal.SIG_IGN);' if ignore_term else '')
                + f'Path({str(marker)!r}).write_text(json.dumps({{"pid":os.getpid(),'
                  '"birth":list(_backend().identity(os.getpid()).birth)}));time.sleep(90)'
            )
            parent = (
                'import subprocess,sys,time;from pathlib import Path;'
                f'p=subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session=True);'
                f'm=Path({str(marker)!r});'
                '\nwhile not m.exists():time.sleep(.005)\n'
                + ('time.sleep(.3)' if root_exits else 'time.sleep(90)')
            )
            env = capture.minimal_environment(work / 'home', {
                'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'})
            result = None
            try:
                result = capture.run_bounded([sys.executable, '-B', '-c', parent],
                    cwd=work, env=env, timeout=1.5, termination_grace=.15,
                    track_descendants=True)
                self.assertTrue(marker.exists(), 'probe never reached detached child')
                owned = json.loads(marker.read_text())
                self.assertTrue(result.descendant_cleanup['passed'], result.descendant_cleanup)
                self.assertGreaterEqual(result.descendant_cleanup['observed_count'], 1)
                self.assertEqual([], result.descendant_cleanup['remaining'])
                now = process_tree._backend().identity(owned['pid'])
                self.assertTrue(now is None or now.zombie or list(now.birth) != owned['birth'])
                self.assertEqual(None if root_exits else capture.TIMEOUT, result.termination_reason)
                if root_exits:
                    self.assertEqual(0, result.returncode)
                if ignore_term:
                    sent = [v['signal'] for v in result.descendant_cleanup['signals']]
                    self.assertIn(signal.SIGTERM, sent)
                    self.assertIn(signal.SIGKILL, sent)
                stored = capture._bounded_evidence(result, stdout_limit=1024, stderr_limit=1024)
                self.assertEqual(result.descendant_cleanup, stored['descendant_cleanup'])
            finally:
                # Only an unchanged recorded probe identity may be cleaned after a failure.
                if marker.exists():
                    owned = json.loads(marker.read_text())
                    now = process_tree._backend().identity(owned['pid'])
                    if now and not now.zombie and list(now.birth) == owned['birth']:
                        os.kill(now.pid, signal.SIGKILL)

    def test_timeout_cleans_detached_term_ignoring_child(self):
        self.exercise(False, True)

    def test_normal_root_exit_cleans_observed_lingering_child(self):
        self.exercise(True)

    def test_unsupported_supervision_fails_before_launch(self):
        with patch.object(process_tree.ObservedProcessTree, 'check_supported',
                          side_effect=process_tree.ProcessTreeError('identity denied')), \
             patch.object(capture.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(capture.CaptureError, 'identity denied'):
                capture.run_bounded(['never-launched'], cwd=Path('/tmp'), env={},
                                    track_descendants=True)
            popen.assert_not_called()

    def test_tracker_setup_failure_closes_pipes_and_restores_handlers(self):
        created = []
        real_popen = capture.subprocess.Popen
        def launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            created.append(process)
            return process
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(capture.subprocess, 'Popen', side_effect=launch), \
             patch.object(process_tree.ObservedProcessTree, '__init__',
                          side_effect=process_tree.ProcessTreeError('root identity denied')):
            with self.assertRaisesRegex(capture.CaptureError, 'root identity denied'):
                capture.run_bounded([sys.executable, '-c', 'import time;time.sleep(90)'],
                    cwd=Path(directory), env=dict(os.environ), termination_grace=.1,
                    track_descendants=True)
        self.assertEqual(1, len(created))
        self.assertIsNotNone(created[0].poll())
        self.assertTrue(created[0].stdout.closed)
        self.assertTrue(created[0].stderr.closed)
        self.assertEqual(handlers, {sig: signal.getsignal(sig) for sig in handlers})

    def test_default_result_and_evidence_keep_original_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            result = capture.run_bounded([sys.executable, '-c', 'pass'],
                cwd=Path(directory), env=dict(os.environ))
        self.assertIs(type(result), capture.Bounded)
        self.assertNotIn('descendant_cleanup', asdict(result))
        self.assertNotIn('descendant_cleanup', capture._bounded_evidence(result,
                         stdout_limit=1024, stderr_limit=1024))
