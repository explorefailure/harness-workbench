#!/usr/bin/env python3
"""Portable process identities used by the launcher and supervisor witness."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import sys
from typing import Any


class _DarwinBsdInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_tvsec", ctypes.c_uint64),
        ("start_tvusec", ctypes.c_uint64),
    ]


def _digest_executable(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _linux_start_identity(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        remainder = raw[raw.rindex(") ") + 2 :].split()
        return f"linux-proc-start:{remainder[19]}"
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def _darwin_library() -> ctypes.CDLL:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    library.proc_pidinfo.restype = ctypes.c_int
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    return library


def _darwin_bsd_info(pid: int) -> _DarwinBsdInfo | None:
    try:
        library = _darwin_library()
        info = _DarwinBsdInfo()
        size = ctypes.sizeof(info)
        if library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), size) != size:
            return None
        return info
    except OSError:
        return None


def platform_start_identity(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        return _linux_start_identity(pid)
    if sys.platform == "darwin":
        info = _darwin_bsd_info(pid)
        if info is not None:
            return f"darwin-start:{info.start_tvsec}:{info.start_tvusec}"
    return None


def process_executable(pid: int) -> Path | None:
    if sys.platform.startswith("linux"):
        try:
            return Path(os.readlink(f"/proc/{pid}/exe")).resolve(strict=True)
        except (FileNotFoundError, OSError):
            return None
    if sys.platform == "darwin":
        try:
            library = _darwin_library()
            buffer = ctypes.create_string_buffer(4096)
            length = library.proc_pidpath(pid, buffer, len(buffer))
            if length <= 0:
                return None
            return Path(os.fsdecode(buffer.value)).resolve(strict=True)
        except (OSError, UnicodeError):
            return None
    return None


def registration_identity(target: Path) -> dict[str, str]:
    pid = os.getpid()
    start = platform_start_identity(pid)
    current = process_executable(pid) or Path(sys.executable).resolve(strict=True)
    if start is None:
        raise RuntimeError("platform does not expose a verifiable process start identity")
    return {
        "platform_start_identity": start,
        "launcher_executable_identity": _digest_executable(current),
        "executable_identity": _digest_executable(target),
    }


def prove_registered_process(row: dict[str, Any]) -> bool:
    pid = row.get("pid")
    pgid = row.get("pgid")
    if type(pid) is not int or type(pgid) is not int or pid != pgid:
        return False
    try:
        if os.getpgid(pid) != pgid:
            return False
    except ProcessLookupError:
        return False
    if platform_start_identity(pid) != row.get("platform_start_identity"):
        return False
    executable = process_executable(pid)
    if executable is None:
        return False
    actual = _digest_executable(executable)
    return actual in {
        row.get("launcher_executable_identity"),
        row.get("executable_identity"),
    }
