"""Pure observe. Proves seam dispatch and nothing else."""
import sys

def after_step(step, obs, ctx):
    sys.stderr.write("[timing] step %s: %d attempt(s)\n" % (step.id, obs["attempts"]))
    return {"ignored": "observe return values are discarded"}
