# Writing a feature

A feature is a directory. The workbench validates the whole set from
manifests **before importing any of it**, so a feature declares what it
intends to do in JSON and then does it in Python.

```
myfeature/
  FEATURE.json     the manifest — what this claims
  feature.py       the hooks — what it does
  invert.py        optional; the same feature with its decision reversed
```

Point a spec at the directory containing it:

```json
{
  "schema": "hwbspec/v0.1",
  "features_root": "./features",
  "features": [{"name": "myfeature", "config": {"max": 3}}],
  "steps": [{"id": "01", "argv": ["./check.sh"], "inputs": ["check.sh"]}]
}
```

`features_root` resolves **relative to the spec**, like `steps[].inputs`, so
it travels with the file rather than with whoever invoked it. It is digested
into the spec's identity, because which code runs is the most
experiment-changing thing in the file. `"hwb:builtin"` selects the features
shipped inside the installed package; absent, the default is
`<spec dir>/features`. Full spec reference: [`the-spec.md`](the-spec.md).

What your feature writes ends up in the run's `extras[<record_key>]` — see
[`the-record.md`](the-record.md) for the shape of everything it lands in.

## The manifest

```json
{"name":"myfeature","version":"0.1.0","power":"annotate",
 "seams":["after_step"],"provides":[],"requires":[],
 "seam_contract":">=0.2.0,<0.3.0","record_key":"myfeature",
 "intent":"capability",
 "inverts":{"seam":"after_step","source":"invert.py",
            "decision":"whether the step's output was acceptable"}}
```

| field | required | meaning |
|---|---|---|
| `name` | yes | must match the directory name |
| `power` | yes | `observe`, `annotate`, or `wrap` — see below |
| `seams` | yes | when the hooks fire |
| `version` | no | defaults `0.0.0`; recorded per run |
| `provides` | no | capability names this offers to other features |
| `requires` | no | capability names this needs; unmet fails at load |
| `record_key` | no | extras namespace; defaults to `name` |
| `seam_contract` | no | version range of the seam API, defaults `>=0.2.0,<0.3.0` |
| `intent` | no | `capability` or `instrument` — see [intent](#intent) |
| `inverts` | no | how to build the mutant of this feature — see [inverts](#inverts) |
| `self_attests` | no | `{"payload": "<key>", "digest": "<key>"}`, lets the core check your digest arithmetic |

Validation is inert: manifests are read and cross-checked without importing
anything, so **a spec you do not trust can still be inspected.** Importing is
arbitrary code execution; keeping resolution inert is what makes that safe.

## Powers

A power is a claim about what a feature is allowed to do through the
dispatcher. Its return channel is enforced at dispatch and record-channel
reach-through is audited afterwards by `hwb confine`. Filesystem, process,
and network effects are outside that check. `hwb effects` can separately
compare persistent endpoint changes under explicitly watched subdirectories
with an allowed path envelope; it is not a syscall tracer and cannot attribute
a change to one feature.

| power | may return | may write to extras | failure semantics |
|---|---|---|---|
| `observe` | nothing (return is discarded) | **never** | feature disabled, run continues |
| `annotate` | a `dict`, merged into its own namespace | its own namespace, **only by returning** | feature disabled, run continues |
| `wrap` | its inner result, propagated outward | **never** | the **step** fails, run continues |

`grant` exists in the taxonomy and is dormant — no feature declares it, and
declaring it is rejected with an explicit message.

**Three rules that are easy to get wrong:**

- An `observe` hook's return value is discarded *by contract*. It is handed
  the live extras dict through `ctx`, so it *can* write — and that write is
  recorded as a breach. If you want to record something, declare `annotate`.
- An `annotate` hook must write **by returning a dict**, not by reaching
  through `ctx["extras"]`. A direct write to your own namespace bypasses the
  declared channel; a write to someone else's is coupling that nothing
  declares, which is exactly what `provides`/`requires` exist to make visible.
- A `wrap` has power over **execution** — how many times the step runs — and
  **no declared channel into the record at all**. Writing extras from a wrap
  is a breach even into your own namespace.

Breaches are **recorded, not blocked**. Disabling a feature mid-run for
reaching through would change what every earlier campaign measured, so the
breach becomes a fact in the record and `hwb confine` reads it. This follows
`freeze`: annotate rather than block, and let the reader decide.

## Seams

Which powers each seam accepts, and what your hook is called with:

| seam | accepts | signature |
|---|---|---|
| `on_spec_loaded` | observe, annotate | `on_spec_loaded(spec, ctx)` |
| `before_run` | observe, annotate | `before_run(spec, ctx)` |
| `before_step` | observe, annotate | `before_step(step, ctx)` |
| `around_step` | **wrap only** | `around_step(step, run_step, ctx)` |
| `after_step` | observe, annotate | `after_step(step, obs, ctx)` — `obs` is `{"attempts": n}` |
| `after_run` | observe, annotate | `after_run(spec, ctx)` |

Declare a seam and define a function of that name in `feature.py`. A declared
seam with no matching function is skipped, not an error.

**`step` is an object, not a dict.** It carries `.id`, `.argv` and `.inputs`.
`obs` and `ctx` *are* dicts, so the access is mixed — `step["id"]` raises,
which disables your feature and lets the run finish without it.

### `ctx`

A plain dict, so features stay dependency-free:

| key | value |
|---|---|
| `config` | this feature's `config` from the spec (also `ctx.config`) |
| `run_id` | the current run id |
| `step` | current step id, or `None` outside a step |
| `feature` | your own name |
| `spec` | the loaded spec |
| `run_dir` | this run's directory in the store |
| `extras` | the live extras dict — **read it, do not write it** |
| `providers` | capability name → the feature providing it |

### `around_step` in detail

`run_step` executes the step and appends an attempt. Call it zero or more
times. Whatever you return propagates outward, which is what lets wraps
compose — a wrap nested inside another wrap must be able to see whether the
work beneath it passed:

```python
def around_step(step, run_step, ctx):
    for _ in range(max(1, int(ctx.config.get("max", 3)))):
        obs = run_step()
        if isinstance(obs, dict) and obs.get("exit") == 0:
            return obs
    return obs
```

**The last-declared wrap ends up outermost.** With two wraps this is
load-bearing and changes the experiment:

```
[sample, retry]  ->  retry(sample(step))   retries a whole draw set
[retry, sample]  ->  sample(retry(step))   retries within each draw
```

Because an inner wrap may return a *list* of observations rather than one
dict, a wrap that inspects its inner result needs a predicate over both
shapes. `builtin/retry/feature.py` shows the pattern.

## Capabilities

`provides` and `requires` are names, resolved through the record rather than
by import. Resolve a capability through `ctx["providers"]`:

```python
provider = (ctx.get("providers") or {}).get("content-digest")
blob = (ctx.get("extras") or {}).get(provider) or {}
```

Sniffing extras for a key shape instead couples you to a name you never
declared, and which provider wins depends on dict order. Two edges must not
be able to disagree.

Unmet requirements fail at **load** time. So does a provider whose earliest
seam is not before its consumer's — a capability edge that points backwards
is refused rather than discovered at runtime.

## Bounds and failure

`observe` and `annotate` hooks are bounded by the spec's `seam_timeout_ms`
(absent means unbounded). A hook that overruns raises `SeamTimeout`; a hook
that *swallows* `SeamTimeout` and keeps running is escalated with `SeamAbort`,
which is not an `Exception` and so survives a careless `except Exception:
pass`. Elapsed time is **also** checked on return, which no handler can fake.

`wrap` hooks are deliberately not bounded this way — a wrap's elapsed time is
mostly the step's time, so a seam budget there would fire on a slow workload
rather than a slow feature. Bound the workload with `step_timeout_ms` instead.

The bound is honest about its limits: main thread only, cannot interrupt a
blocking C call, and a hook catching `BaseException` can still absorb the
escalation. Full isolation needs a subprocess.

A crashing `observe`/`annotate` hook is recorded, the feature is disabled, and
the run continues — an annotation defect cannot admit anything, so it must not
be fatal. A crashing `wrap` fails the step, not the run.

### Attempt artifacts are sealed after hooks

`stdout.bin` and `stderr.bin` remain feature-writable through `after_step` and
`after_run`. Only after every hook finishes does the recorder recompute each
attempt's final byte counts and sha256 digests, replace the finalised attempt
stream, and close the run. When `record.attempt_artifact_contract` is
`attempt-artifacts/0.1`, conformance agreement proves the descriptors match the
bytes finally stored. It does **not** identify which feature rewrote those
bytes or prove that the write respected a declared power; use the separate
confinement/effects evidence for those questions.

Older records may omit `attempt_artifact_contract`. That absence is a legacy
rule, not automatic corruption: their counts may describe capture-time bytes
and their digests may be absent. The exact attempt fields and close sequence
are in [`the-record.md`](the-record.md#attemptsjsonl).

## `intent`

Say why the feature exists.

- `capability` — it does work the run needs.
- `instrument` — it exists to exercise the harness. `timing` is one: it
  proves seam dispatch and nothing else.

This matters to the efficacy family specifically. An instrument feature is
often *supposed* to be inert, and inertness is what that family reports.
Without `intent`, "inert as designed" and "inert and nobody noticed" are the
same row.

## `inverts`

Declare the decision your feature makes, plus a well-formed opposite of it.
`hwb efficacy` swaps `source` in at `seam` and requires the run to come out
different. A mutant that survives is not a feature that passed — it is a
feature nothing downstream consults.

```json
"inverts": {"seam": "around_step", "source": "invert.py",
            "decision": "whether the work beneath an attempt succeeded"}
```

`invert.py` is a full hook module, same signature. Invert the **decision**,
never the observation — flipping what the feature *sees* is a fault, which is
what `hwb blast` injects; this family injects a well-formed *opposite*.

**Declared, never inferred.** The core cannot know what "the opposite" of an
arbitrary feature means, and guessing would make the family measure the guess.
Omit it and efficacy skips the feature rather than inventing a decision for
it — an untestable feature is honest; a fabricated inversion is not.

Writing your own inversion *is* the discipline: a feature whose author cannot
state its opposite has not decided what it does.

## Checking your work

Attaching a feature and then measuring it is a task rather than a contract, so
it has its own guide: [`measuring-your-own-code.md`](measuring-your-own-code.md).

The short version is that `hwb confine` is the one to run first — it checks
that the manifest describes how the feature used the record channels, and
every other family trusts that declaration. It does not prove the feature had
no filesystem, process, or network effects.
