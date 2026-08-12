# Measuring your own code

Use this guide to attach a runnable workload or feature, record a run, and
select the measurement command that answers your question.

It assumes you have read nothing else. It stops where your work begins — the
last section hands you off to the reference pages rather than restating them.

The files shown here are real. They live in
[`../examples/attaching/`](../examples/attaching/), so you can run the whole
guide there first and substitute your own afterwards. Every transcript below
was produced by the commands above it, and the test suite re-runs them.

## Two ways your code attaches

| you have | it becomes | you write |
|---|---|---|
| a **workload** — anything runnable | a **step** | a line of JSON |
| **harness behaviour** — retry, redaction, a checker | a **feature** | a manifest and a Python file |

Only the second involves writing against an interface. If all you want is a
recorded run, you never leave the first row.

Both rows are trusted execution. A workload command and a `feature.py` module
run with your user's permissions; the workbench does not sandbox either one.
Inspecting a spec and its JSON manifests is inert, but executing them is a
deliberate trust-boundary crossing.

---

## 1. Your workload becomes a step

A step is `argv`. The runner executes it and records what came back; it has no
notion of a model, a language, or a build system.

`workload.json`:

```json
{
  "schema": "hwbspec/v0.1",
  "steps": [{"id": "check", "argv": ["./check.sh"], "inputs": ["check.sh"]}]
}
```

Check these three fields before running the spec. Each can produce a valid run
that measures something other than what you intended:

- **There is no shell.** No pipes, globs, `&&`, or `$VAR`. If you want them,
  say so: `["/bin/sh", "-c", "a | b"]`.
- **Paths resolve against the spec's directory**, never the directory you ran
  `hwb` from — so the same spec means the same thing from anywhere.
- **`inputs` is a declaration, not a filter.** It does not affect execution. It
  tells `freeze` what to digest, `hwb catch` what to perturb, and `hwb replay`
  what to copy. Anything you leave out is outside all three checks; `clean`
  means no drift in this declared set, not that every dependency was observed.

If your workload reads environment variables that change its behaviour, declare
them with `env` so their **values** land in the record — and for the same
reason, never declare a secret. Full field list: [`the-spec.md`](the-spec.md).

Run it:

```console
$ hwb run workload.json
20260810T234119Z-1e1419-b5f4  discovery  1 step(s)  completed
```

**`completed` describes the harness, not your command.** A step that exits 1 is
data; the run recorded what happened, which is the run succeeding.

---

## 2. Your harness behaviour becomes a feature

A feature is a directory beside your spec. Put it in `features/` and the runner
finds it with no configuration:

```
mine.json
check.sh
features/
  myfeature/
    FEATURE.json
    feature.py
```

The manifest declares what the code is allowed to do, and is validated **before
any Python is imported**:

```json
{"name": "myfeature", "power": "annotate", "seams": ["after_step"]}
```

The hook is a function named after the seam:

```python
def after_step(step, obs, ctx):
    return {step.id: obs["attempts"]}
```

**`step` is an object with `.id`, `.argv` and `.inputs`. `obs` is a dict.** The
access is mixed. `step["id"]` raises, disables the feature, and lets the run
continue without it.

Choose the power by what the code needs to do, not by what sounds safest:

| you want to | declare | and you may |
|---|---|---|
| look, and record nothing | `observe` | return nothing |
| record something | `annotate` | **return a dict** — never write `ctx["extras"]` yourself |
| control whether the step runs | `wrap` | run the step 0..n times; write nothing |

Ask for it by name:

```json
{"schema": "hwbspec/v0.1",
 "features": [{"name": "myfeature"}],
 "steps": [{"id": "check", "argv": ["./check.sh"], "inputs": ["check.sh"]}]}
```

Run the feature-bearing spec and capture its id for the checks below:

```sh
RUN=$(hwb run mine.json | awk 'NR==1{print $1}')
```

Use `NR==1`. A run that reports a failed feature prints a second line, and a
bare `awk '{print $1}'` will glue a word onto the end of your id.

A mistyped feature name or directory fails during feature loading. Shipped
features are not used as a fallback, so the runner does not substitute code
that the spec did not select.

```console
$ hwb show "$RUN"
run       20260807T205423Z-d72f0e-91e4
...
features  myfeature@0.0.0(ok)

attempts
  step check n=0   exit=0     5ms
...
extras
  myfeature: {"check": 1}
```

If it says `features failed: myfeature`, the traceback is in the record at
`extras.myfeature.error` — an annotation defect disables the feature and lets
the run finish, because it cannot admit anything.

The six shipped features are working examples of each power. `retry`'s hook is
eight lines; the rest of that file explains the decisions around it. Contract
details — seams, capabilities, bounds:
[`writing-a-feature.md`](writing-a-feature.md).

---

## 3. Ask whether it did what it claims

The commands below run the spec *many times* and compare the records. If the
workload retains state between runs — a counter, cache, or appended file — the
comparisons measure that retained state. Point the campaigns at a stateless
version of the workload.

Now the first question, and the one to ask before any other: did the feature
use only the record channel its manifest declared?

```console
$ hwb confine "$RUN"
20260807T205423Z-d72f0e-91e4

did each feature use only its declared record-power channel?
scope: record extras; filesystem/process/network effects unmeasured

FEATURE      POWER      VERDICT      DETAIL
myfeature    annotate   clean        wrote only through its declared channel

1 clean, 0 breached, 0 unmeasured
```

This result is scoped to record channels. `confine` does not observe files,
processes, or network activity performed by feature code.

The other campaigns rely on that declaration, so a breach here invalidates
their results.

If the feature or workload is expected to write files, declare a separate
bounded endpoint envelope. There is no default watch root:

```sh
hwb effects mine.json --watch state --allow state/expected-output.txt
```

`--watch` must name an existing subdirectory of the spec directory. Repeat
`--allow` for every permitted file or subtree. The manifest retains allowed
changes as well as breaches, with before/after types and digests. A passing
result is `within_envelope`, never `clean`: paths outside the watched roots,
ephemeral create/delete pairs, reads, processes, and network activity are not
observed by the portable snapshot sensor. See the known-red/control pair in
[`../examples/effect-boundary/`](../examples/effect-boundary/).

---

## Now ask the others

You now have a recorded run and a feature that stayed within its declared
record channel. Each command below evaluates a different relation; choose the
one that matches the decision you need to make.

**Is the unchanged baseline stable enough for any differential?** Three runs
are preserved and compared on both harness structure and stored output. No
variance is excused unless you name its exact axis.

```console
$ hwb steady mine.json
...
```

Run this before interpreting sweep, blast, catch, or efficacy. `UNSTABLE`
means the control moved; `uninterpretable` means a refusal or missing output
prevented the comparison. Neither is a weak pass.

**Can a killed runner expose premature success?** Run `interrupt` after
changing recorder close logic, feature hooks that rewrite stored attempt
output, or readers that decide whether a run is complete. Point it at a small,
stateless representative spec: it executes that spec nine times, once for each
of eight lifecycle checkpoints and once as an uninterrupted control.

```sh
hwb interrupt mine.json
```

Read the four states literally: `absent` means no run directory exists;
`incomplete` means some evidence exists without a conforming integrity-closed
run; `recoverable` means conforming evidence is readable but not integrity
closed or resumable; and `complete` means a conforming record plus a clean,
exhaustive regular-file integrity inventory. Every created directory stays in
the run store, including interrupted evidence; the campaign does not delete,
repair, quarantine, or resume it.

A pass is bounded evidence, not crash consistency in general. The marker
selects named closed-file boundaries, and the parent terminates only its direct
runner child. It does not cover intervals between checkpoints, power or kernel
failure, storage-cache/`fsync` durability, descendants, or remote and IPC
cleanup. The runnable nine-child walkthrough is in
[`../examples/flaky/`](../examples/flaky/).

**Does anything downstream consult it?** A feature can be perfectly well-behaved
and completely inert.

```console
$ hwb efficacy mine.json
...
FEATURE    INTENT      POWER     SEAM             VERDICT    DETAIL
myfeature  -           annotate  -                skipped    declares no inversion

killed 0/0 tested

  UNMEASURED: myfeature declares no decision and is not an instrument
```

This result is `UNMEASURED`, not a pass. Efficacy requires the feature to
declare `inverts` — a well-formed *opposite* of its decision — and the mutated
run must differ. Without a declared decision and opposite, the campaign has no
valid mutation to apply.

**Is a bug in it contained?** Breaks your feature five ways and checks the
damage stopped there.

```console
$ hwb blast mine.json
...
FEATURE   SEAM            FAULT       POWER    SURVIVED
myfeature after_step      hang        annotate run record others steps   [failed]
...
per-power failure semantics held for every injection
```

**Does your detector fire?** Perturbs your declared inputs and looks for a
feature that notices.

```console
$ hwb catch mine.json
...
caught 0/3   false alarms 0   correctly ignored 2
```

`0/3` is non-passing because `myfeature` is not a detector and no attached
feature observed the mutations. Attach `freeze` to measure whether that
detector notices the same mutations.

**Does it disturb anything else?** Needs a second feature to be meaningful —
with only one attached, `hwb interfere` will tell you there was nothing to
check rather than reporting a clean result.

```sh
SWEEP=$(hwb sweep mine.json | awk 'NR==1{print $1}')
hwb interfere "$SWEEP"
```

**Do the verdict engines still reject a known bad case?** Run `sensitivity`
after adding or changing a verdict engine, and before treating an all-green
measurement suite as evidence. Give it a representative completed run with
stored attempt output; two attached features let its interference and order
probes reach their intended boundaries.

```sh
hwb sensitivity "$RUN"
```

The engine universe comes from the same public command metadata that registers
the CLI. A new verdict engine without a probe therefore produces `UNPROBED`
and a nonzero exit instead of disappearing from the coverage count. Record
probes operate on copies and replay builds a fresh separate fixture. That
copied directory prevents ordinary test-state collisions; it does not isolate
hostile commands or feature code from the operating system.
Campaign-oriented probes may enter at the smallest production observation or
classification boundary named in their `detail`; that proves the boundary
rejects one known-red case, not that the whole acquisition protocol ran.

At present a nonzero exit is expected even with full engine coverage: the
preserved replay-output probe is a known `MISSED` result because replay drops
the separate output-difference axis. Keep that failure visible; do not read
`checker coverage` as all probes passing.

Four more, each covered in [`measuring.md`](measuring.md): `hwb diff` compares
two runs and reports what it masked · `hwb verify` asks whether the record was
edited · `hwb fidelity` asks what can still be answered from the run directory
alone · `hwb replay` re-executes from the preserved spec.

Read [what the instrument cannot see](measuring.md#what-the-instrument-cannot-see)
before interpreting a campaign result.

---

## When you outgrow the local directory

`features_root` is `<spec dir>/features` until you say otherwise. Set it to
`"harness_workbench:builtin"` to use the shipped features, or set another
path. Relative paths resolve against the spec so they travel with the file.
The resolved feature root is digested into the spec identity because changing
which code runs changes the experiment.

## Where to look next

| | |
|---|---|
| [`the-spec.md`](the-spec.md) | every spec field, the bounds, the digest rule |
| [`writing-a-feature.md`](writing-a-feature.md) | manifest contract, seams, powers, capabilities, `inverts` |
| [`the-record.md`](the-record.md) | reading a run with nothing but `cat` |
| [`campaign-manifests.md`](campaign-manifests.md) | exact campaign stores, schemas, fields, verdicts, and limits |
| [`measuring.md`](measuring.md) | every campaign, what its verdict means, and its limits |
| [`../examples/flaky/`](../examples/flaky/) | all of the above, worked, with no model |
