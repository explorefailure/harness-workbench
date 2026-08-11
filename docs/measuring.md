# Measuring

`hwb run` records what happened. The rest of the commands ask questions about
those records. This is what each one asks, and what its answer means.

**There is no scorer anywhere in here, and that is deliberate.** Judging
whether your workload produced a *good* answer needs an oracle this project
does not have. What is checkable without one is a **relation** — between two
runs, or between a run and a deliberate violation of it. Every verdict below
is a relation, which is why none of them needs labels.

Reference for what these read and write:
[`the-spec.md`](the-spec.md) · [`the-record.md`](the-record.md) ·
[`writing-a-feature.md`](writing-a-feature.md).

## The two kinds of command

| takes a spec | takes an id |
|---|---|
| `run` `sweep` `blast` `catch` `steady` `effects` `efficacy` | `show` `verify` `diff` `fidelity` `sensitivity` `confine` `replay` `interfere` `order` |

The first group produces runs; the second interrogates them.

---

## Does the record hold together?

### `hwb verify <run id>`

Two questions that are easy to conflate: has the record been **edited** since
it was written, and does what was written **satisfy the invariants** at all.
A record can be untampered and still malformed.

```console
$ hwb verify 20260807T174828Z-325fab-ece2
20260807T174828Z-325fab-ece2  clean
  conforms: yes
```

Not a schema validator — unknown keys are always ignored, because the record
contract is additive and a strict schema would break on the next new field.

### `hwb fidelity <run id>`

Can a question be answered **from the run directory alone**, without the tool
that produced it? A fixed question set, each with a resolver that never
touches the harness.

```console
$ hwb fidelity <run id>
yes  What commands ran, in what order?      1 step(s): check ./stable.sh config.txt
yes  Why did this attempt happen?           no wrap feature attached; each step ran once
yes  Which feature code actually executed?  every feature's source is preserved beside the record
part What environment did it run in?        nothing declared, so 49 variable name(s) were recorded without values

10 answered, 1 partial, 0 unanswered (of 11)
answerability only -- whether an answer is USEFUL is the human half of this check
```

Two things it will not do. It does not judge whether an answer is *useful*.
And a missing field is not a failure of the run — records written before a
field existed answer fewer questions, which is a fact about the record's age.

The value is that fidelity otherwise degrades **in silence**: nothing fails
when a record stops being sufficient, and you find out at the moment you need
the answer and it is not there.

---

## Did two runs come out the same?

### `hwb diff <run a> <run b>`

Each record is projected down to what is meant to be stable, and the
projections are compared. Two axes, never collapsed: whether the **harness**
behaved the same, and whether the **work** came out the same.

```console
harness: 5 difference(s)
  features: only in B -- retry
  step check: 1 attempt(s) vs 3
  step check: exits [1] vs [1, 1, 0]
output:  4 step output(s) DIFFER

NOT equivalent

masked (the noise floor of this comparison)
  run_id
  started_at
  ...
```

**What it dropped is reported alongside what it found.** That mask is the
noise floor of every comparison built on this, and a comparison that does not
say what it ignored cannot be audited.

`diff` can also **refuse** — exit code 3, distinct from "they differ" — when
the runs must not be compared at all, such as when one reported input drift.
A script must never be able to read a refusal as a difference.

---

## Do features interfere with each other?

### `hwb sweep <spec>` then `hwb interfere <sweep id>`

`sweep` runs one spec under many feature configurations. `interfere` asserts
a metamorphic relation across the resulting records:

> **extras[A] is invariant under attaching any B that A does not require.**

Features write only into their own namespace and talk solely through the
record, so attaching an unrelated feature must not move A's data. That is the
architecture's central claim, and import analysis cannot check it — coupling
that travels through the record is invisible to a static tool.

```console
$ hwb sweep stable.json
20260807T174859Z-654ac0  mode=pairs  3 ran, 1 skipped
  (none)              20260807T174859Z-cfec35-f198
  freeze              20260807T174859Z-786889-60ce
  receipt             skipped: receipt requires capability 'content-digest', which nothing in this spec provides
  freeze,receipt      20260807T174859Z-a1ee74-5173

$ hwb interfere 20260807T174859Z-654ac0
no interference: every feature's namespace was invariant under attaching another
```

### `hwb order <sweep id>`

With the feature **set** held constant, does permuting the declared **order**
change the run? Order is load-bearing for wraps — `[sample, retry]` composes
as `retry(sample(step))` — so it is a distinct experimental variable, not
noise. Needs a sweep run in permutations mode.

---

## Does a feature misbehave?

### `hwb blast <spec>`

The steady-state hypothesis from chaos engineering: run the spec clean, then
again with exactly one feature **broken**, and look for a difference. Four
survival bits per injection:

| bit | meaning |
|---|---|
| `run` | the run finished rather than dying |
| `record` | the record still satisfies the invariants |
| `others` | no other feature's namespace moved |
| `steps` | the step results survived |

```console
FEATURE   SEAM            FAULT       POWER    SURVIVED
freeze    on_spec_loaded  silent      annotate run record others steps   [ok]
receipt   after_run       meddle      annotate run !record !others steps [ok]

2 violation(s) of the powers taxonomy
  receipt/after_run/meddle broke: conforms, others_intact
    disturbed: freeze
```

`[ok]`/`[failed]` is whether the *injected feature* survived; the four bits
are whether the *harness* contained it. A `!` marks a bit that broke.

**The fault library goes beyond `raise` on purpose.** A hook that returns
`None` never raises, so there is nothing to catch — a library that only
raises reports excellent containment and is wrong. Faults are shaped like
plausible bugs rather than like sabotage, because injected faults are known
to be unrepresentative of real residual defects.

Blast-radius *minimisation* from that literature deliberately does not
transfer: this has no production and no users, so the goal is maximum
exploration.

### `hwb confine <run id>`

Did each feature stay inside the **record channel** its power declared? The
relation is deliberately narrower than filesystem or process confinement.
The manifest is what
every other family trusts — blast picks its fault library from `power`,
interference excuses a consumer because of `requires` — and until this
existed, nothing checked it.

```console
FEATURE      POWER      VERDICT      DETAIL
peeker       observe    BREACHED     wrote its own namespace by hand instead of returning

0 clean, 1 breached, 0 unmeasured

  BREACH: peeker declares 'observe' and wrote its own namespace by hand
          at after_step step 01 -> extras[peeker]
```

`unmeasured` is **not** clean, and is reported separately so it cannot be
read as a pass.

### `hwb effects <spec> --watch <subdir> [--allow <path>]`

Did filesystem changes visible at the two endpoints stay inside a declared
effect envelope? `--watch` is required and repeatable. Each watched root must
be an existing strict descendant of the spec directory; there is no implicit
project, home, or filesystem root. `--allow` is also repeatable and names an
exact path or path prefix inside one watched root. Paths are resolved against
the spec directory, just like step inputs.

The campaign snapshots before feature resolution and after the run closes, so
import-time feature effects and seam-time effects are both inside the measured
interval. It preserves the ordinary run and writes a campaign manifest with
every changed path, change kind, before/after path type, content digest and
mode. Allowed changes remain in the evidence. An unallowed endpoint change is
`BREACH`; invalid setup, sensor failure, and a special filesystem node whose
content cannot be observed are separate non-passing states.

```sh
hwb effects clean.json --watch state --allow state/allowed.txt
hwb effects breach.json --watch state --allow state/allowed.txt
```

The complete known-red/control pair is in
[`../examples/effect-boundary/`](../examples/effect-boundary/). Its `spill`
feature returns a legal annotation while also opening `state/spill.txt` behind
the record channel. `confine` sees only the legal annotation; `effects` names
the file as a breach.

**`within_envelope` does not mean clean.** The zero-dependency sensor is two
portable endpoint snapshots, not a syscall tracer. It cannot see paths outside
the explicit roots, a file created and removed between snapshots, reads or
failed writes, timestamps/ownership/xattrs/ACLs/locks, processes, IPC, network,
DNS, sockets, or remote effects. Those classes are written into every manifest
and printed even on the passing path. A missing tracer therefore never becomes
a global `clean` verdict.

### `hwb catch <spec>`

Mutation testing pointed at the **workload** rather than the code: perturb a
declared input, run, and see whether any feature reports drift.

**A catch rate is meaningless without a stated fault model**, so the model is
declared in the output rather than implied:

```console
$ hwb catch stable.json
20260807T202556Z-80a43f

fault model (declared, because a catch rate without one is meaningless)
  append_byte        expect caught   content changed -- the fault freeze was designed for
  ...
  trailing_newline   expect caught   bytes changed, meaning did not -- the equivalent-mutant case

MUTATION           INPUT                    EXPECT    DETECTED BY
(none)             -                        ignored   -
append_byte        config.txt               caught    freeze
delete             config.txt               caught    freeze
touch_only         config.txt               ignored   -
trailing_newline   config.txt               caught    freeze
...
undeclared_file    (not in steps[].inputs)  ignored   -

caught 6/6   false alarms 0   correctly ignored 3
```

A `...` marks output elided for print; everything else is what the tool
prints, and a test re-runs the command to assert it — see
[below](#are-the-transcripts-in-these-docs-real).

Three inclusions each answer a pitfall the mutation-testing literature names:

- **Equivalent mutants.** `trailing_newline` changes the bytes and not the
  meaning. `freeze` catches it, correctly by its own definition — and at the
  level you care about it is a false alarm. That gap is the interesting
  measurement, not the catch rate.
- **Circularity.** `append_byte` is the fault `freeze` was designed for.
  Catching it proves the implementation runs, not that the design earns
  itself. Marked so it cannot be read as a result.
- **Blind spots**, stated in advance. `freeze` digests only declared inputs,
  so an undeclared file is invisible to it — and so are the model weights,
  the interpreter, the environment and the clock. A run can be entirely
  incomparable to its baseline with `drifted: false`.

Run `catch` against a spec with no detector attached and it will honestly
report `caught 0/3`. That is not a defect; it is what "no fault was ever
injected at anything watching" looks like.

---

## Is the baseline stable enough to compare?

### `hwb steady <spec>`

Every differential attributes a changed run to its manipulation. That claim
is uninterpretable if the unchanged control moves on its own. `steady` runs
the exact same spec and resolved feature tree three times by default, keeps
all three ordinary run directories, and compares the first against every
later run on both axes `diff` exposes: harness structure and stored output.

```console
$ rm -f .flaky-state
$ hwb steady noretry.json
20260811T023500Z-a1b2c3  UNSTABLE
runs: 20260811T023500Z-111111-aaaa, 20260811T023500Z-111111-bbbb, 20260811T023500Z-111111-cccc
allowance: (none)
...
  MOVED   output:steps/check/attempts/0/stdout.bin
...
  MOVED   harness:steps[check].exits
  MOVED   output:steps/check/attempts/0/stderr.bin
  MOVED   output:steps/check/attempts/0/stdout.bin
```

There is no rate and no majority vote. One unallowed moving axis makes the
campaign `UNSTABLE`; a comparison refusal or unavailable output makes it
`uninterpretable`; invalid setup exits separately without a stability
verdict. Run IDs and exact moving axes are recorded in `campaign.json`.
The manifest also pins the starting spec digest and resolved feature root;
each run separately preserves the exact spec and feature source it executed.

The variance allowance is empty by default. `--allow <exact-axis>` is
repeatable and explicit; allowed motion is still recorded, never erased. A
stable verdict with allowances therefore means *stable under those named
allowances*, not deterministic in general.

Unlike efficacy's internal control, standalone `steady` does not hide a
warm-up run. Its first execution is evidence too, so a one-time state
initialisation is reported as motion. Stabilise the workload deliberately if
the downstream differential is meant to start after that transition.

---

## Does a feature do anything at all?

### `hwb efficacy <spec>`

Every family above asks whether a feature **misbehaves**. This one asks
whether it **works** — and it is the only one that can tell a working gate
from a gate wired to permit everything, because those two are identical under
every other check here.

```
blast     injects a FAULT and asserts the run SURVIVED it
efficacy  injects a well-formed OPPOSITE and asserts the run DIFFERED
```

Same mutant generator, opposite assertion. **A mutant that survives is not a
feature that passed; it is a feature nothing downstream consults.**

```console
FEATURE    INTENT      POWER     SEAM             VERDICT    DETAIL
freeze     capability  annotate  on_spec_loaded   killed     comparison refused: B reported input drift
receipt    capability  annotate  after_run        killed     an invariant rejected it: Invariant 1 ...
timing     instrument  observe   -                skipped    instrument -- inertness is the design

killed 2/2 tested
```

The opposite is **declared, never inferred** — see [`inverts`](writing-a-feature.md#inverts).
A feature that declares none is skipped rather than guessed at.

`intent` matters here: an `instrument` feature is often *supposed* to be
inert, and without the declaration "inert as designed" and "inert and nobody
noticed" would be the same row.

**The baseline is warmed and then run twice.** A feature with persistent
state has two code paths — the one that initialises the state and the one
that decides against it — and a fresh scratch spec per run takes the
initialising path every time, so the campaign measures a bootstrap. The two
baseline runs must agree before any mutant is interpreted; when they do not,
the campaign **refuses to report kills at all.** A refusal is worth more than
a table of numbers nobody should act on.

---

## Does the instrument still work?

### `hwb sensitivity <run id>`

The other families measure the system. This one measures **the tools**.

> Without it, "no interference" and "cannot detect interference" are the same
> output.

A checker whose passing verdict is silence — `equivalent`, `clean`,
`conforms: yes` — is indistinguishable from a checker that has stopped
looking. So each probe constructs a violation one checker **must** reject,
and records whether it noticed.

The checker universe is declared separately from the probe list. Adding a
public verdict engine without a probe produces an `UNPROBED` row and a
non-zero exit instead of quietly shrinking the claimed coverage.

```console
PROBE                        CHECKER   VERDICT    DETAIL
confine_record_reach         confine   detected   reached into somebody-else
conform_artifact_mismatch    conform   detected   Invariant 1: stdout_bytes disagrees ...
diff_exit_code *             diff      detected   ...
effects_out_of_envelope      effects   detected   added state/spill.txt
replay_changed_executable    replay    MISSED     reported `matched` after output changed
verify_tamper                verify    detected   drifted (drifted: record.json)

* = positive control

detected 14/15
checker coverage: 13/13
```

Record probes run on copies, verdict reducers receive deliberately red
in-memory manifests, and replay uses a fresh isolated workload — nothing
touches the real store. The positive control is deliberate: if every probe
reports "detected", that is *also* what a broken probe harness reports. If
the control fails, no other row on the table can be believed.

The current known red is replay: its comparison consumes harness-field
differences but drops `diff`'s separate output-difference axis, so changed
replay output can still be labelled `matched`. Sensitivity reports that blind
checker and exits non-zero; it does not turn a missing observation into green.

This is a family rather than a habit because a practice applied from memory
covers the tools you are already thinking about, which are never the ones you
have trusted for months. `diff` was blind to step output for its entire
existence.

### `hwb replay <run id> --in <dir>`

Re-execute a recorded run from its own preserved spec and features.
Answerability is not reproduction: `fidelity` reports reproducibility as
answered because the spec is preserved beside the record, and until something
actually re-executes it, that is a claim with no consumer.

**What this found on its first run:** the record does not name the directory
it executed in. Steps resolve paths against the spec's directory, so a
preserved spec reading `config.txt` cannot be replayed without knowing where
that was rooted. So `--in` must be supplied by a human, and the manifest
records that it was **supplied rather than recovered** — a replay that needed
outside information is not evidence the record is sufficient.

Replays run in a sandbox: declared inputs are copied with their modes, under
the spec's original basename, because a feature with persistent state keys it
to the spec stem.

---

## Are the transcripts in these docs real?

Checked, not promised — which is the same standard every other page here
asks you to hold the tool to.

A ` ```console ` block is an **abridgement of real output**. Registered
blocks are re-run by the test suite, and each line shown must appear in what
the tool prints today, **in order**, with any output dropped between two
shown lines marked `...`. A transcript may start late and stop early; what
it may not do is leave an unmarked hole in the middle.

**The fence carries the claim.** ` ```console ` means *this is output the tool
produced* and invites the check. A command shown with no output is not a
transcript and is fenced ` ```sh ` instead — nothing to verify, so nothing
claimed.

That rule exists because the alternative already happened here. Two `catch`
transcripts showed four rows of a ten-row table above a summary reading
`caught 6/6 … correctly ignored 3` — an abridgement that contradicted
itself on its own face, in the file the README sends every newcomer to
first, and nothing failed.

**What is not covered, stated so the check is not read as broader than it
is.** Only the registered blocks are re-run; the rest are prose until
someone registers them, and the suite holds the unregistered count as a
ceiling so it can only fall. Blocks using `<run id>` placeholders cannot be
run as written. And no test judges the *prose*: it can tell you a table went
stale, never that an explanation stopped being a good one.

---

## What the instrument cannot see

Stated because a measurement whose limits are undocumented gets trusted
further than it holds.

- **`confine` watches record-power channels, not the filesystem.** A feature
  that rewrites captured output on disk is outside that relation, while the
  version that writes `extras` through an undeclared channel is a recorded
  breach. Final byte counts and digests prove the record agrees with stored
  output, but they do not attribute the write. `effects` observes persistent
  endpoint changes only inside roots supplied with `--watch`; it is not a
  tracer and does not assign a write to a particular feature.
- **Seam budgets are advisory at the edges.** Main thread only, cannot
  interrupt a blocking C call, and a hook catching `BaseException` can absorb
  the escalation. Real isolation needs a subprocess.
- **`freeze` and `catch` cover only declared inputs.** Not the interpreter, the
  environment, the clock, or anything you forgot to declare.
- **Injected faults are not real defects.** A large fault-injection study put
  the mismatch as high as 72%.
- **A campaign against a stateful workload measures the state.** Campaigns
  re-run a spec many times; if your step remembers previous runs, every
  comparison is about the leftovers. `examples/flaky/` keeps a stateless
  companion script for exactly this reason.
