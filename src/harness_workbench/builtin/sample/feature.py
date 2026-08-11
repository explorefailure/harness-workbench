"""Run each step N times. Local models are nondeterministic; one draw is
not a measurement. Every attempt is retained -- reduction happens later,
never at capture.

Returns the list of observations so an OUTER wrap can see what happened
beneath it. Returning nothing made retry(sample(step)) blind: retry could
not tell a passing draw set from a failing one and retried regardless.
"""

def around_step(step, run_step, ctx):
    n = int(ctx.config.get("n", 3))
    return [run_step() for _ in range(n)]
