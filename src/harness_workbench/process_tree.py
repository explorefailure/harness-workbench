"""Bounded cleanup of descendants observed through verified parent identities.

Polling can miss a child which forks and reparents between observations. This is
not a sandbox or a claim to discover every descendant. No system-wide process
inventory, command-line matching, or workspace-based ownership inference is used.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import math
import os
from pathlib import Path
import signal
import sys
import time


MAX_PROCESSES = 4096
POLL_SECONDS = 0.02
OBSERVE_SECONDS = 0.25
MAX_WORK = 32768


class ProcessTreeError(RuntimeError):
    """Ownership cannot be established without guessing."""


class _DeadlineExpired(ProcessTreeError):
    """The phase deadline passed. Distinct from running out of work units."""


class _Budget:
    """Aggregate work/deadline checks, including within backend iteration.

    A kernel identity/read call cannot be preempted here; its elapsed time is
    checked immediately afterwards. No further calls/signals follow exhaustion.
    """

    def __init__(self, deadline=None, work=None):
        self.deadline = time.monotonic() + OBSERVE_SECONDS if deadline is None else deadline
        self.remaining = MAX_WORK if work is None else work

    def check(self):
        if self.remaining <= 0:
            raise ProcessTreeError("process observation budget exhausted")
        if time.monotonic() >= self.deadline:
            raise _DeadlineExpired("process observation budget exhausted")
        self.remaining -= 1


def _check(budget):
    if budget is not None:
        budget.check()


@dataclass(frozen=True)
class _Identity:
    pid: int
    ppid: int
    birth: tuple[int, ...]
    zombie: bool = False

    def record(self):
        return {"pid": self.pid, "ppid": self.ppid, "birth": list(self.birth)}


class _Linux:
    name = "linux-proc"

    def identity(self, pid, budget=None):
        _check(budget)
        try:
            raw = (Path("/proc") / str(pid) / "stat").read_text()
        except (FileNotFoundError, ProcessLookupError):
            return None
        _check(budget)
        try:
            # comm is parenthesized and may itself contain spaces or ')'.
            end = raw.rindex(")")
            actual_pid = int(raw[:raw.index("(")].strip())
            fields = raw[end + 1:].split()
            if actual_pid != pid or len(fields) < 20:
                raise ValueError("invalid proc stat")
            return _Identity(pid, int(fields[1]), (int(fields[19]),), fields[0] in ("Z", "X"))
        except (ValueError, IndexError) as error:
            raise ProcessTreeError("invalid process identity") from error

    def children(self, pid, budget=None):
        _check(budget)
        task = Path("/proc") / str(pid) / "task"
        try:
            threads = []
            for entry in task.iterdir():
                _check(budget)
                threads.append(entry)
                if len(threads) > MAX_PROCESSES:
                    raise ProcessTreeError("thread discovery limit exceeded")
        except (FileNotFoundError, ProcessLookupError):
            return []
        result = set()
        for thread in threads:
            _check(budget)
            try:
                with (thread / "children").open() as handle:
                    raw = handle.read(MAX_PROCESSES * 24 + 1)
            except (FileNotFoundError, ProcessLookupError):
                continue
            _check(budget)
            if len(raw) > MAX_PROCESSES * 24:
                raise ProcessTreeError("child discovery output limit exceeded")
            try:
                for value in raw.split():
                    _check(budget)
                    result.add(int(value))
            except ValueError as error:
                raise ProcessTreeError("invalid child discovery") from error
            if len(result) > MAX_PROCESSES:
                raise ProcessTreeError("child discovery limit exceeded")
        return sorted(result)


class _BSDInfo(ctypes.Structure):
    # Darwin SDK sys/proc_info.h, PROC_PIDTBSDINFO; MAXCOMLEN is 16.
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid",
        "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        "nfiles", "pgid", "jobc", "tdev", "tpgid")]
    _fields_ += [("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64),
                ("start_usec", ctypes.c_uint64)]


class _Mac:
    name = "darwin-libproc"

    def __init__(self):
        if ctypes.sizeof(_BSDInfo) != 136:
            raise ProcessTreeError("unsupported Darwin process identity ABI")
        try:
            self.lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            self.lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                              ctypes.c_void_p, ctypes.c_int]
            self.lib.proc_pidinfo.restype = ctypes.c_int
            self.lib.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            self.lib.proc_listchildpids.restype = ctypes.c_int
        except (OSError, AttributeError) as error:
            raise ProcessTreeError("Darwin process APIs unavailable") from error

    def identity(self, pid, budget=None):
        _check(budget)
        info = _BSDInfo()
        ctypes.set_errno(0)
        size = self.lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        _check(budget)
        if size != ctypes.sizeof(info):
            code = ctypes.get_errno()
            if size == 0 and code in (errno.ESRCH, errno.ENOENT):
                return None
            if size == 0 and code in (errno.EPERM, errno.EACCES):
                # A PID enumerated just before exit may lose readable metadata.
                # Only independent ESRCH establishes absence; a live/inaccessible
                # PID still fails closed and never becomes a signaling target.
                _check(budget)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    _check(budget)
                    return None
                except OSError:
                    pass
                _check(budget)
            raise ProcessTreeError("Darwin process identity unavailable for PID %s (errno %s)" % (pid, code))
        if info.pid != pid or not info.start_sec or info.start_usec >= 1_000_000:
            raise ProcessTreeError("invalid Darwin process identity")
        return _Identity(pid, info.ppid, (info.start_sec, info.start_usec), info.status == 5)

    def children(self, pid, budget=None):
        _check(budget)
        # An extra slot detects truncation rather than silently ignoring a child.
        buffer = (ctypes.c_int * (MAX_PROCESSES + 1))()
        ctypes.set_errno(0)
        # Unlike proc_listpids, this convenience API returns a PID COUNT.
        count = self.lib.proc_listchildpids(pid, buffer, ctypes.sizeof(buffer))
        _check(budget)
        if count < 0:
            raise ProcessTreeError("Darwin child discovery failed")
        code = ctypes.get_errno()
        if count == 0 and code not in (0, errno.ESRCH, errno.ENOENT):
            raise ProcessTreeError("Darwin child discovery unavailable (errno %s)" % code)
        if count > MAX_PROCESSES:
            raise ProcessTreeError("child discovery limit exceeded")
        children = set()
        for index in range(count):
            _check(budget)
            if buffer[index] > 0:
                children.add(buffer[index])
        return sorted(children)


def _backend():
    if sys.platform.startswith("linux"):
        return _Linux()
    if sys.platform == "darwin":
        return _Mac()
    raise ProcessTreeError("observed descendant tracking unsupported on this platform")


class ObservedProcessTree:
    """Track only demonstrated ancestry; never signal the native root here.

    Call observe periodically while the root lives. cleanup also discovers under
    already observed live descendants after root exit. POSIX PID birth rechecks
    fail closed on observed reuse, but Darwin's final check/kill is not atomic.
    """

    @staticmethod
    def check_supported():
        try:
            backend = _backend()
            budget = _Budget()
            me = backend.identity(os.getpid(), budget)
            if me is None or me.zombie:
                raise ProcessTreeError("current process identity unavailable")
            backend.children(os.getpid(), budget)
        except OSError as error:
            raise ProcessTreeError("process observation unavailable") from error

    def __init__(self, root_pid: int):
        if type(root_pid) is not int or root_pid <= 1 or root_pid == os.getpid():
            raise ProcessTreeError("invalid observed root PID")
        self.backend = _backend()
        try:
            root = self.backend.identity(root_pid)
        except OSError as error:
            raise ProcessTreeError("initial root identity unavailable") from error
        # An unreaped zombie may retain a readable identity (Linux), but Darwin
        # can hide it already. Without an observed birth we must fail closed,
        # even if a very fast native command otherwise exited successfully.
        if root is None:
            raise ProcessTreeError("initial root identity unavailable")
        self.root = root
        self.identities = {root_pid: root}
        self.errors = []
        self.signals = []
        self.retired = set()

    def _error(self, message):
        if message not in self.errors and len(self.errors) < 128:
            self.errors.append(message)

    def _matching(self, expected, budget=None):
        _check(budget)
        if expected.pid in self.retired:
            return None
        try:
            current = self.backend.identity(expected.pid, budget)
        except (OSError, ProcessTreeError) as error:
            self._error("PID %s identity unavailable: %s" % (expected.pid, error))
            return None
        if current is None or current.zombie:
            self.retired.add(expected.pid)
            return None
        if current.birth != expected.birth:
            self.retired.add(expected.pid)
            self._error("PID %s identity changed; replacement not signaled" % expected.pid)
            return None
        return current

    def root_alive(self):
        return self._matching(self.root) is not None

    def observe(self, _budget=None, _expiry_escalates=False):
        budget = _budget if _budget is not None else _Budget()
        pending = list(self.identities.values())
        visited = set()
        while pending:
            try:
                _check(budget)
                parent = pending.pop()
                if parent.pid in visited or self._matching(parent, budget) is None:
                    continue
                visited.add(parent.pid)
                children = self.backend.children(parent.pid, budget)
                if len(children) > MAX_PROCESSES:
                    raise ProcessTreeError("child discovery limit exceeded")
                candidates = []
                for pid in children:
                    _check(budget)
                    if type(pid) is not int or pid <= 1 or pid == os.getpid():
                        raise ProcessTreeError("invalid discovered child PID")
                    child = self.backend.identity(pid, budget)
                    if child is not None and not child.zombie and child.ppid == parent.pid:
                        candidates.append(child)
                if self._matching(parent, budget) is None:
                    if any(child.pid in self.retired or child.pid not in self.identities or
                           self.identities[child.pid].birth != child.birth for child in candidates):
                        self._error("parent identity lost during child discovery")
                    continue
                for child in candidates:
                    _check(budget)
                    previous = self.identities.get(child.pid)
                    if previous is not None:
                        if previous.birth != child.birth:
                            self._error("observed child PID reused; replacement not adopted")
                            self.retired.add(child.pid)
                        continue
                    if len(self.identities) >= MAX_PROCESSES:
                        raise ProcessTreeError("observed process limit exceeded")
                    self.identities[child.pid] = child
                    pending.append(child)
            except (OSError, ProcessTreeError) as error:
                if _expiry_escalates and isinstance(error, _DeadlineExpired):
                    raise  # cleanup's SIGTERM phase escalates; not a failure
                self._error("descendant discovery failed: %s" % error)
                # A shared budget cannot be recovered by moving to a different
                # parent. Stop rather than repeating failures for every node.
                if isinstance(error, ProcessTreeError) and "budget exhausted" in str(error):
                    break

    def _signal(self, expected, signum, budget=None):
        _check(budget)
        if expected.pid == self.root.pid or self._matching(expected, budget) is None:
            return
        descriptor = None
        try:
            # Linux pidfds bind signaling to the opened task. Check birth after
            # opening as well so a pre-open PID reuse cannot acquire ownership.
            if sys.platform.startswith("linux") and hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
                descriptor = os.pidfd_open(expected.pid)
                if self._matching(expected, budget) is None:
                    return
                _check(budget)
                signal.pidfd_send_signal(descriptor, signum)
            else:
                if self._matching(expected, budget) is None:
                    return
                _check(budget)
                os.kill(expected.pid, signum)
            self.signals.append({**expected.record(), "signal": signum})
        except ProcessLookupError:
            self.retired.add(expected.pid)
        except OSError as error:
            self._error("PID %s signal failed (errno %s)" % (expected.pid, error.errno))
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def cleanup(self, grace: float):
        if type(grace) not in (float, int) or not math.isfinite(grace) or grace <= 0:
            raise ProcessTreeError("cleanup grace must be positive")
        # One deadline per phase, regardless of number of descendants.
        for signum in (signal.SIGTERM, signal.SIGKILL):
            deadline = time.monotonic() + grace
            budget = _Budget(deadline=deadline)
            sent = set()
            while True:
                try:
                    _check(budget)
                    self.observe(budget, _expiry_escalates=signum == signal.SIGTERM)
                    alive = []
                    for identity in self.identities.values():
                        _check(budget)
                        if identity.pid != self.root.pid and self._matching(identity, budget) is not None:
                            alive.append(identity)
                    for identity in alive:
                        _check(budget)
                        if identity.pid not in sent:
                            self._signal(identity, signum, budget)
                            sent.add(identity.pid)
                    if not alive:
                        break
                    # Grace expiry is expected escalation, not an observation
                    # failure. Budget errors inside work still fail closed.
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
                    if time.monotonic() >= deadline:
                        break
                except _DeadlineExpired as error:
                    # SIGTERM grace ran out mid-poll. That is the same expected
                    # escalation as expiry between polls: the SIGKILL phase
                    # observes the tree afresh. SIGKILL expiry still fails closed.
                    if signum != signal.SIGTERM:
                        self._error("cleanup phase failed: %s" % error)
                    break
                except ProcessTreeError as error:
                    self._error("cleanup phase failed: %s" % error)
                    break
        remaining = []
        budget = _Budget()
        exhausted = False
        for identity in self.identities.values():
            if identity.pid == self.root.pid or identity.pid in self.retired:
                continue
            try:
                if exhausted:
                    raise ProcessTreeError("process observation budget exhausted")
                current = self._matching(identity, budget)
            except ProcessTreeError as error:
                exhausted = True
                self._error("final identity check failed: %s" % error)
                remaining.append({**identity.record(), "state": "unknown"})
                continue
            if current is not None:
                remaining.append(identity.record())
            elif identity.pid not in self.retired:
                # An unknown identity is not proof of exit.
                remaining.append({**identity.record(), "state": "unknown"})
        return {"enabled": True, "passed": not remaining and not self.errors,
                "backend": self.backend.name, "observed_count": len(self.identities) - 1,
                "remaining": remaining, "errors": list(self.errors), "signals": list(self.signals),
                "scope": "observed descendants only; polling can miss between-sample reparenting"}
