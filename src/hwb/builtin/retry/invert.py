"""retry, with its decision inverted. Family 7's mutant.

retry's decision is "was the work beneath this attempt good enough to stop?"
The inversion flips that test: it gives up on the first failure and keeps
re-running work that already passed.

WHAT THIS IS EXPECTED TO SHOW, and it is not a bug. On a workload where
nothing fails, retry never re-runs anything and neither does its inversion,
so the two produce identical records and the mutant SURVIVES. That is a true
result reported in the right words: retry is inert *in this configuration*.

It is also the clearest argument for why the family reports inertness as a
property of the configuration rather than of the feature. `retry` is
unstudiable without failures, which is precisely why the probe has a
deterministic failure knob (`HWB_PROBE_FAIL_TIMES`) -- declare it in the
spec's `env` and the same mutant becomes killable. A survival here is an
instruction to change the workload, not a verdict on the feature.
"""


def _passed(obs):
    """Did the work beneath this attempt succeed? (unchanged from the real
    feature -- inverting the OBSERVATION would be a fault, not a decision)"""
    if obs is None:
        return False
    if isinstance(obs, dict):
        return obs.get("exit") == 0
    if isinstance(obs, list):
        return bool(obs) and all(
            (o or {}).get("exit") == 0 for o in obs if isinstance(o, dict))
    return bool(obs)


def around_step(step, run_step, ctx):
    limit = int((ctx.get("config") or {}).get("max", 3))
    seen = []
    for _ in range(max(1, limit)):
        obs = run_step()
        seen.append(obs)
        # THE INVERSION: stop on failure, continue on success.
        if not _passed(obs):
            break
    return seen[-1] if seen else None
