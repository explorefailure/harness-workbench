"""Seams say *when*. Features declare *what power they claim*.

Powers and their failure semantics are per-power, not global — that is the
whole reason the taxonomy exists.

    observe    read only; return value ignored
    annotate   may return a dict, stored under extras[<feature>]
    wrap       may run the step zero or more times; each run appends an attempt
    grant      DORMANT. Specified in the plan, no feature declares it.

DEVIATION FROM THE PLAN, found by building: the plan specified `wrap` as a
"generator with a single yield". A plain callable receiving `run_step` is
strictly more capable — it gives setup/teardown around the call AND control
over how many times the step runs, which a single-yield generator cannot.
"""
from __future__ import annotations

import copy
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from .canon import canon_bytes

SEAMS = (
    "on_spec_loaded",
    "before_run",
    "before_step",
    "around_step",
    "after_step",
    "after_run",
)

SEAM_ORDER = {name: i for i, name in enumerate(SEAMS)}

POWERS = ("observe", "annotate", "wrap", "grant")

# Which powers a seam will accept. `grant` is listed but dormant: no feature
# declares it, and features.py rejects it with an explicit message.
SEAM_POWERS = {
    "on_spec_loaded": ("observe", "annotate", "grant"),
    "before_run": ("observe", "annotate", "grant"),
    "before_step": ("observe", "annotate", "grant"),
    "around_step": ("wrap",),
    "after_step": ("observe", "annotate"),
    "after_run": ("observe", "annotate"),
}


class PowerMismatch(Exception):
    """A feature did something its declared power does not permit."""


class InvalidAnnotation(Exception):
    """An annotate hook produced data the run record cannot represent."""


class SeamTimeout(Exception):
    """An observe/annotate hook exceeded the spec's seam budget.

    An ordinary Exception on purpose: a hook may legitimately want to catch
    it, unwind, and return something. That courtesy is what SeamAbort exists
    to withdraw when it is abused.
    """


class SeamAbort(BaseException):
    """Escalation after a swallowed timeout.

    Deliberately NOT an Exception. `except Exception: pass` inside a retry
    loop is ordinary careless code, and it swallows SeamTimeout on every
    fire -- measured: a hook doing exactly that ran 120.6 seconds against a
    0.4 second budget, because the wall-clock check on return is never
    reached by a hook that never returns.

    Escalating out of the Exception hierarchy defeats the careless case
    without pretending to defeat a determined one: a hook catching
    BaseException can still absorb this, and only process isolation would
    change that. What this buys is that the bound stops being advisory for
    the failure people actually write.
    """


class _Budget:
    """Bound an observe/annotate hook with SIGALRM.

    NOT applied to `wrap`. A wrap feature's elapsed time is mostly the STEP's
    time -- it exists to run the step -- so a seam budget there would fire on
    a slow workload rather than a slow feature. Steps are bounded separately
    by `step_timeout_ms`, which is the correct place for it.

    Honest limits, because a bound that is trusted further than it holds is
    worse than none:
      * main thread only; off-thread hooks are unbounded,
      * cannot interrupt a blocking C call that never yields,
      * a hook with a bare `except` can swallow the alarm -- so elapsed time
        is ALSO checked on return, which no handler can fake.
    Full isolation needs a subprocess and is a change to how hooks receive
    ctx; this is the cheap 80% and is documented as such.
    """

    def __init__(self, ms):
        self.ms = ms
        self.armed = False
        self.fired = 0

    def __enter__(self):
        self.t0 = time.perf_counter()
        if not self.ms:
            return self
        try:
            import signal
            self._prev = signal.signal(signal.SIGALRM, self._fire)
            signal.setitimer(signal.ITIMER_REAL, self.ms / 1000.0)
            self.armed = True
        except (ValueError, AttributeError, OSError):
            self.armed = False        # not the main thread, or no SIGALRM
        return self

    def _fire(self, signum, frame):
        self.fired += 1
        if self.fired == 1:
            # Re-arm before raising. If the hook swallows this and keeps
            # running, the next fire escalates past `except Exception`.
            # The grace window is short but non-zero so a hook that IS
            # unwinding cleanly gets to finish.
            try:
                import signal
                signal.setitimer(signal.ITIMER_REAL, max(self.ms / 1000.0, 0.05))
            except (ValueError, OSError):
                pass
            raise SeamTimeout("seam budget of %dms exceeded" % self.ms)
        raise SeamAbort(
            "seam budget of %dms exceeded and the timeout was swallowed "
            "(fire %d)" % (self.ms, self.fired))

    def __exit__(self, exc_type, exc, tb):
        if self.armed:
            import signal
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self._prev)
        return False

    def overran(self):
        """Wall clock on return. A swallowed alarm cannot fake elapsed time."""
        if not self.ms:
            return False
        return (time.perf_counter() - self.t0) * 1000.0 > self.ms


class Ctx(dict):
    """What every hook receives. A dict so features stay dependency-free."""

    @property
    def config(self) -> Dict[str, Any]:
        return self.get("config") or {}


class Dispatcher:
    """Calls hooks, enforces declared powers, records failures without
    letting them take down the run."""

    def __init__(self, loaded, recorder):
        self.loaded = loaded          # list of features.Loaded
        self.recorder = recorder      # runner.Recorder
        # capability -> providing feature name. Handed to every hook so a
        # consumer can resolve the capability it DECLARED instead of
        # sniffing the record for a key shape. Without this a feature that
        # duck-types on someone else's field is coupled to a name it never
        # declared, and the manifest edge becomes decorative.
        self.providers = {}
        for f in loaded:
            for cap in f.manifest.provides:
                self.providers.setdefault(cap, f.name)

    def _ctx(self, feat, step_id):
        return Ctx(run_id=self.recorder.run_id, step=step_id,
                   feature=feat.name, config=feat.config,
                   spec=self.recorder.spec, run_dir=self.recorder.run_dir,
                   extras=self.recorder.extras_view(),
                   providers=dict(self.providers))

    def _live(self, seam: str) -> List[Any]:
        return [f for f in self.loaded
                if seam in f.manifest.seams and f.status == "ok"]

    # ---- confinement (Family 8) ----------------------------------------
    #
    # `extras_view()` hands every hook the LIVE extras dict, which is what
    # makes features talk through the record instead of importing each other.
    # It also means the declared power is, on its own, an honour system: an
    # `observe` feature can write, and an `annotate` feature can write into
    # somebody else's namespace, and neither is visible in the result.
    #
    # RECORDED, NOT ENFORCED, and the choice is deliberate. Disabling a
    # feature mid-run for reaching through would change what every earlier
    # campaign measured -- blast's `meddle` fault is exactly this behaviour
    # and is currently, correctly, reported as contained. So the breach
    # becomes a FACT in the record and `hwb confine` reads it, which follows
    # `freeze`: annotate rather than block, and let the reader decide.

    def _snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self.recorder.extras_view())

    def _restore(self, snapshot: Dict[str, Any]) -> None:
        """Roll the live record channel back without replacing its root.

        Hooks receive the root object through ``ctx``. Keeping that identity
        while restoring its contents means subsequent hooks see the rollback
        through the same channel they were originally handed.
        """
        extras = self.recorder.extras_view()
        extras.clear()
        extras.update(snapshot)

    @staticmethod
    def _require_canonical(value: Any, source: str) -> None:
        """Exercise the exact encoder that will seal ``record.json``."""
        try:
            canon_bytes(value)
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise InvalidAnnotation(
                "%s must be canonical JSON (%s: %s)"
                % (source, type(exc).__name__, exc)) from exc

    def _rollback_invalid_direct_annotation(
            self, feat, seam: str, step_id, before: Dict[str, Any]) -> None:
        """Contain poison left behind by a hook that also raised."""
        if feat.manifest.power != "annotate":
            return
        try:
            self._require_canonical(
                self.recorder.extras_view(),
                "annotate hook direct record mutation")
        except InvalidAnnotation:
            # The hook's original exception remains the reported failure;
            # this only prevents its partial write from causing a second,
            # fatal failure during close.
            self._note_reach(feat, seam, step_id, before)
            self._restore(before)

    def _note_reach(self, feat, seam: str, step_id, before: Dict[str, Any]) -> None:
        """Namespaces the hook changed by hand, before its return is applied.

        Taken BEFORE the return value is merged, so anything caught here
        definitionally arrived through `ctx` rather than through the declared
        channel. That ordering is the whole check.
        """
        after = self.recorder.extras_view()
        for key in sorted(set(before) | set(after)):
            if before.get(key) == after.get(key):
                continue
            feat.breaches.append({
                "seam": seam, "step": step_id, "namespace": key,
                "kind": "own" if key == feat.name else "foreign",
                "power": feat.manifest.power,
            })

    def _fail(self, feat, seam: str, step_id, exc: BaseException) -> None:
        feat.status = "failed"
        feat.failed_at_step = step_id
        feat.error = "%s: %s" % (type(exc).__name__, exc)
        self.recorder.extras(feat.name)["error"] = {
            "seam": seam,
            "step": step_id,
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc().splitlines()[-6:],
        }

    def call(self, seam: str, *args, step_id=None) -> None:
        """observe / annotate seams. A crash is recorded, the feature is
        disabled, and the run continues — an annotation defect cannot admit
        anything, so it must not be fatal."""
        for feat in self._live(seam):
            ctx = self._ctx(feat, step_id)
            hook = getattr(feat.module, seam, None)
            if hook is None:
                continue
            budget = getattr(self.recorder.spec, "seam_timeout_ms", None)
            before = self._snapshot()
            t0 = time.perf_counter()
            with _Budget(budget) as b:
                try:
                    out = hook(*args, ctx)
                except SeamAbort as exc:
                    # Caught by NAME, never by `except BaseException` -- this
                    # must not become the thing that swallows a real
                    # KeyboardInterrupt on the way past.
                    self.recorder.note_seam(feat.name, seam,
                                            (time.perf_counter() - t0) * 1000)
                    self._rollback_invalid_direct_annotation(
                        feat, seam, step_id, before)
                    self._fail(feat, seam, step_id, exc)
                    continue
                except Exception as exc:                  # noqa: BLE001
                    self.recorder.note_seam(feat.name, seam,
                                            (time.perf_counter() - t0) * 1000)
                    self._rollback_invalid_direct_annotation(
                        feat, seam, step_id, before)
                    self._fail(feat, seam, step_id, exc)
                    continue
            self.recorder.note_seam(feat.name, seam,
                                    (time.perf_counter() - t0) * 1000)
            if feat.manifest.power == "annotate":
                try:
                    # ``ctx['extras']`` is deliberately live so confinement
                    # can observe reach-through. A non-canonical write cannot
                    # remain there, though: it would turn this feature defect
                    # into a raw close-time encoder crash.
                    self._require_canonical(
                        self.recorder.extras_view(),
                        "annotate hook direct record mutation")
                except InvalidAnnotation as exc:
                    self._note_reach(feat, seam, step_id, before)
                    self._restore(before)
                    self._fail(feat, seam, step_id, exc)
                    continue
            self._note_reach(feat, seam, step_id, before)
            if b.overran():
                # Returned, but late. Checked separately from the alarm
                # because a hook that swallows SIGALRM still cannot swallow
                # the clock -- and a late annotation is not trustworthy data.
                self._fail(feat, seam, step_id,
                           SeamTimeout("hook returned after %dms budget" % budget))
                continue
            if feat.manifest.power == "observe":
                continue                                   # return ignored
            if out is None:
                continue
            if not isinstance(out, dict):
                self._fail(feat, seam, step_id, PowerMismatch(
                    "annotate hook must return a dict or None, got %s"
                    % type(out).__name__))
                continue
            try:
                # Validate the prospective merged state, not just ``out``:
                # two separately encodable mappings can still be invalid
                # together (for example, keys that cannot be sorted).
                candidate = self._snapshot()
                candidate.setdefault(feat.name, {}).update(out)
                self._require_canonical(candidate, "annotate hook return")
            except InvalidAnnotation as exc:
                self._fail(feat, seam, step_id, exc)
                continue
            self.recorder.extras(feat.name).update(out)

    def wrap_chain(self, seam: str, step, base: Callable[[], Any]) -> Callable[[], Any]:
        """Compose wrap features around the base executor, in declared order.

        The LAST declared feature ends up outermost, so [sample, retry] means
        retry(sample(step)) — the ordering question that only appears once two
        features share this seam.
        """
        chain = base
        for feat in self._live(seam):
            hook = getattr(feat.module, seam, None)
            if hook is None:
                continue
            chain = self._wrap_one(feat, seam, step, chain)
        return chain

    def _wrap_one(self, feat, seam, step, inner):
        hook = getattr(feat.module, seam)

        def wrapped():
            ctx = self._ctx(feat, step.id)
            # One frame per wrap ACTIVATION, carrying this feature's own call
            # ordinal. `counted` bumps it after each pass so the first inner
            # call reads 0 -- matching OpenTelemetry, where the initial
            # attempt carries no resend count and resends number upward.
            frame = {"feature": feat.name, "i": 0}

            # CONFINEMENT FOR WRAP, made attributable.
            #
            # A wrap hook's execution is a sandwich:
            #
            #     [its own code] counted() [its own code] counted() ... [own]
            #
            # A snapshot around the WHOLE call cannot separate its writes
            # from those of the features nested inside `counted()`, which is
            # why this was left unmeasured. But `counted` is defined right
            # here, so the boundaries are known: everything between the hook
            # starting and `counted()` being entered is unambiguously the
            # wrap's own, and so is everything between one `counted()`
            # returning and the next starting.
            #
            # `mark` holds the snapshot at the start of the current OWN
            # segment, and is None while nested code is running. Nothing
            # observed during a nested window is ever attributed here, so the
            # measurement errs toward silence rather than toward the false
            # positives that made this unmeasurable in the first place.
            mark = {"snap": self._snapshot()}

            def counted():
                if mark["snap"] is not None:
                    self._note_reach(feat, seam, step.id, mark["snap"])
                    mark["snap"] = None            # entering nested territory
                try:
                    return inner()
                finally:
                    frame["i"] += 1
                    mark["snap"] = self._snapshot()   # next own segment opens

            self.recorder.push_frame(frame)
            t0 = time.perf_counter()
            try:
                # The hook's return value PROPAGATES outward. Found by
                # building `retry`: with it discarded, a wrap nested inside
                # another wrap could not see whether the work beneath it
                # passed, so retry(sample(step)) retried a draw set that had
                # already succeeded -- silently degenerating into a slower
                # sample. A composable wrap must be able to observe its inner
                # result, so `around_step` hooks may return one.
                return hook(step, counted, ctx)
            except Exception as exc:                       # noqa: BLE001
                # wrap failure fails the STEP, not the run
                self._fail(feat, seam, step.id, exc)
                self.recorder.note_step_failed(step.id, feat.name)
                return None
            finally:
                self.recorder.note_seam(feat.name, seam,
                                        (time.perf_counter() - t0) * 1000)
                # The final own-segment: from the last `counted()` returning
                # (or from the hook's start, if it never called one) to here.
                if mark["snap"] is not None:
                    self._note_reach(feat, seam, step.id, mark["snap"])
                self.recorder.pop_frame()
        return wrapped
