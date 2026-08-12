# Measuring

`hwb run` records what happened. The rest of the commands ask questions about
those records. This is what each one asks, and what its answer means.

`hwb` does not score workload quality. That judgment needs an oracle the
project does not have. The measurement commands instead check a **relation**
between two runs, or between a run and a deliberate violation. These verdicts
do not require quality labels.

Reference for what these read and write:
[`the-spec.md`](the-spec.md) · [`the-record.md`](the-record.md) ·
[`campaign-manifests.md`](campaign-manifests.md) ·
[`writing-a-feature.md`](writing-a-feature.md).

## The two kinds of command

| takes a spec | takes an id |
|---|---|
| `run` `sweep` `blast` `catch` `steady` `effects` `interrupt` `efficacy` | `show` `verify` `diff` `fidelity` `sensitivity` `confine` `replay` `interfere` `order` |

The first group produces runs; the second interrogates them.

Commands that write a campaign manifest keep it in a store separate from the
run evidence they execute or inspect. The selected campaign store and run
store must be disjoint real paths: the same path, either path nested beneath
the other, and symlink aliases are rejected before any campaign or run
directory is created. This prevents `ls`, which intentionally exposes
incomplete run directories, from misclassifying a campaign directory as run
evidence. The check applies only when creating a campaign; it does not change
the format or readability of campaigns already on disk. The default stores
are siblings and already satisfy the boundary.

---

## Does the record hold together?

### `hwb verify <run id>`

Two questions that are easy to conflate: has the record been **edited** since
it was written, and does what was written **satisfy the invariants** at all.
A record can be untampered and still malformed.

```console
$ hwb verify 20260807T174828Z-325fab-ece2
20260807T174828Z-325fab-ece2  complete
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

`fidelity` does not judge whether an answer is *useful*. A missing field is
also not a failure of the run: records written before a field existed answer
fewer questions.

Nothing fails when an older or incomplete record cannot answer a question.
`fidelity` makes that missing evidence visible before another analysis depends
on it.

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
change the run? Order changes wrap composition: `[sample, retry]` composes as
`retry(sample(step))`. It is therefore a distinct experimental variable, not
noise. This command needs a sweep run in permutations mode.

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

The fault library includes more than `raise`. A hook that incorrectly returns
`None` does not raise, so a raise-only library would not test that containment
path. The injected faults approximate plausible implementation defects rather
than sabotage; they are not assumed to represent real residual defects.

Blast-radius *minimisation* from that literature deliberately does not
transfer: this has no production and no users, so the goal is maximum
exploration.

### `hwb interrupt <spec>`

Can a terminated runner directory ever look like a clean completed run before
all completion invariants exist? `interrupt` starts one real child per named
lifecycle boundary. The child writes an atomic coordination marker and blocks;
only after the marker exists does the parent terminate that direct child. The
kill point is therefore a named boundary, not a guessed sleep duration.

The bounded sequence is: before and after run-directory creation, after the
spec and feature sources are preserved, after the first attempt artifacts are
closed, after `attempts.jsonl.finalising` is closed but before `os.replace`,
after that replace, after `record.json` closes, and after `integrity.json`
closes. An uninterrupted child is the second positive control.

```sh
hwb interrupt stable.json
```

Every row records the checkpoint, child return code or signal, announced run
path, observed state, retained file inventory, and any violation. Every
interrupted directory remains in the run store. The full campaign executes the
spec nine times: eight checkpoint children and one uninterrupted control. Use
a small representative spec when the real workload is expensive. There is no
automatic delete, repair, quarantine, or resume.

The state oracle is deliberately stricter than the record's own `status`:

| state | meaning | `ls` / `show` / `verify` |
|---|---|---|
| `absent` | the announced run directory was not created | not listed; the campaign still records the announced path |
| `incomplete` | evidence exists without a readable conforming record, or integrity disagrees | listed; `show` displays retained evidence and exits 1; `verify` exits 1 |
| `recoverable` | a conforming completed record is readable but integrity has not closed | listed and readable, but `show`/`verify` exit 1; this does **not** mean resumable |
| `complete` | conforming record whose `run_id` matches its directory, plus a clean exhaustive regular-file integrity inventory | listed; `show`/`verify` exit 0 |

The positive control at `integrity_written` proves a child can be terminated
after all invariants exist and still classify complete. Every earlier row is a
negative control against premature completion. A file added after integrity
close is now `untracked` and prevents a clean result; integrity checks both the
claimed entries and the complete regular stored-file inventory. The baseline
must identify itself as `integrity/v0.1` and carry `written_at`. Non-regular
nodes such as FIFOs and symlinks are never opened or followed; they are
reported as unsupported and prevent `complete` rather than being silently
excluded from an overbroad claim.

The interrupt result covers direct-child process termination at the named
checkpoints. It does not cover power loss, intervals between checkpoints,
kernel or storage-cache failure, `fsync` guarantees, descendant-process
cleanup, network/IPC/lock cleanup, or durable suspend/resume. Those unobserved
classes are carried in every campaign manifest and printed on the passing
path.

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
  semantic level it may be a false alarm. The byte-level catch rate does not
  resolve that difference.
- **Circularity.** `append_byte` is the fault `freeze` was designed for.
  Catching it proves the implementation runs, not that the design earns
  itself. Marked so it cannot be read as a result.
- **Blind spots**, stated in advance. `freeze` digests only declared inputs,
  so an undeclared file is invisible to it — and so are the model weights,
  the interpreter, the environment and the clock. A run can be entirely
  incomparable to its baseline with `drifted: false`.

Run `catch` against a spec with no detector attached and it reports `caught
0/3`. No attached feature observed the injected faults, so the result is
non-passing rather than a low detector score.

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

The feature declares its opposite through
[`inverts`](writing-a-feature.md#inverts). The core does not derive an opposite
from arbitrary feature code; a feature without `inverts` is `skipped`.

`intent` matters here: an `instrument` feature is often *supposed* to be
inert, and without the declaration "inert as designed" and "inert and nobody
noticed" would be the same row.

**The baseline is warmed and then run twice.** A feature with persistent
state has two code paths — the one that initialises the state and the one
that decides against it — and a fresh scratch spec per run takes the
initialising path every time, so the campaign measures a bootstrap. The two
baseline runs must agree before any mutant is interpreted; when they do not,
the campaign **refuses to report kills at all.** Do not interpret mutant
results when the two baseline runs disagree.

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

The checker universe is derived from the same public command/engine metadata
the CLI uses to register commands. Adding a verdict engine there without a
probe produces an `UNPROBED` row and a non-zero exit. There is no separate
hand-maintained checker list that can omit a registered engine.

```console
PROBE                        CHECKER   VERDICT    DETAIL
confine_record_reach         confine   detected   reached into somebody-else
conform_artifact_mismatch    conform   detected   Invariant 1: stdout_bytes disagrees ...
diff_exit_code *             diff      detected   ...
effects_out_of_envelope      effects   detected   added state/spill.txt
interrupt_premature_complete interrupt detected   missing integrity close classified recoverable, not complete
replay_changed_executable    replay    MISSED     reported `matched` after output changed
verify_tamper                verify    detected   drifted (drifted: record.json)

* = positive control

detected 15/16
checker coverage: 14/14
```

Record probes run on copies and replay uses a fresh separate workload —
nothing touches the real store. Most probes call the public checker directly.
Four campaign-oriented probes enter at the smallest production boundary that
decides their result, rather than pretending to exercise an entire campaign:

- `blast` calls `_survival` on copied run evidence, then its reducer;
- `catch` calls `_drift_reported` on a copied record carrying a drift flag,
  then its reducer;
- `efficacy` calls `_wellformed`, `_conforms`, and `_differs` on copied run
  evidence, then derives the mutant verdict consumed by its reducer; and
- `steady` creates genuinely different stored output and calls `compare_pair`
  followed by `classify`.

The probe detail names that boundary. These rows prove those production
observation/classification paths can reject their known-red case; they do not
claim that sensitivity executed the whole campaign's acquisition protocol.
The positive control is deliberate: if every probe reports "detected", that
is *also* what a broken probe harness reports. If the control fails, no other
row on the table can be believed.

The current known red is replay: its comparison consumes harness-field
differences but drops `diff`'s separate output-difference axis, so changed
replay output can still be labelled `matched`. Sensitivity reports that blind
checker and exits non-zero; it does not turn a missing observation into green.

The probe list makes sensitivity coverage explicit rather than dependent on a
remembered checklist. That distinction matters for established tools: `diff`
was blind to step output until a probe exposed the gap.

### `hwb replay <run id> [--in <dir>]`

Re-execute a recorded run from its own preserved spec and features.
Answerability is not reproduction: `fidelity` reports reproducibility as
answered because the spec is preserved beside the record, and until something
re-executes it, that is a claim with no consumer.

Current records include `spec_path`. Replay uses that path's parent as the
workload directory when it still exists; `--in` overrides it for a moved
checkout or record. If the recorded parent is unavailable and `--in` is
omitted, replay uses the current working directory. Before execution, it
checks recorded input digests against the copied files. The manifest records
whether the workload directory was recovered from the record or supplied by
hand.

Replays run in a separate copied workload directory: declared inputs are
copied with their modes, under the spec's original basename, because a feature
with persistent state keys it to the spec stem. This copy protects the
original workload from ordinary replay state changes; it is **not a security
sandbox or OS isolation boundary**. The replayed commands and preserved
feature modules are trusted code and run with the current user's permissions.

---

## Are the transcripts in these docs real?

A ` ```console ` block claims an **abridgement of real output**. Registered
blocks are re-run by the test suite, and each line shown must appear in what
the tool prints today, **in order**, with any output dropped between two
shown lines marked `...`. A transcript may start late and stop early; what
it may not do is leave an unmarked hole in the middle.

The fence type defines the claim. ` ```console ` means the tool produced the
displayed output. A command shown without output is not a transcript and uses
` ```sh ` instead.

That rule exists because the alternative already happened here. Two `catch`
transcripts showed four rows of a ten-row table above a summary reading
`caught 6/6 … correctly ignored 3` — an abridgement that contradicted
itself on its own face, in the file the README sends every newcomer to
first, and nothing failed.

Only registered blocks are re-run. The remaining blocks are unverified prose,
and the suite holds their count to a ceiling so it can only fall. Blocks using
`<run id>` placeholders cannot run as written. No test judges the *prose*: it
can detect a stale table, but not an inaccurate explanation.

---

## What the instrument cannot see

Each limit below narrows the verdicts described above.

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
- **`interrupt` covers named closed-file boundaries and only its direct runner
  child.** It does not test power loss, storage durability, arbitrary
  instruction points, descendants, or remote effects, and `recoverable` means
  readable evidence rather than resumable execution.
- **`freeze` and `catch` cover only declared inputs.** Not the interpreter, the
  environment, the clock, or anything you forgot to declare.
- **Injected faults are not real defects.** A large fault-injection study put
  the mismatch as high as 72%.
- **A campaign against a stateful workload measures the state.** Campaigns
  re-run a spec many times; if your step remembers previous runs, every
  comparison is about the leftovers. `examples/flaky/` keeps a stateless
  companion script for exactly this reason.
