"""The run loop and the record.

Two things this module refuses to do:
  * collapse attempts (they are append-only, one line each, never reduced)
  * treat a non-zero exit as an error (that is data; only harness failures raise)

WHAT AN ABSENT FIELD MEANS -- the rule is PER FIELD, and that is the hazard.
Unknown keys are always ignored, so fields arrive over time and every reader
meets records written before they existed. Getting this wrong is silent:

  caused_by      absent = provenance WAS NOT RECORDED.
                 It must NOT be read as "no wrap feature ran". Records
                 written before the field existed carry no stack, and
                 treating that as "unwrapped" makes two incomparable runs
                 look equal -- which corrupts exactly the ordering
                 comparisons the field was added for.
  timed_out      absent = the attempt did not time out. Safe to assume.
  seam_timings   absent or empty = no hook was dispatched. Safe to assume.
  replicates     null = this run makes no reproduction claim. Safe.
  attempt_artifact_contract
                 absent = attempt descriptors predate close-time sealing;
                 byte counts may describe capture-time buffers and digests
                 may be absent. It must NOT be read as final agreement.

The distinction: a field that records something POSITIVE and rare (a
timeout) is safe to read as absent-means-no. A field that records something
STRUCTURAL and usual (what caused an attempt) is not, because its absence is
indistinguishable from the thing not being tracked yet. New fields should be
classified here when they are added.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .canon import canon_bytes, digest_file, short
from .seams import Dispatcher

RECORD_SCHEMA = "hwbrun/v0.1"
SEAM_CONTRACT = "seams/0.2.0"
ATTEMPT_ARTIFACT_CONTRACT = "attempt-artifacts/0.1"

# Private coordination used by the bounded interruption campaign.  This is
# deliberately not a spec field or public run option: checkpoints are an
# instrument attached by ``hwb interrupt``, not behaviour a workload can ask
# the harness to perform.
_CHECKPOINT_ENV = "HWB_LIFECYCLE_CHECKPOINT"
_CHECKPOINT_MARKER_ENV = "HWB_LIFECYCLE_MARKER"


def _lifecycle_checkpoint(name: str, run_dir: str) -> None:
    """Publish one named, closed-file boundary and wait to be terminated.

    The marker is written atomically before the wait.  The interruption
    campaign therefore kills only after it has evidence that the child
    reached the requested boundary; no sleep duration chooses the kill point.
    Outside that campaign both environment variables are absent and this is a
    very small equality check.
    """
    if os.environ.get(_CHECKPOINT_ENV) != name:
        return
    marker = os.environ.get(_CHECKPOINT_MARKER_ENV)
    if not marker:
        raise HarnessError("lifecycle checkpoint %s has no marker path" % name)
    payload = {"checkpoint": name, "pid": os.getpid(),
               "run_dir": os.path.abspath(run_dir)}
    tmp = marker + ".writing"
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    with open(tmp, "wb") as fh:
        fh.write(canon_bytes(payload))
    os.replace(tmp, marker)
    # The parent owns release by terminating this process.  An Event avoids a
    # timing-based sleep and cannot return spuriously on its own.
    threading.Event().wait()


def _utc() -> str:
    """Millisecond resolution, deliberately.

    Second resolution made feature overhead unmeasurable: a seam dispatch
    costs microseconds and a run-level delta of "0 seconds" says nothing.
    Both forms are valid ISO 8601 and any conformant parser reads the old
    records unchanged, so this is a value-format refinement rather than a
    schema change.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _stamp() -> str:
    """Run-id stamp stays at second resolution — the uuid suffix already
    supplies uniqueness, and widening it would churn every run id."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class HarnessError(Exception):
    """The harness broke. Distinct from a step exiting non-zero."""


class Recorder:
    """Owns the run directory and everything written into it."""

    def __init__(self, root: str, spec):
        self.spec = spec
        # Timestamp + spec digest is NOT unique: the digest is identical
        # across runs of one spec (that is the point of it), and the stamp
        # has second resolution -- so back-to-back runs collided. Both
        # precedents add an opaque component: run.mjs uses a uuid slice,
        # Inspect a trailing id. Found by running it twice quickly.
        self.run_id = "%s-%s-%s" % (_stamp(), short(spec.digest),
                                    uuid.uuid4().hex[:4])
        self.run_dir = os.path.join(root, self.run_id)
        _lifecycle_checkpoint("before_run_directory", self.run_dir)
        try:
            os.makedirs(self.run_dir, exist_ok=False)
        except FileExistsError:
            raise HarnessError("run directory already exists: %s" % self.run_dir)
        self._attempts = open(os.path.join(self.run_dir, "attempts.jsonl"),
                              "a", encoding="utf-8")
        _lifecycle_checkpoint("run_directory_created", self.run_dir)
        self._extras: Dict[str, Dict[str, Any]] = {}
        self._failed_steps: List[Dict[str, str]] = []
        self._frames: List[Dict[str, Any]] = []
        self._timings: Dict[str, Dict[str, Any]] = {}
        self.started_at = _utc()

    # ---- preservation --------------------------------------------------
    def preserve(self, loaded) -> None:
        """Copy the run's INPUTS into the store.

        The record fingerprints the spec and each feature tree, but both
        lived outside the run and were never captured -- so `spec_digest`
        and `features[].digest` were sound claims that nothing could check.
        Demonstrated: a spec rewritten after its run (different steps,
        different class, features removed) still verified clean.

        Store the referent and the claim becomes checkable. Both are small,
        both are what the base itself resolved, and `_write_integrity` walks
        the directory afterwards so the copies are covered automatically.

        NOT stored here: declared step inputs. Those are unbounded in size
        (a prompt is bytes, a model file is gigabytes), and a feature that
        digests them is better placed to decide a size policy than the base.
        """
        import shutil

        shutil.copyfile(self.spec.path, os.path.join(self.run_dir, "spec.json"))

        if not loaded:
            return
        root = os.path.join(self.run_dir, "features")
        os.makedirs(root, exist_ok=True)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".git")
        for f in loaded:
            shutil.copytree(f.manifest.root, os.path.join(root, f.name),
                            ignore=ignore)

    # ---- feature-facing ------------------------------------------------
    def extras(self, feature: str) -> Dict[str, Any]:
        """Each feature writes under its own key and nowhere else."""
        return self._extras.setdefault(feature, {})

    # ---- wrap provenance -----------------------------------------------
    # Without this, retry(sample(step)) and sample(retry(step)) produce
    # byte-identical attempt streams: a flat 0..N counter cannot say which
    # mechanism caused an attempt. Prior art agrees on flat-plus-ordinal --
    # OpenTelemetry gives each resend its own span carrying
    # `http.request.resend_count`, and Inspect keeps samples flat with an
    # `epoch` field -- so this stays append-only and adds a key rather than
    # nesting. Outermost wrap first.
    def push_frame(self, frame: Dict[str, Any]) -> None:
        self._frames.append(frame)

    def pop_frame(self) -> None:
        if self._frames:
            self._frames.pop()

    def frames(self) -> List[Dict[str, Any]]:
        """Snapshot -- callers mutate their own frame's counter in place."""
        return [dict(f) for f in self._frames]

    # ---- seam timing ---------------------------------------------------
    def note_seam(self, feature: str, seam: str, elapsed_ms: float) -> None:
        """Measured inside the dispatcher because it cannot be inferred:
        a model call is seconds and a dispatch is microseconds, so a
        wall-clock delta between configurations is all workload noise."""
        row = self._timings.setdefault(feature, {})
        cell = row.setdefault(seam, {"calls": 0, "total_ms": 0.0})
        cell["calls"] += 1
        cell["total_ms"] = round(cell["total_ms"] + elapsed_ms, 3)

    def extras_view(self) -> Dict[str, Any]:
        """Read access to what other features have written. Features talk
        through the record, never by importing each other — so this is the
        only channel, and the coupling it creates is visible as data."""
        return self._extras

    def note_step_failed(self, step_id: str, feature: str) -> None:
        self._failed_steps.append({"step_id": step_id, "by": feature})

    # ---- attempts ------------------------------------------------------
    def append_attempt(self, rec: Dict[str, Any]) -> None:
        self._attempts.write(json.dumps(rec, sort_keys=True) + "\n")
        self._attempts.flush()

    def attempt_dir(self, step_id: str, n: int) -> str:
        d = os.path.join(self.run_dir, "steps", step_id, "attempts", str(n))
        os.makedirs(d, exist_ok=True)
        return d

    # ---- close ---------------------------------------------------------
    def close(self, status: str, features, gates=None) -> Dict[str, Any]:
        self._attempts.close()
        self._finalise_attempt_artifacts()
        # A DECLARED variable that is unset records as null, not absent.
        # Iterating os.environ alone made "the spec declared nothing" and
        # "the spec declared OLLAMA_HOST and it was unset" produce the same
        # empty dict -- so the record could not say whether the author had
        # thought about the environment at all. Found by fidelity reporting
        # `nothing declared` for a spec that declares one variable.
        env_declared = {k: os.environ.get(k) for k in self.spec.env}
        env_other = [k for k in os.environ if k not in self.spec.env]
        record = {
            "schema": RECORD_SCHEMA,
            "run_id": self.run_id,
            "run_class": self.spec.run_class,
            "replicates": self.spec.raw.get("replicates"),
            "spec_digest": self.spec.digest,
            # WHERE the run happened. Steps execute with `cwd=spec.dir` and
            # `steps[].inputs` resolve against it, so the preserved spec on
            # its own does NOT describe a reproducible run -- `replay` found
            # that by needing a `--in` argument the record could not supply,
            # while `fidelity` was reporting reproducibility as answered.
            #
            # Recorded, and MASKED at comparison time: a path is where the
            # experiment sat, not what it was. Two runs of one spec from two
            # checkouts are the same condition, and their inputs are pinned
            # by digest anyway -- a real divergence there is caught by
            # `_digest_conflict`, which refuses rather than compares.
            "spec_path": os.path.abspath(self.spec.path),
            "seam_contract": SEAM_CONTRACT,
            # Attempt lines are written while the run is live, but a wrap
            # may legitimately inspect (or, today, rewrite) the captured
            # files after the step returns. The descriptors are therefore
            # finalised only after every feature hook has completed. This
            # contract tells readers that byte counts and digests describe
            # the bytes that were actually sealed into the run directory.
            "attempt_artifact_contract": ATTEMPT_ARTIFACT_CONTRACT,
            # WHERE the features came from. What RAN is already
            # identified by each feature's own digest; this answers the
            # different question of which route supplied it -- the
            # shipped tree, the spec's own folder, a declared root, or
            # an environment override. Provenance, not identity, and a
            # reader should not have to infer it from a path.
            "features_source": featmod.source_of(
                self.spec.dir, getattr(self.spec, "features_root", None)),
            "started_at": self.started_at,
            "ended_at": _utc(),
            "status": status,
            "features": [f.as_record() for f in features],
            "gates": gates or [],
            "env": {"declared": env_declared, "undeclared_names": sorted(env_other)},
            "steps": [s.as_record() for s in self.spec.steps],
            "failed_steps": self._failed_steps,
            "seam_timings": self._timings,
            "extras": self._extras,
        }
        path = os.path.join(self.run_dir, "record.json")
        with open(path, "wb") as fh:
            fh.write(canon_bytes(record))
        _lifecycle_checkpoint("record_written", self.run_dir)
        _write_integrity(self.run_dir)
        _lifecycle_checkpoint("integrity_written", self.run_dir)
        return record

    def _finalise_attempt_artifacts(self) -> None:
        """Seal each executed attempt to the stdout/stderr bytes on disk.

        Capture-time lengths are provisional: an outer wrap regains control
        after ``run_step`` and can change the files before the run closes.
        Re-reading here makes the append-only attempt stream describe the
        final artifacts rather than the earlier in-memory byte strings.

        This proves agreement, not authority. It cannot attribute a rewrite
        to a feature; filesystem-effect confinement is a separate campaign.
        """
        path = os.path.join(self.run_dir, "attempts.jsonl")
        with open(path, "r", encoding="utf-8") as fh:
            attempts = [json.loads(line) for line in fh if line.strip()]

        for attempt in attempts:
            if not attempt.get("executed", True):
                continue
            adir = os.path.join(self.run_dir, "steps", str(attempt["step_id"]),
                                "attempts", str(attempt["n"]))
            for stream in ("stdout", "stderr"):
                artifact = os.path.join(adir, stream + ".bin")
                if not os.path.isfile(artifact):
                    raise HarnessError(
                        "attempt %s#%s lost %s.bin before close"
                        % (attempt["step_id"], attempt["n"], stream))
                attempt[stream + "_bytes"] = os.path.getsize(artifact)
                attempt[stream + "_digest"] = digest_file(artifact)

        tmp = path + ".finalising"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                for attempt in attempts:
                    fh.write(json.dumps(attempt, sort_keys=True) + "\n")
            _lifecycle_checkpoint("attempts_finalising_written", self.run_dir)
            os.replace(tmp, path)
            _lifecycle_checkpoint("attempts_finalised", self.run_dir)
        finally:
            if os.path.isfile(tmp):
                os.remove(tmp)


from . import features as featmod  # noqa: E402


def _write_integrity(run_dir: str) -> None:
    """Tamper-EVIDENT, not tamper-proof. The owner can regenerate any
    baseline; what this catches is a record edited after the fact."""
    files = {}
    for dirpath, dirnames, filenames in os.walk(run_dir):
        dirnames[:] = sorted(dirnames)
        for fn in sorted(filenames):
            if fn == "integrity.json":
                continue
            full = os.path.join(dirpath, fn)
            files[os.path.relpath(full, run_dir)] = digest_file(full)
    with open(os.path.join(run_dir, "integrity.json"), "wb") as fh:
        fh.write(canon_bytes({"schema": "integrity/v0.1",
                              "written_at": _utc(), "files": files}))


def verify(run_dir: str) -> Dict[str, Any]:
    path = os.path.join(run_dir, "integrity.json")
    if not os.path.isfile(path):
        return {"state": "baseline_missing", "drifted": [], "missing": [],
                "untracked": [], "error": None}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            base = json.load(fh)
        files = base["files"]
        if not isinstance(files, dict):
            raise ValueError("files is not an object")
        for rel, dig in files.items():
            if not isinstance(rel, str) or not isinstance(dig, str):
                raise ValueError("files entries must map path strings to digests")
            normal = os.path.normpath(rel)
            if os.path.isabs(rel) or normal == ".." or normal.startswith(".." + os.sep):
                raise ValueError("integrity path escapes the run directory: %r" % rel)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"state": "baseline_invalid", "drifted": [], "missing": [],
                "untracked": [], "error": str(e)}
    drifted, missing = [], []
    for rel, dig in sorted(files.items()):
        full = os.path.join(run_dir, rel)
        if not os.path.isfile(full):
            missing.append(rel)
        elif digest_file(full) != dig:
            drifted.append(rel)
    actual = set()
    for dirpath, dirnames, filenames in os.walk(run_dir):
        dirnames[:] = sorted(dirnames)
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if fn != "integrity.json" and os.path.isfile(full):
                actual.add(os.path.relpath(full, run_dir))
    untracked = sorted(actual - set(files))
    state = "clean" if not drifted and not missing and not untracked else "drifted"
    return {"state": state, "drifted": drifted, "missing": missing,
            "untracked": untracked, "error": None}


# ---------------------------------------------------------------- the loop

def execute(spec, loaded, root: str) -> Dict[str, Any]:
    # Before anything is created: an unresolvable reproduction claim must
    # fail without leaving a run directory behind.
    from .spec import validate_replicates, SpecError
    try:
        validate_replicates(spec, root)
    except SpecError as e:
        raise HarnessError(str(e))

    rec = Recorder(root, spec)
    rec.preserve(loaded)
    _lifecycle_checkpoint("inputs_preserved", rec.run_dir)
    disp = Dispatcher(loaded, rec)

    disp.call("on_spec_loaded", spec)
    disp.call("before_run", spec)

    for step in spec.steps:
        disp.call("before_step", step, step_id=step.id)
        counter = {"n": 0}

        def run_once(step=step, counter=counter):
            n = counter["n"]
            counter["n"] += 1
            adir = rec.attempt_dir(step.id, n)
            t0 = time.monotonic()
            started = _utc()
            timed_out = False
            timeout_s = (spec.step_timeout_ms / 1000.0
                         if spec.step_timeout_ms else None)
            try:
                proc = subprocess.run(step.argv, cwd=spec.dir,
                                      capture_output=True, timeout=timeout_s)
                exit_code = proc.returncode
                out, err = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as e:
                # A step that never returns is the likeliest hang in practice
                # -- a model call is ordinary code that can block forever.
                # Recorded as data with whatever it managed to emit, because
                # an unbounded step produces NO record at all, and a run you
                # cannot see is worse than one that failed.
                timed_out = True
                exit_code = None
                out, err = e.stdout or b"", e.stderr or b""
            except OSError as e:
                # could not even start it: data, not a crash
                exit_code, out, err = None, b"", str(e).encode()
            with open(os.path.join(adir, "stdout.bin"), "wb") as fh:
                fh.write(out)
            with open(os.path.join(adir, "stderr.bin"), "wb") as fh:
                fh.write(err)
            _lifecycle_checkpoint("attempt_artifacts_written", rec.run_dir)
            obs = {"step_id": step.id, "n": n, "exit": exit_code,
                   "started": started,
                   "duration_ms": int((time.monotonic() - t0) * 1000),
                   "stdout_bytes": len(out), "stderr_bytes": len(err)}
            if timed_out:
                obs["timed_out"] = True
            caused_by = rec.frames()
            if caused_by:                 # absent, not empty, with no wraps
                obs["caused_by"] = caused_by
            rec.append_attempt(obs)
            return obs

        chain = disp.wrap_chain("around_step", step, run_once)
        chain()
        if counter["n"] == 0:
            # a wrap feature never ran the step; that is a fact, not a crash
            # STRUCTURALLY marked, not just noted in prose. This line is a
            # real fact -- the step produced nothing because a wrap feature
            # took control and never ran it -- but it is the one attempt with
            # no bytes behind it, and a checker comparing the stream against
            # the store cannot tell a legitimate no-execution from a
            # fabricated line unless the record says which it is.
            rec.append_attempt({"step_id": step.id, "n": 0, "exit": None,
                                "started": _utc(), "duration_ms": 0,
                                "executed": False,
                                "note": "no attempt executed"})
        disp.call("after_step", step, {"attempts": counter["n"]}, step_id=step.id)

    disp.call("after_run", spec)
    return rec.close("completed", loaded)
