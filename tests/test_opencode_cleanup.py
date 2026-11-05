"""Optional native OpenCode descendant cleanup, with independent kernel checks.

Uses the shipped loopback scripted provider, never inference or account access.
Set HWB_OPENCODE_LIVE_TEST=1 and HWB_OPENCODE_BIN to the pinned native binary.
HWB_OPENCODE_CLEANUP_EVIDENCE_DIR retains all attempts for external validation.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

# The fixture records its own PID and birth identity from the kernel. This does
# not import the cleanup implementation or infer ownership from cwd/argv.
IDENTITY_SOURCE = r'''
import ctypes, errno, json, os, sys, time
from pathlib import Path

def kernel_identity(pid):
    if sys.platform.startswith("linux"):
        try:
            raw = Path("/proc", str(pid), "stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return None
        fields = raw[raw.rindex(")") + 1:].split()
        return {"pid": pid, "birth": [int(fields[19])], "zombie": fields[0] in ("Z", "X")}
    if sys.platform == "darwin":
        class BSD(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint32) for n in ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
            _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
            _fields_ += [(n, ctypes.c_uint32) for n in ("nfiles", "pgid", "jobc", "tdev", "tpgid")]
            _fields_ += [("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]
        if ctypes.sizeof(BSD) != 136:
            raise RuntimeError("unsupported Darwin identity ABI")
        lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        info = BSD()
        ctypes.set_errno(0)
        size = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size == 0 and ctypes.get_errno() in (errno.ESRCH, errno.ENOENT):
            return None
        if size != ctypes.sizeof(info) or info.pid != pid or not info.start_sec:
            raise RuntimeError("kernel identity unavailable")
        return {"pid": pid, "birth": [info.start_sec, info.start_usec], "zombie": info.status == 5}
    raise RuntimeError("native cleanup test supports Linux and macOS")
'''
IDENTITY = {}
exec(IDENTITY_SOURCE, IDENTITY)
kernel_identity = IDENTITY['kernel_identity']


def same_live_process(expected):
    current = kernel_identity(expected['pid'])
    return current is not None and current['birth'] == expected['birth'] and not current['zombie']


def stop_owned_process(expected):
    """Failure cleanup signals only the PID still carrying the recorded birth."""
    for number in (signal.SIGTERM, signal.SIGKILL):
        if not same_live_process(expected):
            return
        try:
            os.kill(expected['pid'], number)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2
        while same_live_process(expected) and time.monotonic() < deadline:
            time.sleep(.05)


@unittest.skipUnless(os.environ.get('HWB_OPENCODE_LIVE_TEST') == '1',
                     'set HWB_OPENCODE_LIVE_TEST=1 with pinned OpenCode')
class NativeOpenCodeCleanupTests(unittest.TestCase):
    def command(self, label, parent, *args, on_started=None):
        python = os.environ.get('HWB_OPENCODE_CLEANUP_PYTHON', sys.executable)
        home = parent / (label + '-home'); home.mkdir()
        env = {'HOME': str(home), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
               'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
        if not os.environ.get('HWB_OPENCODE_CLEANUP_PYTHON'):
            env['PYTHONPATH'] = str(ROOT / 'src')
        argv = [python, '-m', 'harness_workbench._opencode_cli', *map(str, args)]
        (parent / (label + '.command.json')).write_text(json.dumps({'argv': argv, 'environment': env}, indent=2) + '\n')
        process = subprocess.Popen(argv, env=env, cwd=parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            if on_started is not None:
                on_started()
            stdout, stderr = process.communicate(timeout=100)
        except BaseException:
            identity = kernel_identity(process.pid)
            if identity is not None:
                stop_owned_process(identity)
            process.wait(timeout=5)
            raise
        (parent / (label + '.stdout')).write_text(stdout)
        (parent / (label + '.stderr')).write_text(stderr)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    def workspace(self, label):
        evidence = os.environ.get('HWB_OPENCODE_CLEANUP_EVIDENCE_DIR')
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            return contextlib.nullcontext(tempfile.mkdtemp(prefix=label + '-', dir=evidence))
        return tempfile.TemporaryDirectory(prefix=label + '-')

    def exercise(self, detached):
        with self.workspace('detached' if detached else 'timeout') as raw:
            parent = Path(raw); owned = parent / 'builder'
            initialized = self.command('init', parent, 'init', owned)
            self.assertEqual(0, initialized.returncode, initialized.stdout + initialized.stderr)
            fixture = owned / 'coding_fixture'
            child = IDENTITY_SOURCE + '\nwith Path("native-children.jsonl").open("a") as f:\n    f.write(json.dumps(kernel_identity(os.getpid())) + "\\n")\ntime.sleep(90)\n'
            if detached:
                body = ('import subprocess, sys, time, unittest\n'
                        'class TestNativeChild(unittest.TestCase):\n'
                        '    def test_detached_background(self):\n'
                        f'        subprocess.Popen([sys.executable, "-B", "-c", {child!r}], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n'
                        '        time.sleep(2)\n')
            else:
                body = IDENTITY_SOURCE + ('\nimport unittest\nclass TestNativeChild(unittest.TestCase):\n'
                    '    def test_ordinary_native_command(self):\n'
                    '        with Path("native-children.jsonl").open("a") as f:\n'
                    '            f.write(json.dumps(kernel_identity(os.getpid())) + "\\n")\n'
                    '        time.sleep(90)\n')
            (fixture / 'test_slugger.py').write_text(body)
            config = owned / 'adapter_config.json'
            inputs = json.loads(config.read_text())['inputs']
            before = {n: hashlib.sha256((owned / n).read_bytes()).hexdigest() for n in inputs}
            sentinel = sentinel_identity = None

            def start_unrelated_same_cwd_sentinel():
                nonlocal sentinel, sentinel_identity
                deadline = time.monotonic() + 10
                candidates = []
                while not candidates and time.monotonic() < deadline:
                    candidates = list(owned.glob('.hwb-opencode-*/fixture'))
                    if not candidates:
                        time.sleep(.02)
                self.assertEqual(1, len(candidates), 'must observe the real retained fixture')
                # Same cwd as native tools, but this process is the test's child,
                # never an OpenCode descendant. Cwd cannot confer ownership.
                sentinel = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(90)'], cwd=candidates[0],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                sentinel_identity = kernel_identity(sentinel.pid)
                self.assertIsNotNone(sentinel_identity)

            recorded = []
            try:
                result = self.command('run', parent, 'run', config, '--opencode', os.environ.get('HWB_OPENCODE_BIN', 'opencode'),
                                      '--timeout', '30' if detached else '20', '--workspace-parent', owned, '--output', parent / 'result.json',
                                      on_started=start_unrelated_same_cwd_sentinel)
                output = parent / 'result.json'
                failure_detail = result.stdout[-4000:] + result.stderr + (output.read_text()[-4000:] if output.exists() else '')
                self.assertEqual(0 if detached else 1, result.returncode, failure_detail)
                record = json.loads(output.read_text())
                retained = Path(record['workspace']['retained_root']) / 'fixture'
                marker = retained / 'native-children.jsonl'
                self.assertTrue(marker.is_file(), 'must prove a real native child started')
                recorded = [json.loads(line) for line in marker.read_text().splitlines()]
                self.assertGreaterEqual(len(recorded), 2 if detached else 1)
                after = {n: hashlib.sha256((owned / n).read_bytes()).hexdigest() for n in inputs}
                audit = {'recorded_children': recorded, 'same_identity_children_still_live': [p for p in recorded if same_live_process(p)],
                         'unrelated_sentinel': sentinel_identity, 'unrelated_sentinel_still_live': same_live_process(sentinel_identity),
                         'original_input_hashes_unchanged': before == after}
                (parent / 'kernel-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
                report = record['process']['descendant_cleanup']
                self.assertTrue(report['enabled'])
                self.assertTrue(report['passed'], report)
                self.assertGreater(report['observed_count'], 0)
                self.assertEqual([], report['remaining'])
                self.assertEqual([], report['errors'])
                self.assertFalse(record['process']['group_alive_after_cleanup'])
                self.assertEqual(detached, record['verdict']['passed'], record['verdict'])
                self.assertEqual(None if detached else 'timeout', record['process']['termination_reason'])
                self.assertEqual(before, after)
                self.assertEqual([], audit['same_identity_children_still_live'])
                self.assertTrue(audit['unrelated_sentinel_still_live'])
            finally:
                # Failed runs are retained, but this test must not leave its own
                # verified processes running. Never signal a reused PID.
                marker_paths = list(owned.glob('.hwb-opencode-*/fixture/native-children.jsonl'))
                for marker in marker_paths:
                    for line in marker.read_text().splitlines():
                        identity = json.loads(line)
                        stop_owned_process(identity)
                if sentinel_identity is not None:
                    stop_owned_process(sentinel_identity)
                if sentinel is not None:
                    sentinel.wait(timeout=5)

    def test_ordinary_native_command_timeout_stops_observed_child(self):
        self.exercise(detached=False)

    def test_normal_exit_stops_observed_detached_children(self):
        self.exercise(detached=True)


if __name__ == '__main__':
    unittest.main()
