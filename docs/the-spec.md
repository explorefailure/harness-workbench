# The spec

A spec is a JSON file describing one experiment: what to run, with what
attached, under what bounds.

**The runner never imports Python to read one**, so a spec you do not trust
can still be inspected. Manifests are validated before any feature code is
imported, for the same reason. If you want a matrix, write a generator that
*emits* specs rather than a spec that computes itself.

## Minimum

```json
{
  "schema": "hwbspec/v0.1",
  "steps": [{"id": "01", "argv": ["/bin/echo", "hello"]}]
}
```

`schema` and a non-empty `steps` are the only required fields.

## Everything

```json
{
  "schema": "hwbspec/v0.1",
  "run_class": "discovery",
  "features_root": "hwb:builtin",
  "features": [
    {"name": "retry", "config": {"max": 3}},
    {"name": "timing"}
  ],
  "env": ["OLLAMA_HOST", "FLAKY_FAIL_TIMES"],
  "step_timeout_ms": 30000,
  "seam_timeout_ms": 400,
  "replicates": null,
  "steps": [
    {
      "id": "check",
      "argv": ["./flaky.sh", "api"],
      "inputs": ["flaky.sh", "config.txt"]
    }
  ]
}
```

| field | default | meaning |
|---|---|---|
| `schema` | — | `hwbspec/v0.1`. Required. |
| `steps` | — | non-empty list. Required. |
| `run_class` | `discovery` | `discovery`, `calibration`, or `confirmation` |
| `features` | `[]` | what to attach, in order |
| `features_root` | `<spec dir>/features` | where to find them |
| `env` | `[]` | variables to capture **with values** |
| `step_timeout_ms` | unbounded | kill a step that runs longer |
| `seam_timeout_ms` | unbounded | bound an observe/annotate hook |
| `replicates` | `null` | run id this claims to reproduce |
| `gate_budget_ms` | `null` | dormant; reserved for gates |

## `steps[]`

```json
{"id": "check", "argv": ["./flaky.sh", "api"], "inputs": ["flaky.sh"]}
```

- **`id`** — required, unique within the spec. It is one non-empty directory
  component: Unicode is accepted; `.`, `..`, path separators and NUL are
  rejected so an id cannot escape the run store.
- **`argv`** — required, a non-empty list of strings whose first item is
  non-empty. Executed with `subprocess.run`; **no
  shell.** No pipes, globs, `&&`, or variable expansion — if you want those,
  call `["/bin/sh", "-c", "..."]` explicitly and know that you have.
- **`inputs`** — optional list of path strings, relative to the spec's
  directory, that this step reads.

Steps run **in declared order**, and every step runs — a failing step does
not stop the ones after it. Each runs with its working directory set to the
spec's directory, never the directory you invoked `hwb` from. That is
deliberate: a cwd-relative run means invoking the same spec from a different
folder silently changes the experiment.

`hwb steady` reloads this same spec and resolves the same declared feature
route for every repeat. It adds no hidden spec fields and performs no hidden
warm-up. Each execution preserves its own `spec.json` and feature source; a
spec digest or feature digest that moves is a moving harness axis, not noise.
The optional stability allowance belongs to the CLI/campaign manifest rather
than this spec, so the experiment description is not rewritten to excuse its
own variance.

### `inputs` is a declaration, and things read it

It does not affect execution. It declares what the step depends on, and three
things consume that:

- `freeze` digests exactly these and reports drift against a baseline,
- `hwb catch` perturbs exactly these to see whether a detector fires,
- `hwb replay` copies exactly these — with their modes — into its sandbox.

**Anything you do not declare is outside all three checks.** An undeclared
file can change under you while declared-input drift remains clean, which is
the bounded observation `hwb catch` states out loud rather than leaving you
to overread.

## `features[]`

```json
{"name": "retry", "config": {"max": 3}}
```

`name` must be one non-empty directory component under the resolved
`features_root`, and the manifest inside that directory must repeat the same
name. `config` must be an object; it is handed to that feature's hooks as
`ctx.config` and is otherwise opaque to the core.

**Order is load-bearing.** For wrap features the last declared ends up
outermost, so `[sample, retry]` is `retry(sample(step))` — a different
experiment from `[retry, sample]`. `hwb order` exists to measure whether that
choice matters for a given spec.

Features are resolved and validated as a **set** before anything is imported:
an unmet `requires`, a capability edge pointing backwards, an out-of-range
`seam_contract`, or a power at a seam that does not accept it all fail at
load, naming the culprit.

> A `"action"` key is parsed on each entry and **currently read by nothing.**
> It is carried through sweeps but has no effect. Treat it as reserved.

## `features_root`

| value | resolves to |
|---|---|
| absent | `<spec dir>/features` |
| `"hwb:builtin"` | the six features shipped inside the package |
| any other string | that path, **relative to the spec** |

It resolves relative to the spec, like `inputs`, so it travels with the file
rather than with whoever invoked it — and it is **digested into the spec's
identity**, because which code runs is the most experiment-changing thing in
the file.

`$HWB_FEATURES` overrides all of it. That override exists for the campaigns,
which stage mutant feature trees and point runs at them; it is applied by the
harness and recorded in each campaign manifest. **It is not the way to
configure an ordinary run** — an undeclared variable deciding the feature set
is precisely the failure this design exists to prevent.

Shipped features are **opt-in and never a fallback**. A mistyped root fails
loudly rather than quietly succeeding with code you did not choose.

## `env`

A list of non-empty names. Declared variables are captured **with values**
into the record; everything else present is recorded **by name only**.

Declare what determines the experiment. A variable your workload reads but
you did not declare is not in the record, so a run that behaved differently
because of it looks identical to one that did not — and note the converse:
whatever you *do* declare has its value written to disk, so do not declare
secrets.

Declaring nothing is why `hwb fidelity` reports the environment question as
**partial** rather than answered.

## Bounds

Both default to **unbounded**, and that is deliberate: a workbench that killed
a slow model call by surprise would be worse than one that hangs visibly.

- **`step_timeout_ms`** bounds your workload. On expiry the attempt is
  recorded with `timed_out: true`. Digested into the spec's identity, because
  a bound can kill a step that would otherwise have passed.
- **`seam_timeout_ms`** bounds an `observe`/`annotate` hook. **Not applied to
  `wrap`** — a wrap's elapsed time is mostly the step's time, so a budget
  there would fire on a slow workload rather than a slow feature.

The seam bound is honest about its reach: main thread only, cannot interrupt
a blocking C call, and a hook catching `BaseException` can absorb the
escalation. A hook that swallows the timeout and keeps running is escalated
with `SeamAbort`, which is not an `Exception`, and elapsed time is *also*
checked on return — which no handler can fake. See
[`writing-a-feature.md`](writing-a-feature.md#bounds-and-failure).

## `replicates`

`"replicates": "<run id>"` asserts this run re-executes an existing one.
Its identifier shape is validated while the spec loads; the relationship is
validated during run setup, before a run directory is created. An unchecked
claim reads as provenance and carries none:

- the id must be one opaque, non-empty filesystem component — Unicode is
  valid; absolute paths, `/`, `\\`, `.` and `..` are not,
- the target must exist,
- it must share this spec's digest — otherwise it reproduces something else,
- it must not itself be a replicate. **No chains**: a claim about a claim
  cannot be resolved to an original.

It is the **only** field excluded from the spec digest. The rule is *digest
what determines the work, exclude what only makes a claim about it* — and
including it would be self-defeating, since a replicate must share its
target's digest, which is impossible if making the claim changes the digest.

## `run_class`

A label, not a behaviour: `discovery`, `calibration`, or `confirmation`. It is
recorded, shown by `hwb ls`, and compared by `hwb diff`. It exists so that an
exploratory run and a confirming one are distinguishable months later, when
the difference matters and you no longer remember.

## The digest rule

`spec_digest` is the experiment's identity. **Digest what determines the
work; exclude what only makes a claim about it.**

Everything that can change what happens is inside it — including
forward-looking fields like `gate_budget_ms`, which does nothing while gates
are dormant and is digested anyway. Excluding it now would mean changing
digest semantics later, and that churn is worth avoiding.

Removing an absent key changes nothing, so every digest written before an
exclusion existed is unaffected.

## Compatibility

Specs written as `hbspec/v0.1` still load. The preserved `spec.json` inside
every pre-rename run says so, and refusing it would make those runs
unreproducible for a purely cosmetic reason.

Unknown keys are **always ignored**, at every level. The record contract is
additive; a strict schema would break on the next field added. Known fields
still fail closed when their type is malformed, and Python's non-standard
`NaN`/`Infinity` JSON extensions are rejected at the file boundary.
