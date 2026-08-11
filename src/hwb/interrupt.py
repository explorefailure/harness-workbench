"""Bounded direct-child interruption at durable runner checkpoints.

Family 14 asks a narrower question than arbitrary crash testing:

    At each named closed-file boundary, what state does the run store expose
    after the runner process is terminated?

The parent never guesses when to kill.  A child publishes an atomic marker at
one checkpoint and blocks; only then does the parent send SIGTERM (or the
platform's direct-child terminate equivalent).  This covers named boundaries,
not the intervals between them, power loss, storage-controller durability, or
descendant cleanup.

The state vocabulary is intentionally separate from ``record.status``:

``absent``
    The announced run directory does not exist.
``incomplete``
    Evidence exists, but there is no readable conforming completed record, or
    an integrity baseline exists and disagrees with the complete file set.
``recoverable``
    A conforming completed record and its artifacts are readable, but no
    integrity baseline closed the run.  This means evidence can be inspected;
    it does NOT mean execution can resume or the baseline can be regenerated
    with its original authority.
``complete``
    The record conforms to its named store directory and an exhaustive
    regular-file integrity inventory verifies clean. Unsupported non-regular
    nodes prevent this state.

Nothing here deletes, repairs, quarantines, or resumes an interrupted run.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .canon import canon_bytes
from .runner import (_CHECKPOINT_ENV, _CHECKPOINT_MARKER_ENV, _stamp)

ABSENT = "absent"
INCOMPLETE = "incomplete"
RECOVERABLE = "recoverable"
COMPLETE = "complete"

PASSED = "passed"
VIOLATIONS = "VIOLATIONS"
SETUP_ERROR = "setup_error"

DEFAULT_TIMEOUT_SECONDS = 30.0

# Ordered as the writer reaches them.  The state on the right is what may be
# visible after termination at that exact boundary.
CHECKPOINTS: Tuple[Tuple[str, str], ...] = (
    ("before_run_directory", ABSENT),
    ("run_directory_created", INCOMPLETE),
    ("inputs_preserved", INCOMPLETE),
    ("attempt_artifacts_written", INCOMPLETE),
    ("attempts_finalising_written", INCOMPLETE),
    ("attempts_finalised", INCOMPLETE),
    ("record_written", RECOVERABLE),
    ("integrity_written", COMPLETE),
)

UNOBSERVED_CLASSES = (
    "termination between named checkpoints rather than at a published boundary",
    "power loss, kernel crash, filesystem cache loss, fsync, and storage durability",
    "descendant-process lifetime and process-tree cleanup; only the runner child is observed",
    "network, remote-service, IPC, and lock cleanup",
    "automatic resume, repair, quarantine, or deletion; none is attempted",
)


class InterruptError(Exception):
    """The campaign itself could not be constructed safely."""


def _inventory(run_dir: str) -> List[str]:
    if not os.path.isdir(run_dir):
        return []
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(run_dir):
        dirnames[:] = sorted(dirnames)
        for name in sorted(filenames):
            out.append(os.path.relpath(os.path.join(dirpath, name), run_dir))
    return out


def inspect_state(run_dir: str) -> Dict[str, Any]:
    """Classify one announced run path without mutating it."""
    from . import conform, runner

    absolute = os.path.abspath(run_dir)
    if not os.path.isdir(absolute):
        return {"state": ABSENT, "run_path": absolute, "record": None,
                "inventory": [], "reasons": ["run directory is absent"],
                "integrity": None}

    inventory = _inventory(absolute)
    record_path = os.path.join(absolute, "record.json")
    attempts_path = os.path.join(absolute, "attempts.jsonl")
    if not os.path.isfile(record_path):
        return {"state": INCOMPLETE, "run_path": absolute, "record": None,
                "inventory": inventory,
                "reasons": ["record.json has not closed"], "integrity": None}

    try:
        with open(record_path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as e:
        return {"state": INCOMPLETE, "run_path": absolute, "record": None,
                "inventory": inventory,
                "reasons": ["record.json is unreadable: %s" % e],
                "integrity": None}

    attempts: List[Dict[str, Any]] = []
    try:
        if os.path.isfile(attempts_path):
            with open(attempts_path, "r", encoding="utf-8") as fh:
                attempts = [json.loads(line) for line in fh if line.strip()]
        conform.validate_record(record, attempts, run_dir=absolute)
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            conform.NonConforming) as e:
        return {"state": INCOMPLETE, "run_path": absolute, "record": record,
                "inventory": inventory,
                "reasons": ["record does not conform: %s" % e],
                "integrity": None}

    if record.get("status") != "completed":
        return {"state": INCOMPLETE, "run_path": absolute, "record": record,
                "inventory": inventory,
                "reasons": ["record status is %r, not 'completed'"
                            % record.get("status")], "integrity": None}

    try:
        integrity = runner.verify(absolute)
    except OSError as e:
        return {"state": INCOMPLETE, "run_path": absolute, "record": record,
                "inventory": inventory,
                "reasons": ["integrity could not be read: %s" % e],
                "integrity": None}
    if integrity["state"] == "baseline_missing":
        return {"state": RECOVERABLE, "run_path": absolute, "record": record,
                "inventory": inventory,
                "reasons": ["conforming evidence exists but integrity.json has not closed"],
                "integrity": integrity}
    if integrity["state"] != "clean":
        detail = integrity.get("error") or "integrity baseline disagrees"
        return {"state": INCOMPLETE, "run_path": absolute, "record": record,
                "inventory": inventory,
                "reasons": [detail], "integrity": integrity}
    return {"state": COMPLETE, "run_path": absolute, "record": record,
            "inventory": inventory, "reasons": [], "integrity": integrity}


def _child_result(returncode: Optional[int], terminated: bool,
                  escalated: bool = False) -> Dict[str, Any]:
    signum = -returncode if returncode is not None and returncode < 0 else None
    signal_name = None
    if signum is not None:
        try:
            signal_name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            signal_name = "signal-%d" % signum
    if signal_name:
        result = "signal"
    elif returncode == 0:
        result = "exited"
    elif returncode is None:
        result = "unknown"
    else:
        result = "nonzero_exit"
    return {"result": result, "returncode": returncode,
            "signal": signal_name, "terminate_requested": terminated,
            "kill_escalated": escalated}


def _wait_for_marker(proc: subprocess.Popen, marker: str,
                     timeout_seconds: float) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if os.path.isfile(marker):
            try:
                with open(marker, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError) as e:
                # Return an error payload rather than raising while the child
                # remains blocked forever at the checkpoint.
                return {"checkpoint": None, "run_dir": None,
                        "marker_error": str(e)}
        if proc.poll() is not None:
            return None
        # This poll does not select the interruption point: the child remains
        # blocked after publishing the marker until this parent terminates it.
        time.sleep(0.01)
    return None


def _terminate(proc: subprocess.Popen) -> Tuple[bytes, bytes, bool]:
    proc.terminate()
    escalated = False
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        escalated = True
        proc.kill()
        out, err = proc.communicate()
    return out, err, escalated


def _command(spec_path: str, runs_root: str) -> List[str]:
    return [sys.executable, "-m", "hwb", "--root", os.path.abspath(runs_root),
            "run", os.path.abspath(spec_path)]


def _environment(checkpoint: Optional[str], marker: Optional[str]) -> Dict[str, str]:
    env = dict(os.environ)
    # Tests import from src without requiring an editable install.  Preserve
    # that property for the campaign's real child interpreter too.
    src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_root + (os.pathsep + old if old else "")
    if checkpoint is not None and marker is not None:
        env[_CHECKPOINT_ENV] = checkpoint
        env[_CHECKPOINT_MARKER_ENV] = marker
    else:
        env.pop(_CHECKPOINT_ENV, None)
        env.pop(_CHECKPOINT_MARKER_ENV, None)
    return env


def _bounded_text(data: bytes) -> str:
    return data.decode("utf-8", "replace")[-1000:]


def _run_checkpoint(spec_path: str, runs_root: str, work: str,
                    checkpoint: str, expected: str,
                    timeout_seconds: float) -> Dict[str, Any]:
    marker = os.path.join(work, "reached.json")
    proc = subprocess.Popen(
        _command(spec_path, runs_root), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=_environment(checkpoint, marker))
    reached = _wait_for_marker(proc, marker, timeout_seconds)
    terminated = False
    escalated = False
    if reached is not None:
        terminated = True
        out, err, escalated = _terminate(proc)
    else:
        try:
            out, err = proc.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            terminated = True
            out, err, escalated = _terminate(proc)

    run_path = reached.get("run_dir") if reached else None
    observed = (inspect_state(run_path) if run_path else
                {"state": ABSENT, "run_path": None, "record": None,
                 "inventory": [], "reasons": ["checkpoint marker was not published"],
                 "integrity": None})
    violations: List[str] = []
    if reached is None:
        violations.append("child did not publish checkpoint within the bound")
    elif reached.get("checkpoint") != checkpoint:
        violations.append("child published checkpoint %r" % reached.get("checkpoint"))
    if reached is not None and reached.get("marker_error"):
        violations.append("checkpoint marker is unreadable: %s"
                          % reached["marker_error"])
    if observed["state"] != expected:
        violations.append("expected state %s, observed %s"
                          % (expected, observed["state"]))
    if reached is not None and not terminated:
        violations.append("checkpoint child was not externally terminated")
    return {
        "checkpoint": checkpoint,
        "control": ("positive" if checkpoint == "integrity_written"
                    else "negative"),
        "expected_state": expected,
        "child": _child_result(proc.returncode, terminated, escalated),
        "run_path": run_path,
        "observed_state": observed["state"],
        "observed_inventory": observed["inventory"],
        "state_reasons": observed["reasons"],
        "violations": violations,
        "child_stdout": _bounded_text(out),
        "child_stderr": _bounded_text(err),
    }


def _run_uninterrupted(spec_path: str, runs_root: str,
                       timeout_seconds: float) -> Dict[str, Any]:
    before = set(os.listdir(runs_root)) if os.path.isdir(runs_root) else set()
    try:
        proc = subprocess.run(
            _command(spec_path, runs_root), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=_environment(None, None),
            timeout=timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired as e:
        proc = e
        timed_out = True
    after = set(os.listdir(runs_root)) if os.path.isdir(runs_root) else set()
    created = sorted(after - before)
    run_path = (os.path.join(runs_root, created[0]) if len(created) == 1 else None)
    observed = (inspect_state(run_path) if run_path else
                {"state": ABSENT, "inventory": [],
                 "reasons": ["control created %d run directories" % len(created)]})
    returncode = None if timed_out else proc.returncode
    out = proc.stdout or b""
    err = proc.stderr or b""
    violations: List[str] = []
    if timed_out:
        violations.append("uninterrupted control exceeded the child timeout")
    if returncode != 0:
        violations.append("uninterrupted control child did not exit zero")
    if observed["state"] != COMPLETE:
        violations.append("uninterrupted control did not produce a complete run")
    return {
        "checkpoint": "uninterrupted_control",
        "control": "positive",
        "expected_state": COMPLETE,
        "child": _child_result(returncode, False),
        "run_path": run_path,
        "observed_state": observed["state"],
        "observed_inventory": observed["inventory"],
        "state_reasons": observed["reasons"],
        "violations": violations,
        "child_stdout": _bounded_text(out),
        "child_stderr": _bounded_text(err),
    }


def campaign(spec_path: str, runs_root: str, interrupt_root: str,
             timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    from . import spec as specmod, stores

    if timeout_seconds <= 0:
        raise InterruptError("timeout_seconds must be greater than zero")
    try:
        stores.require_disjoint(runs_root, interrupt_root,
                                "interruption-campaign store")
    except stores.StoreOverlapError as e:
        raise InterruptError(str(e))
    try:
        base = specmod.load(spec_path)
    except specmod.SpecError as e:
        raise InterruptError(str(e))

    campaign_id = "%s-%s" % (_stamp(), uuid.uuid4().hex[:6])
    cdir = os.path.join(interrupt_root, campaign_id)
    try:
        os.makedirs(cdir)
        os.makedirs(runs_root, exist_ok=True)
    except OSError as e:
        raise InterruptError("cannot create interruption campaign: %s" % e)

    rows: List[Dict[str, Any]] = []
    for i, (checkpoint, expected) in enumerate(CHECKPOINTS):
        work = os.path.join(cdir, "%02d-%s" % (i, checkpoint))
        os.makedirs(work)
        rows.append(_run_checkpoint(spec_path, runs_root, work, checkpoint,
                                    expected, timeout_seconds))
    rows.append(_run_uninterrupted(spec_path, runs_root, timeout_seconds))

    violations = ["%s: %s" % (row["checkpoint"], violation)
                  for row in rows for violation in row["violations"]]
    setup = [row for row in rows
             if "did not publish checkpoint" in " ".join(row["violations"])
             or "marker is unreadable" in " ".join(row["violations"])
             or "control child did not exit zero" in " ".join(row["violations"])]
    verdict = SETUP_ERROR if setup else (VIOLATIONS if violations else PASSED)
    manifest: Dict[str, Any] = {
        "schema": "hwbinterrupt/v0.1",
        "campaign_id": campaign_id,
        "base_spec": os.path.abspath(spec_path),
        "base_spec_digest": base.digest,
        "runs_root": os.path.abspath(runs_root),
        "checkpoint_protocol": "atomic-marker-then-direct-child-terminate/0.1",
        "timeout_seconds": timeout_seconds,
        "state_oracle": {
            ABSENT: "announced run directory does not exist",
            INCOMPLETE: "evidence exists without a conforming, integrity-closed run",
            RECOVERABLE: "conforming evidence is readable but integrity is not closed; not resumable",
            COMPLETE: "conforming record plus clean exhaustive regular-file integrity inventory",
        },
        "checkpoints": rows,
        "violations": violations,
        "unobserved": list(UNOBSERVED_CLASSES),
        "verdict": verdict,
    }
    with open(os.path.join(cdir, "campaign.json"), "wb") as fh:
        fh.write(canon_bytes(manifest))
    return manifest
