#!/usr/bin/env python3
"""One prompt -> one model, complete output only.

qwen3 is a reasoning model: with a small num_predict the whole budget goes
to `thinking` and `response` comes back EMPTY. Emitting only `response`
produced byte-identical empty files and a meaningless "runs agree" result.
So: disable thinking, and refuse to emit a truncated answer at all.

WORKLOAD KNOBS. The workload is a control variable, not a fixed property.
Which setting is correct depends on what is being measured:

  * Measuring the HARNESS (interference, blast radius, cost) -- pin it.
    Model variance is contamination there; a delta between two feature
    configurations is only attributable if the workload was held constant.
    This is the default.
  * Exercising `sample` -- unpin it. With temperature 0 and a fixed seed,
    `sample` draws N byte-identical outputs and measures nothing.
  * Exercising `retry` -- inject failure. Nothing here fails on its own, so
    a retry feature has no workload to retry.

Set via environment, and DECLARE the ones you set in the spec's `env` list
so the run record captures which workload produced it. An undeclared knob
changes the experiment invisibly, which is the failure this design exists
to prevent.

  HWB_PROBE_TEMP=0.8        sampling temperature (default 0)
  HWB_PROBE_SEED=           empty string = no seed (default 42)
  HWB_PROBE_FAIL_TIMES=2    fail the first N invocations, then succeed
  HWB_PROBE_FAIL_ALWAYS=1   always exit non-zero
  HWB_PROBE_STATE=path      counter file for FAIL_TIMES (default ./.probe_state)

Failure injection is DETERMINISTIC (a counter, not a coin) so a retry
experiment is reproducible. A random failure rate would make every run a
different experiment.
"""
import json, os, sys, urllib.request

model, prompt_file = sys.argv[1], sys.argv[2]


def _fail_if_asked():
    """Deterministic injected failure, before any model call is made."""
    if os.environ.get("HWB_PROBE_FAIL_ALWAYS"):
        sys.stderr.write("injected: HWB_PROBE_FAIL_ALWAYS\n")
        sys.exit(4)
    n_fail = int(os.environ.get("HWB_PROBE_FAIL_TIMES", "0") or 0)
    if n_fail <= 0:
        return
    state = os.environ.get("HWB_PROBE_STATE", ".probe_state")
    try:
        seen = int(open(state).read().strip() or 0)
    except (OSError, ValueError):
        seen = 0
    seen += 1
    try:
        with open(state, "w") as fh:
            fh.write(str(seen))
    except OSError:
        pass
    if seen <= n_fail:
        sys.stderr.write("injected: failure %d of %d\n" % (seen, n_fail))
        sys.exit(4)


_fail_if_asked()

with open(prompt_file, encoding="utf-8") as fh:
    prompt = fh.read()

options = {
    "temperature": float(os.environ.get("HWB_PROBE_TEMP", "0")),
    "num_predict": 256,
}
# An empty HWB_PROBE_SEED means "no seed" -- distinct from unset, which keeps
# the pinned default. Omitting the key entirely is how you let Ollama vary.
seed = os.environ.get("HWB_PROBE_SEED", "42")
if seed != "":
    options["seed"] = int(seed)

body = json.dumps({
    "model": model, "prompt": prompt, "stream": False, "think": False,
    "options": options,
}).encode()
req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                             data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=600) as r:
    d = json.load(r)

if d.get("done_reason") == "length":
    sys.stderr.write("truncated: hit num_predict before finishing\n")
    sys.exit(3)                       # non-zero exit is data, not a crash
sys.stdout.write(d.get("response") or "")
