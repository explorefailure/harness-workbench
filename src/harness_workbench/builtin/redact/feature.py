"""Scrub declared patterns out of captured step output, before it is kept.

A step's stdout and stderr are written to disk verbatim, and a run store is
exactly the thing that outlives the reason it existed -- an API key echoed
by a careless script is still sitting in `steps/01/attempts/0/stdout.bin`
when the run is shared, replayed or committed. No tool in this class solves
secret capture, so this is a real capability rather than a demonstration.

HOW IT REACHES THE BYTES, and this is the part worth understanding before
trusting it. The runner writes stdout.bin and stderr.bin inside the step
executor (`runner.py:304`), which is the call this wrap is wrapped around.
So by the time `run_step()` returns, the secret has ALREADY been written.
This does not prevent capture; it rewrites what was captured. The window is
small and real, and anything reading the run directory during a step sees
the unredacted bytes.

That is a consequence of where the seam is, not of how this is written. A
feature that could prevent the write would need to sit between the process
and the file, and no power in this taxonomy does: `observe` returns are
discarded, `annotate` may only write extras, and `wrap` controls how often
the step runs, not what is recorded of it.

WHAT IT COSTS TO BE HONEST, which is the finding this feature exists to
produce. `confine` states the rule for a wrap plainly -- "no write either. A
wrap's power is over EXECUTION ... it has no declared channel into extras at
all." A wrap therefore has no way to report what it did. Rewriting the bytes
is outside `confine`'s record-channel relation; saying so through `extras` is
a recorded breach. Close-time byte counts and digests still describe the
final redacted artifacts, but attributing the filesystem write requires a
separate effects measurement. Set `report` and watch which record-channel
verdict you get.

CONFIG
    patterns     list of regexes, matched against the captured BYTES
    replacement  what each match becomes (default "[REDACTED]")
    report       write a summary to extras -- deliberately a breach (default
                 false)
"""
import glob
import os
import re

DEFAULT_REPLACEMENT = "[REDACTED]"


def _compiled(ctx):
    pats = ctx.config.get("patterns") or []
    if isinstance(pats, str):                     # one pattern, unwrapped
        pats = [pats]
    return [(p, re.compile(p.encode("utf-8"))) for p in pats]


def _scrub(path, compiled, replacement):
    """Rewrite one captured file in place. Returns hits per pattern.

    Read-modify-write rather than an edit in place: these files are small
    (one step's output), and a partial write here would corrupt the only
    copy of the evidence.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    hits = {}
    for source, rx in compiled:
        data, n = rx.subn(replacement, data)
        if n:
            hits[source] = hits.get(source, 0) + n
    if hits:
        with open(path, "wb") as fh:
            fh.write(data)
    return hits


def around_step(step, run_step, ctx):
    compiled = _compiled(ctx)
    replacement = ctx.config.get("replacement", DEFAULT_REPLACEMENT)
    if isinstance(replacement, str):
        replacement = replacement.encode("utf-8")

    obs = run_step()

    # Every attempt of THIS step, whatever ran beneath -- a nested `sample`
    # or `retry` may have produced several, and a secret in the second draw
    # is no less a secret. Globbed rather than counted, so this does not
    # depend on knowing what was nested inside it.
    found = {}
    files = 0
    if compiled:
        pattern = os.path.join(ctx["run_dir"], "steps", str(step.id),
                               "attempts", "*", "std*.bin")
        for path in sorted(glob.glob(pattern)):
            hits = _scrub(path, compiled, replacement)
            if hits:
                files += 1
            for source, n in hits.items():
                found[source] = found.get(source, 0) + n

    if ctx.config.get("report"):
        # KNOWINGLY OUTSIDE THE DECLARED CHANNEL. A wrap has no return path
        # into the record, so the only way to say what happened is to reach
        # through ctx -- which the dispatcher records as a breach, correctly.
        # Left switchable rather than removed, because "the honest version is
        # the one that fails the check" is the measurement, not a bug to hide.
        ctx["extras"].setdefault(ctx["feature"], {}).update({
            "patterns": [p for p, _ in compiled],
            "matches": found,
            "files_rewritten": files,
        })

    # The inner observation propagates, so an outer wrap can still see what
    # happened beneath it -- the defect `retry` exposed when returns were
    # being discarded.
    return obs
