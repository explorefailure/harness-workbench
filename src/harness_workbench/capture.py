"""Running an untrusted child and coming back with evidence you can defend.

Every adapter needs the same thing and none of it is about any particular
harness: bound a subprocess, keep what it emitted without letting it exhaust
you, digest what you kept, and be able to say afterwards which bound fired.

One rule governs the whole module: **a bound firing is a measurement, not an
error.** Timeouts, byte limits and nonzero exits are returned in the result;
this module raises only when it cannot measure at all. An adapter that has to
catch an exception to learn that its subject timed out will eventually catch
one it did not mean to, and record a timeout that never happened.

The second rule is that nothing here reports success by staying quiet. Every
bound and every cleanup returns a positive observation, because three silent
instrumentation failures once produced a perfectly clean run that measured
nothing at all -- and absence of error looked exactly like success.

What is deliberately absent: this module never parses harness events. It will
tell you some bytes are line-delimited JSON, which is a format fact. It does
not know what a tool call is, and it does not decide whether the subject did
the task. Those are a normalizer's and an oracle's job, and keeping them out
is what lets one primitive serve harnesses that agree on nothing else.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

from . import canon


# Positive defaults, never zero or None. A limit of zero disables the bound in
# most APIs that accept one, so an unset limit would silently become "unbounded"
# -- the one state this module exists to prevent.
DEFAULT_STDOUT_LIMIT = 1_048_576
DEFAULT_STDERR_LIMIT = 524_288
DEFAULT_SIDECAR_LIMIT = 524_288

# Environment names a child may keep. Everything else is dropped, so a variable
# has to be named here or passed as an explicit override to reach the subject.
# An allowlist rather than a denylist: a new credential variable appearing on
# some future host must not reach a subject because nobody thought to ban it.
PASSTHROUGH_NAMES = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "SYSTEMROOT")

# Substrings that make an environment value worth scrubbing from captured bytes.
CREDENTIAL_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL")

# Below this length a "secret" is more likely to be a flag than a credential,
# and redacting it would corrupt evidence to protect nothing.
CREDENTIAL_MIN_LENGTH = 8

TIMEOUT = "timeout"
STDOUT_LIMIT = "stdout_limit"
STDERR_LIMIT = "stderr_limit"
SIGNALLED = "signalled"


class CaptureError(ValueError):
    """The capture cannot be performed without guessing.

    Raised for impossible requests -- a nonpositive limit, a path that escapes
    its root -- never for anything the subject did. See the module rule: what
    the subject did is returned, not raised.
    """


def digest_bytes(data: bytes) -> str:
    """Bare-hex SHA-256 of exactly these bytes.

    Public because every adapter needs it and the alternative is each one
    importing hashlib and picking its own encoding. `canon` covers canonical
    JSON and files; raw in-memory bytes are the gap, and an adapter that has to
    fill a gap itself is how two digest conventions start.
    """
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    """Bare hex for a file, computed by `canon` and stripped at the wire edge.

    `canon.digest_file` is the project's one file-digest rule and is not
    reimplemented here. It returns `sha256:<hex>`; captured evidence stores bare
    hex, and sealed records already contain those bytes. The prefix is a display
    convention and the sealed bytes are a commitment, so the conversion happens
    here, once, rather than by reformatting evidence that has already been sealed.
    """
    return canon.digest_file(str(path)).split(":", 1)[1]


@dataclass(frozen=True)
class Bounded:
    """What a bounded run observed. Every field is an observation, not a verdict.

    `returncode` is the child's real exit status and is never synthesized. An
    earlier implementation mapped a timeout to 124 and a byte limit to 125,
    which are indistinguishable from a subject that genuinely exited 124 --
    `termination_reason` carries that fact instead, and carries it by name.

    `*_source_bytes` is what the subject tried to emit; `stdout`/`stderr` are
    what was kept. Without the pair, a truncated capture and a quiet subject
    look identical.
    """

    argv: list[str]
    returncode: int
    termination_reason: str | None
    stdout: bytes
    stderr: bytes
    stdout_source_bytes: int
    stderr_source_bytes: int
    stdout_overflow: bool
    stderr_overflow: bool
    # A cleanup you never check is not a cleanup. A subject that spawns a shell
    # that spawns a build leaves orphans holding the workspace open, which
    # corrupts the *next* run's before-manifest -- a failure that shows up
    # somewhere other than where it was caused.
    group_alive_before_cleanup: bool
    group_alive_after_cleanup: bool
    forwarded_signals: tuple[int, ...] = ()

    @property
    def timed_out(self) -> bool:
        return self.termination_reason == TIMEOUT


def _group_alive(pgid: int) -> bool:
    """Whether any process remains in the group.

    PermissionError counts as alive: the group exists, this process merely may
    not signal it. Reporting "gone" there would turn a containment failure into
    a clean receipt.
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(pgid: int, signum: int) -> None:
    """Signal the whole group, tolerating a group that has already exited."""
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _wait_for_group_exit(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _group_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not _group_alive(pgid)


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float = 120.0,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
    termination_grace: float = 5.0,
    forward_signals: bool = True,
) -> Bounded:
    """Run `argv` under a wall clock and per-stream byte ceilings.

    The child is started in its own session, so it leads a process group, and
    **every termination signals the group rather than the process**. A subject
    holding a shell will outlive a signal sent to the shell alone; the orphans
    then hold the workspace open and corrupt the next run's manifest.

    Escalation is fixed and observable: SIGTERM to the group, `termination_grace`
    seconds, SIGKILL to the group, then record whether anything survived. The
    surviving-group observation is returned rather than raised, because a leaked
    process is evidence about the subject and callers must be able to record it.

    Output past a limit is dropped, not buffered: memory stays bounded by the
    limits themselves, and `*_source_bytes` preserves how much was discarded.

    Raises CaptureError only for a request that cannot be measured -- a
    nonpositive timeout or limit. Everything the child does is returned.
    """
    if timeout <= 0 or stdout_limit <= 0 or stderr_limit <= 0 or termination_grace <= 0:
        raise CaptureError("process timeout and capture limits must be positive")

    argv = list(argv)
    forwarded: list[int] = []
    process: subprocess.Popen[bytes] | None = None
    previous: dict[int, Any] = {}

    # Signal handlers may only be installed from the main thread. A soak or a
    # server calling this from a worker must still get a bounded run, so
    # forwarding degrades rather than raising -- and `forwarded_signals` stays
    # empty, which is an honest record of what was observed.
    installing = forward_signals and threading.current_thread() is threading.main_thread()

    def _forward(signum: int, _frame: Any) -> None:
        forwarded.append(signum)
        if process is not None and process.poll() is None:
            _signal_group(process.pid, signal.SIGTERM)

    if installing:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _forward)

    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    overflow = {"stdout": False, "stderr": False}
    source = {"stdout": 0, "stderr": 0}
    reason: str | None = None
    sent_at: float | None = None
    alive_before = False
    alive_after = False

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + timeout

        def terminate(why: str) -> None:
            nonlocal reason, sent_at
            if reason is not None:
                return
            reason = why
            sent_at = time.monotonic()
            if process is not None and process.poll() is None:
                _signal_group(process.pid, signal.SIGTERM)

        try:
            while selector.get_map():
                now = time.monotonic()
                if reason is None and now >= deadline:
                    terminate(TIMEOUT)
                if reason is None and forwarded:
                    terminate(SIGNALLED)
                if (
                    reason is not None
                    and sent_at is not None
                    and now - sent_at >= termination_grace
                ):
                    # The grace expired with the streams still open: the child
                    # ignored SIGTERM, or a grandchild inherited the pipe and
                    # holds it. Stop reading and escalate; waiting on EOF here
                    # is how a bounded run becomes unbounded.
                    if process.poll() is None:
                        _signal_group(process.pid, signal.SIGKILL)
                    break
                read_any = False
                for key, _ in selector.select(timeout=0.05):
                    stream = key.data
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65_536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        selector.unregister(key.fileobj)
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    read_any = True
                    source[stream] += len(chunk)
                    room = limits[stream] - len(buffers[stream])
                    if room > 0:
                        buffers[stream].extend(chunk[:room])
                    if len(chunk) > room:
                        overflow[stream] = True
                        terminate(
                            STDOUT_LIMIT if stream == "stdout" else STDERR_LIMIT
                        )
                if process.poll() is not None and not read_any:
                    # The child is gone and the pipe is drained -- anything
                    # still holding it is a grandchild that outlived its parent.
                    # Reading to EOF here would block on a process this run does
                    # not control, so stop: the orphan is a finding, reported by
                    # the group check below, not something to wait for.
                    break
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
    finally:
        if process is not None:
            # Reap the child *before* sampling the group. The child leads its
            # own group, so a not-yet-reaped exit status makes the group look
            # occupied and turns every clean run into a reported orphan.
            if process.poll() is None:
                if not _wait_for_group_exit(process.pid, termination_grace):
                    _signal_group(process.pid, signal.SIGKILL)
                    if not _wait_for_group_exit(process.pid, termination_grace):
                        process.kill()
            process.wait()
            alive_before = _group_alive(process.pid)
            if alive_before:
                _signal_group(process.pid, signal.SIGTERM)
                if not _wait_for_group_exit(process.pid, termination_grace):
                    _signal_group(process.pid, signal.SIGKILL)
                    _wait_for_group_exit(process.pid, termination_grace)
            alive_after = _group_alive(process.pid)
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    return Bounded(
        argv=argv,
        returncode=process.returncode if process is not None else -1,
        termination_reason=reason,
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
        stdout_source_bytes=source["stdout"],
        stderr_source_bytes=source["stderr"],
        stdout_overflow=overflow["stdout"],
        stderr_overflow=overflow["stderr"],
        group_alive_before_cleanup=alive_before,
        group_alive_after_cleanup=alive_after,
        forwarded_signals=tuple(forwarded),
    )


def credential_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    """The values worth scrubbing from captured bytes, longest first.

    Matched on the *name* and scrubbed by *value*, because a subject echoes the
    value. Sorted longest-first so a secret that contains a shorter secret is
    replaced whole -- redacting the short one first would leave the tail of the
    long one in the evidence, which reads as safe and is not.
    """
    values = {
        value
        for name, value in environment.items()
        if any(marker in name.upper() for marker in CREDENTIAL_MARKERS)
        and isinstance(value, str)
        and len(value) >= CREDENTIAL_MIN_LENGTH
    }
    return tuple(sorted(values, key=len, reverse=True))


def redact_bytes(raw: bytes, values: Sequence[str]) -> tuple[bytes, int]:
    """Replace each value with `[REDACTED]`, returning the bytes and a count.

    The count is not decoration. Scrubbed bytes and bytes that never held a
    secret are otherwise indistinguishable, and their digests differ for a
    reason nothing records.

    Each value is also matched in its JSON-escaped form: a subject that logs a
    credential inside a JSON string emits escapes the raw value does not
    contain, and matching only the raw form leaks exactly the case most likely
    to occur.
    """
    redacted = raw
    count = 0
    for value in values:
        variants = {
            value.encode("utf-8"),
            json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"),
        }
        for variant in sorted(variants, key=len, reverse=True):
            if not variant:
                continue
            occurrences = redacted.count(variant)
            if occurrences:
                redacted = redacted.replace(variant, b"[REDACTED]")
                count += occurrences
    return redacted, count


def capture_bytes(
    raw: bytes,
    *,
    redactions: Sequence[str] = (),
    source_bytes: int | None = None,
) -> dict[str, Any]:
    """The stored-evidence envelope: what was kept, what it came from, its digest.

    `sha256` is of the bytes as *stored*, after redaction -- the digest must
    describe the artefact that exists, or verifying it against the record fails
    for a scrubbed capture that is perfectly correct.

    `text` is None for bytes that are not valid UTF-8 rather than being coerced.
    A subject that emits broken encoding has told you something, and
    `errors="replace"` would silently discard it while changing the bytes a
    reader compares against the digest.
    """
    stored, redaction_count = redact_bytes(raw, redactions)
    envelope: dict[str, Any] = {
        "bytes": len(stored),
        "source_bytes": len(raw) if source_bytes is None else source_bytes,
        "sha256": digest_bytes(stored),
        "base64": base64.b64encode(stored).decode("ascii"),
        "redaction_count": redaction_count,
    }
    try:
        envelope["text"] = stored.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        envelope["text"] = None
    return envelope


def parse_jsonl(
    raw: bytes, *, objects_only: bool = False
) -> tuple[list[Any], list[str]]:
    """Decode line-delimited JSON, collecting errors instead of raising.

    Malformed evidence is the normal case, not the exceptional one: a subject
    killed mid-write leaves a truncated final line. Returning the records that
    did parse alongside the complaints keeps a partial run analysable, where
    raising would discard every valid record because the last one was cut off.
    """
    records: list[Any] = []
    errors: list[str] = []
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        return [], [f"evidence is not UTF-8: {error}"]
    for number, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append(f"line {number} is not JSON: {error.msg}")
            continue
        if objects_only and not isinstance(record, dict):
            errors.append(f"line {number} is not a JSON object")
            continue
        records.append(record)
    return records, errors


def capture_file(
    path: Path,
    *,
    required: bool,
    format_name: str = "bytes",
    max_bytes: int = DEFAULT_SIDECAR_LIMIT,
    redactions: Sequence[str] = (),
) -> dict[str, Any]:
    """Capture a sidecar file, recording its absence as a state rather than an error.

    A missing required file yields `exists: False` and an entry in `errors`; it
    does not raise. A run that failed to instrument must stay comparable to one
    that succeeded, and an exception here would remove it from the comparison
    entirely -- which is how an instrumentation failure becomes invisible.

    `format_name` of `utf8` or `jsonl` turns a decode failure into a recorded
    error. `bytes` makes no claim, so binary evidence is not reported as broken.
    Oversize evidence is refused rather than truncated: a sidecar is usually a
    log whose meaning depends on being whole, and half a session log invites
    conclusions the bytes do not support.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise CaptureError("sidecar max_bytes must be a positive integer")

    errors: list[str] = []
    regular = path.is_file() and not path.is_symlink()
    exists = path.exists()
    raw: bytes | None = b""
    size = 0

    if not exists:
        if required:
            errors.append("required evidence file was not created")
    elif not regular:
        errors.append("evidence path is not a regular file")
    else:
        size = path.stat().st_size
        if size > max_bytes:
            errors.append(
                f"evidence exceeds {max_bytes}-byte capture limit: {size} bytes"
            )
            raw = None
        else:
            raw = path.read_bytes()

    envelope = capture_bytes(
        raw if raw is not None else b"",
        redactions=redactions,
        source_bytes=size,
    )
    if raw is None:
        # Nothing was stored, so the stored-bytes digest describes emptiness.
        # The file's own digest is still recorded, because "too big to keep" and
        # "not there" must not produce the same evidence.
        envelope["base64"] = None
        envelope["text"] = None
    envelope.update(
        {
            "exists": exists,
            "format": format_name,
            "size": size,
            "max_bytes": max_bytes,
            "file_sha256": digest_file(path) if regular else None,
            "jsonl": None,
            "errors": errors,
        }
    )

    if raw is not None and exists and not errors:
        if format_name in {"utf8", "jsonl"} and envelope["text"] is None:
            errors.append("evidence is not UTF-8")
        if format_name == "jsonl" and envelope["text"] is not None:
            records, decode_errors = parse_jsonl(raw)
            envelope["jsonl"] = records
            errors.extend(decode_errors)
    return envelope


def manifest(root: Path) -> list[dict[str, Any]]:
    """Every regular file below `root`, sorted, with mode and digest.

    Collected by the adapter from *outside* the subject, which is the only
    reason it is evidence: a subject's own report that it wrote a file is a
    claim, and this is the independent check on it.

    Symlinks are skipped rather than followed. Following one would digest bytes
    from outside the tree and attribute them to a path inside it.
    """
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "mode": stat.st_mode & 0o777,
                "size": stat.st_size,
                "sha256": digest_file(path),
            }
        )
    return entries


def minimal_environment(
    root: Path,
    overrides: Mapping[str, str] | None = None,
    *,
    passthrough: Iterable[str] = PASSTHROUGH_NAMES,
    home: str = "home",
) -> dict[str, str]:
    """Build a child environment from an allowlist plus a disposable HOME.

    This is the half of credential hygiene that `credential_values` cannot do.
    Scrubbing catches a secret the subject echoes; it cannot catch one the
    subject writes into a file, or uses to reach the network. Not passing the
    secret in the first place covers what scrubbing misses, and scrubbing covers
    the secret a subject legitimately needs. Neither alone is sufficient, which
    is why both ship.

    HOME is redirected into `root` so a subject that writes config, caches
    credentials, or drops a session log does it somewhere the caller can
    manifest and discard -- not into the operator's real home directory.

    This is disclosure, not containment. Nothing here stops a subject that
    chooses to read an absolute path.
    """
    env: dict[str, str] = {}
    for name in passthrough:
        value = os.environ.get(name)
        if value:
            env[name] = value
    fake_home = root / home
    fake_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(fake_home)
    if overrides:
        env.update(overrides)
    return env


def contained_path(root: Path, value: Any, *, label: str = "path") -> Path:
    """Resolve a caller-declared relative path, refusing anything that escapes.

    Rejects absolute paths and any `..` component before touching the
    filesystem. Adapters take paths from config and from subjects, and subjects
    do propose paths outside the workspace -- one of them did it during probing.

    Raises CaptureError, because unlike a subject's behaviour this is a request
    that cannot be honoured at all.
    """
    if not isinstance(value, str) or not value:
        raise CaptureError(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise CaptureError(f"{label} must stay below its declared root: {value}")
    return root / relative


def relative_to_root(raw: Any, root: Path) -> str | None:
    """Describe a path a *subject* reported, relative to the workspace.

    Returns `<outside-workspace>` rather than raising or dropping the entry: a
    subject naming a path it should not have named is a finding, and both
    discarding it and crashing on it would destroy that finding. None means the
    subject did not report a string at all.
    """
    if not isinstance(raw, str):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "<outside-workspace>"


__all__ = [
    "Bounded",
    "CaptureError",
    "CREDENTIAL_MARKERS",
    "CREDENTIAL_MIN_LENGTH",
    "DEFAULT_SIDECAR_LIMIT",
    "DEFAULT_STDERR_LIMIT",
    "DEFAULT_STDOUT_LIMIT",
    "PASSTHROUGH_NAMES",
    "SIGNALLED",
    "STDERR_LIMIT",
    "STDOUT_LIMIT",
    "TIMEOUT",
    "capture_bytes",
    "capture_file",
    "contained_path",
    "credential_values",
    "digest_bytes",
    "digest_file",
    "manifest",
    "minimal_environment",
    "parse_jsonl",
    "redact_bytes",
    "relative_to_root",
    "run_bounded",
]
