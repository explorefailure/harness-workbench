"""redact, with its decision inverted: every span is judged safe to keep.

redact's decision is "which spans of captured output must not survive to
disk." The inversion keeps the patterns, compiles them, walks the same
files, and then leaves every match exactly where it was.

WHAT MAKES THIS A DECISION AND NOT A FAULT. The mutant still reads its
config, still resolves the attempt paths, and still returns the inner
observation, so a run under it completes normally with a valid record and
every feature reporting `ok`. Nothing is broken; the feature simply judges
every secret worth keeping. That is the CRE-style opposite -- a well-formed
verdict of the other kind -- rather than blast's territory of crashes and
hangs.

WHAT IT SHOULD SHOW. On a workload whose output contains a declared
pattern, the mutant is killed by `diff`: the stored bytes differ from the
baseline's, and `diff` compares step output content. On a workload with no
secret in it, redaction is a no-op either way and the mutant SURVIVES --
correctly reported as inert *in this configuration*, which is a statement
about the workload rather than about the feature. `examples/redact.json`
exists to be the first kind.
"""
import glob
import os
import re


def _compiled(ctx):
    pats = ctx.config.get("patterns") or []
    if isinstance(pats, str):
        pats = [pats]
    return [(p, re.compile(p.encode("utf-8"))) for p in pats]


def around_step(step, run_step, ctx):
    compiled = _compiled(ctx)
    obs = run_step()

    found = {}
    if compiled:
        pattern = os.path.join(ctx["run_dir"], "steps", str(step.id),
                               "attempts", "*", "std*.bin")
        for path in sorted(glob.glob(pattern)):
            with open(path, "rb") as fh:
                data = fh.read()
            for source, rx in compiled:
                n = len(rx.findall(data))
                if n:
                    found[source] = found.get(source, 0) + n
            # THE INVERSION: the matches are located and then kept.

    if ctx.config.get("report"):
        ctx["extras"].setdefault(ctx["feature"], {}).update({
            "patterns": [p for p, _ in compiled],
            "matches": found,
            "files_rewritten": 0,
        })

    return obs
