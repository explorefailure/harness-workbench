"""Re-run a failing step, up to `max` attempts total.

Stops at the first attempt that exits 0. Every attempt is retained, so the
record shows the whole history rather than only the outcome -- reduction
happens at read time, never at capture.

THE ORDERING COLLISION. This is the second feature to claim `around_step`,
and with `sample` it makes composition order load-bearing:

    [sample, retry]  ->  retry(sample(step))   retries a whole draw set
    [retry, sample]  ->  sample(retry(step))   retries within each draw

Same two features, two different experiments. The last-declared feature ends
up outermost (see Dispatcher.wrap_chain).
"""

def _passed(obs):
    """Did the work beneath this attempt succeed?

    A single attempt is a dict. A nested wrap returns whatever IT returns --
    `sample` returns its list of draws -- so composing them requires a
    predicate over both shapes. For a draw set the default is ALL: retrying
    because one draw of five failed is the honest reading of "retry a sample
    set", and `any` would call a mostly-failing set a success.

    None means the inner wrap reported nothing, so the outcome is UNKNOWN.
    Unknown is treated as not-passed: retrying work that already succeeded
    wastes attempts, but stopping on unknown would silently disable retry,
    and a feature that quietly does nothing is the worse failure.
    """
    if isinstance(obs, dict):
        return obs.get("exit") == 0
    if isinstance(obs, list):
        return bool(obs) and all(_passed(o) for o in obs)
    return False


def around_step(step, run_step, ctx):
    max_attempts = max(1, int(ctx.config.get("max", 3)))
    last = None
    for _ in range(max_attempts):
        last = run_step()
        if _passed(last):
            break
    return last
