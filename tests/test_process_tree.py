"""Ownership and cleanup tests; no system-wide process discovery."""
import ctypes
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from harness_workbench import process_tree as tree


class FakeBackend:
    name = "fake-targeted"

    def __init__(self):
        self.processes = {101: tree._Identity(101, 1, (11,))}
        self.edges = {}

    def identity(self, pid, budget=None):
        value = self.processes.get(pid)
        if isinstance(value, Exception):
            raise value
        return value

    def children(self, pid, budget=None):
        value = self.edges.get(pid, [])
        if isinstance(value, Exception):
            raise value
        return value


# Outside every synthetic PID range below. Production rejects the REAL
# os.getpid() as a root or child, so a test process whose own PID happens to
# fall inside a fake table (small PIDs are common in containers) used to fail
# for reasons unrelated to the code under test.
FAKE_SELF_PID = 4_000_037


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(tree.os, "getpid", return_value=FAKE_SELF_PID)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tracker(self, backend):
        with patch.object(tree, "_backend", return_value=backend):
            return tree.ObservedProcessTree(101)

    def child(self, backend, pid=102, ppid=101, birth=12):
        backend.processes[pid] = tree._Identity(pid, ppid, (birth,))
        backend.edges.setdefault(ppid, []).append(pid)

    def test_only_verified_ancestry_is_adopted_and_reparenting_is_retained(self):
        backend = FakeBackend()
        self.child(backend)
        backend.processes[900] = tree._Identity(900, 1, (90,))
        backend.edges[101].append(900)  # Discovery alone does not confer ownership.
        tracker = self.tracker(backend)
        tracker.observe()
        self.assertEqual({101, 102}, set(tracker.identities))
        backend.processes[102] = tree._Identity(102, 1, (12,))
        backend.processes.pop(101)
        signaled = []

        def stop(identity, signum, budget=None):
            signaled.append(identity.pid)
            backend.processes.pop(identity.pid, None)

        with patch.object(tracker, "_signal", side_effect=stop):
            result = tracker.cleanup(.01)
        self.assertTrue(result['passed'], result)
        self.assertEqual([102], signaled)
        self.assertIn(900, backend.processes)

    def test_pid_reuse_before_signal_is_never_signaled(self):
        backend = FakeBackend()
        self.child(backend)
        tracker = self.tracker(backend)
        tracker.observe()
        backend.processes[102] = tree._Identity(102, 1, (999,))
        with patch.object(tree.os, 'kill') as kill, patch.object(tree.os, 'pidfd_open', create=True) as pidfd:
            tracker._signal(tracker.identities[102], signal.SIGTERM)
        kill.assert_not_called()
        pidfd.assert_not_called()
        self.assertFalse(tracker.cleanup(.01)['passed'])

    def test_parent_reuse_during_discovery_does_not_adopt_children(self):
        backend = FakeBackend()
        self.child(backend)
        tracker = self.tracker(backend)

        def discover(pid, budget=None):
            backend.processes[101] = tree._Identity(101, 1, (999,))
            return [102]

        with patch.object(backend, 'children', side_effect=discover):
            tracker.observe()
        self.assertEqual({101}, set(tracker.identities))
        self.assertTrue(tracker.errors)

    def test_reuse_before_kill_and_signal_permission_failure_fail_closed(self):
        backend = FakeBackend()
        self.child(backend)
        tracker = self.tracker(backend)
        tracker.observe()
        with patch.object(tree.sys, 'platform', 'darwin'), \
                patch.object(tree.os, 'kill', side_effect=PermissionError(errno.EPERM, 'denied')):
            tracker._signal(tracker.identities[102], signal.SIGTERM)
        self.assertIn('signal failed', ' '.join(tracker.errors))
        backend.processes[102] = tree._Identity(102, 1, (999,))
        with patch.object(tree.os, 'kill') as kill:
            tracker._signal(tracker.identities[102], signal.SIGKILL)
        kill.assert_not_called()
        self.assertFalse(tracker.cleanup(.01)['passed'])

    def test_nonfinite_and_nonpositive_cleanup_bounds_are_rejected(self):
        tracker = self.tracker(FakeBackend())
        for value in (0, -1, float('inf'), float('nan'), True, '1'):
            with self.subTest(value=value), self.assertRaises(tree.ProcessTreeError):
                tracker.cleanup(value)

    def test_discovery_and_identity_errors_fail_closed(self):
        backend = FakeBackend()
        self.child(backend)
        tracker = self.tracker(backend)
        tracker.observe()
        backend.processes[102] = PermissionError(errno.EPERM, 'synthetic denied')
        backend.edges[101] = tree.ProcessTreeError('synthetic discovery failure')
        with patch.object(tree.os, 'kill') as kill:
            result = tracker.cleanup(.01)
        self.assertFalse(result['passed'])
        self.assertEqual('unknown', result['remaining'][0]['state'])
        self.assertTrue(result['errors'])
        kill.assert_not_called()

    def test_limit_fails_instead_of_silently_omitting_children(self):
        backend = FakeBackend()
        tracker = self.tracker(backend)
        backend.edges[101] = list(range(200, 200 + tree.MAX_PROCESSES + 1))
        tracker.observe()
        result = tracker.cleanup(.01)
        self.assertFalse(result['passed'])
        self.assertIn('limit', ' '.join(result['errors']))

    def test_zombies_are_gone_and_root_is_not_signaled(self):
        backend = FakeBackend()
        self.child(backend)
        tracker = self.tracker(backend)
        tracker.observe()
        backend.processes[102] = tree._Identity(102, 101, (12,), zombie=True)
        with patch.object(tree.os, 'kill') as kill:
            result = tracker.cleanup(.01)
        self.assertTrue(result['passed'], result)
        kill.assert_not_called()
        self.assertTrue(tracker.root_alive())

    def test_many_children_share_one_grace_budget(self):
        backend = FakeBackend()
        for pid in range(102, 122):
            self.child(backend, pid=pid, birth=pid)
        tracker = self.tracker(backend)
        tracker.observe()
        actions = []

        def stop(identity, signum, budget=None):
            actions.append((identity.pid, signum))
            if signum == signal.SIGKILL:
                backend.processes.pop(identity.pid)

        # One shared grace per phase, not one per child: 20 children with a
        # grace each would take ~5 s in the SIGTERM phase alone. 50 ms left no
        # margin for a paused CI VM to finish the fake bookkeeping.
        start = time.monotonic()
        with patch.object(tracker, '_signal', side_effect=stop):
            result = tracker.cleanup(.25)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertTrue(result['passed'], result)
        self.assertEqual(20, sum(sig == signal.SIGKILL for _, sig in actions))

    def _expire_mid_poll(self, phase):
        """Children ignore SIGTERM; the chosen phase's deadline passes inside a poll."""
        backend = FakeBackend()
        self.child(backend)
        tracker = self.tracker(backend)
        tracker.observe()
        state = {"signum": None, "expired": False}

        def stop(identity, signum, budget=None):
            state["signum"] = signum
            if signum == signal.SIGKILL and phase == signal.SIGTERM:
                backend.processes.pop(identity.pid)

        original = backend.children

        def slow_children(pid, budget=None):
            if state["signum"] == phase and not state["expired"]:
                state["expired"] = True
                time.sleep(.08)  # past the .05 grace, mid-observation
            return original(pid, budget)

        with patch.object(tracker, '_signal', side_effect=stop), \
                patch.object(backend, 'children', side_effect=slow_children):
            result = tracker.cleanup(.05)
        self.assertTrue(state["expired"])
        return result

    def test_sigterm_grace_expiring_mid_poll_escalates_without_error(self):
        result = self._expire_mid_poll(signal.SIGTERM)
        self.assertTrue(result['passed'], result)
        self.assertEqual([], result['errors'])

    def test_sigkill_deadline_expiring_mid_poll_still_fails_closed(self):
        result = self._expire_mid_poll(signal.SIGKILL)
        self.assertFalse(result['passed'])
        self.assertIn('cleanup phase failed', ' '.join(result['errors']))

    def test_candidate_traversal_has_one_work_budget(self):
        backend = FakeBackend()
        for pid in range(102, 302):
            self.child(backend, pid=pid, birth=pid)
        tracker = self.tracker(backend)
        with patch.object(tree, 'MAX_WORK', 20), \
                patch.object(backend, 'identity', wraps=backend.identity) as reads:
            tracker.observe()
        self.assertLessEqual(reads.call_count, 20)
        self.assertIn('budget exhausted', ' '.join(tracker.errors))
        self.assertFalse(tracker.cleanup(.01)['passed'])

    def test_slow_candidate_calls_stop_at_monotonic_observation_budget(self):
        backend = FakeBackend()
        for pid in range(102, 202):
            self.child(backend, pid=pid, birth=pid)
        tracker = self.tracker(backend)
        reads = []
        original = backend.identity

        def slow(pid, budget=None):
            reads.append(pid)
            time.sleep(.01)
            return original(pid, budget)

        start = time.monotonic()
        with patch.object(tree, 'OBSERVE_SECONDS', .025), \
                patch.object(backend, 'identity', side_effect=slow):
            tracker.observe()
        self.assertLess(time.monotonic() - start, .15)
        self.assertLessEqual(len(reads), 4)
        self.assertIn('budget exhausted', ' '.join(tracker.errors))

    def test_cleanup_work_exhaustion_is_recorded_without_unbounded_signals(self):
        backend = FakeBackend()
        for pid in range(102, 202):
            self.child(backend, pid=pid, birth=pid)
        tracker = self.tracker(backend)
        tracker.observe()
        start = time.monotonic()
        with patch.object(tree, 'MAX_WORK', 20), \
                patch.object(tracker, '_signal') as send:
            result = tracker.cleanup(.05)
        self.assertLess(time.monotonic() - start, .3)
        self.assertFalse(result['passed'])
        self.assertIn('budget exhausted', ' '.join(result['errors']))
        self.assertTrue(result['remaining'])
        send.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith('linux') and hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'Linux pidfd API')
    def test_pidfd_is_closed_and_rechecked_before_signal(self):
        backend = FakeBackend()
        self.child(backend)
        tracker = self.tracker(backend)
        tracker.observe()

        def opened(pid):
            backend.processes[pid] = tree._Identity(pid, 1, (999,))
            return 51

        with patch.object(tree.os, 'pidfd_open', side_effect=opened), \
                patch.object(tree.signal, 'pidfd_send_signal') as send, \
                patch.object(tree.os, 'close') as close:
            tracker._signal(tracker.identities[102], signal.SIGTERM)
        send.assert_not_called()
        close.assert_called_once_with(51)


class BackendTests(unittest.TestCase):
    def test_linux_stat_parsing_handles_parentheses_and_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'321').mkdir()
            fields = ['S', '123'] + ['0'] * 17 + ['555']
            (root/'321/stat').write_text('321 (strange ) name) ' + ' '.join(fields))
            with patch.object(tree, 'Path', return_value=root):
                identity = tree._Linux().identity(321)
            self.assertEqual(tree._Identity(321, 123, (555,)), identity)

    def test_linux_children_from_all_threads_are_combined(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for tid, children in [('321', '500 501'), ('322', '502')]:
                path = root/'321/task'/tid
                path.mkdir(parents=True)
                (path/'children').write_text(children)
            with patch.object(tree, 'Path', return_value=root):
                self.assertEqual([500, 501, 502], tree._Linux().children(321))

    def test_linux_thread_iteration_uses_shared_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for tid in range(20):
                path = root/'321/task'/str(tid)
                path.mkdir(parents=True)
                (path/'children').write_text('500 501 502')
            with patch.object(tree, 'Path', return_value=root):
                with self.assertRaisesRegex(tree.ProcessTreeError, 'budget exhausted'):
                    tree._Linux().children(321, tree._Budget(work=5))

    def test_darwin_abi_high_resolution_identity_and_child_count(self):
        self.assertEqual(136, ctypes.sizeof(tree._BSDInfo))

        class Lib:
            def proc_pidinfo(self, pid, flavor, arg, pointer, size):
                info = pointer._obj
                info.pid = pid
                info.ppid = 101
                info.start_sec = 500
                info.start_usec = 123456
                return size

            def proc_listchildpids(self, pid, buffer, size):
                buffer[0] = 102
                buffer[1] = 103
                return 2

        backend = tree._Mac.__new__(tree._Mac)
        backend.lib = Lib()
        self.assertEqual((500, 123456), backend.identity(102).birth)
        self.assertEqual([102, 103], backend.children(101))
        with self.assertRaisesRegex(tree.ProcessTreeError, 'budget exhausted'):
            backend.children(101, tree._Budget(work=2))

    def test_darwin_permission_failure_only_tolerates_proven_absence(self):
        class Lib:
            def proc_pidinfo(self, *args):
                ctypes.set_errno(errno.EPERM)
                return 0
        backend = tree._Mac.__new__(tree._Mac)
        backend.lib = Lib()
        with patch.object(tree.os, 'kill', side_effect=ProcessLookupError) as exists:
            self.assertIsNone(backend.identity(321))
            exists.assert_called_once_with(321, 0)
        for result in (None, PermissionError('inaccessible')):
            with patch.object(tree.os, 'kill', side_effect=result) as exists:
                with self.assertRaisesRegex(tree.ProcessTreeError, 'PID 321'):
                    backend.identity(321)
                exists.assert_called_once_with(321, 0)
        with patch.object(tree.os, 'kill') as exists:
            with self.assertRaisesRegex(tree.ProcessTreeError, 'budget exhausted'):
                backend.identity(321, tree._Budget(work=2))
            exists.assert_not_called()

    def test_unsupported_preflight_fails_before_launch(self):
        with patch.object(tree.sys, 'platform', 'unsupported'):
            with self.assertRaises(tree.ProcessTreeError):
                tree.ObservedProcessTree.check_supported()

    def test_preflight_os_error_becomes_explicit_tracking_error(self):
        backend = FakeBackend()
        with patch.object(tree, '_backend', return_value=backend), \
                patch.object(backend, 'identity', side_effect=PermissionError('denied')):
            with self.assertRaises(tree.ProcessTreeError):
                tree.ObservedProcessTree.check_supported()

    def test_unreaped_zombie_root_is_accepted_as_not_alive(self):
        backend = FakeBackend()
        backend.processes[101] = tree._Identity(101, 1, (11,), zombie=True)
        with patch.object(tree, '_backend', return_value=backend):
            tracker = tree.ObservedProcessTree(101)
        self.assertFalse(tracker.root_alive())
        self.assertTrue(tracker.cleanup(.01)['passed'])

    def test_initial_root_without_kernel_identity_fails_closed(self):
        backend = FakeBackend()
        backend.processes.pop(101)
        with patch.object(tree, '_backend', return_value=backend):
            with self.assertRaisesRegex(tree.ProcessTreeError, 'initial root identity unavailable'):
                tree.ObservedProcessTree(101)


@unittest.skipUnless(sys.platform.startswith('linux') or sys.platform == 'darwin', 'Linux or Darwin process APIs')
class NativeTests(unittest.TestCase):
    def test_fast_exited_unreaped_root_requires_available_kernel_identity(self):
        root = subprocess.Popen([sys.executable, '-c', 'pass'])
        try:
            # Do not poll/wait: preserve the root PID's unreaped identity.
            time.sleep(.1)
            identity = tree._backend().identity(root.pid)
            if identity is None:
                # Darwin can hide an unreaped zombie from proc_pidinfo. A
                # completed root without observed birth is not safe ownership.
                with self.assertRaisesRegex(tree.ProcessTreeError, 'initial root identity unavailable'):
                    tree.ObservedProcessTree(root.pid)
            else:
                tracker = tree.ObservedProcessTree(root.pid)
                self.assertTrue(tracker.cleanup(.01)['passed'])
        finally:
            root.wait(timeout=3)

    def test_observed_detached_child_cleanup_leaves_unrelated_sentinel_alive(self):
        tree.ObservedProcessTree.check_supported()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'child.pid'
            child = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
            script = ('import subprocess,sys,time,pathlib; '
                      f'p=subprocess.Popen([sys.executable,"-c",{child!r}],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
                      f'pathlib.Path({str(path)!r}).write_text(str(p.pid)); time.sleep(30)')
            sentinel = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], cwd=temp, start_new_session=True)
            root = subprocess.Popen([sys.executable, '-c', script], cwd=temp, start_new_session=True)
            tracker = None
            try:
                tracker = tree.ObservedProcessTree(root.pid)
                deadline = time.monotonic()+3
                while time.monotonic()<deadline:
                    tracker.observe()
                    if path.exists() and int(path.read_text()) in tracker.identities:
                        break
                    time.sleep(.02)
                self.assertTrue(path.exists())
                self.assertIn(int(path.read_text()), tracker.identities)
                root.terminate()
                root.wait(timeout=3)
                result = tracker.cleanup(.1)
                self.assertTrue(result['passed'], result)
                self.assertEqual(1, result['observed_count'])
                self.assertFalse(result['remaining'])
                self.assertIsNone(sentinel.poll())
            finally:
                if root.poll() is None:
                    root.kill()
                    root.wait(timeout=3)
                if tracker is not None:
                    tracker.cleanup(.05)
                sentinel.terminate()
                sentinel.wait(timeout=3)
