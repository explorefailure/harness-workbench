# The record

A run is readable without `hwb`. `hwb fidelity` checks that property
mechanically; this page describes the same record for a reader using `cat` and
`python3 -m json.tool`.

Everything here is sealed at close and never revised afterwards. Attempt
lines are appended while execution is live, then their artifact descriptors
are finalised once after every feature hook. There is no database and no
index — a run is a directory, and copying the directory copies the run.

An interrupted directory is still evidence, but it is not necessarily a run.
`hwb ls` deliberately exposes directories without `record.json`; `show` and
`verify` classify them and exit non-zero rather than hiding them or repairing
them. See [Lifecycle state](#lifecycle-state).

## Layout

```
runs/20260807T174244Z-947867-1117/
  record.json          what happened, and under what configuration
  attempts.jsonl       one line per attempt, never collapsed; sealed at close
  integrity.json       sha256 per file, written last
  spec.json            the spec that ran, preserved verbatim
  features/            the source of every attached feature
    retry/
      FEATURE.json
      feature.py
      invert.py
  steps/
    check/
      attempts/
        0/stdout.bin   final stored bytes for this attempt
        0/stderr.bin
        1/stdout.bin
        ...
```

The run id is `<UTC timestamp>-<spec digest prefix>-<random>`. The middle
field means two runs of the same spec sort together and are recognisable as
the same experiment at a glance.

**Output is bytes, not text.** No decoding or newline policy is imposed by the
base. A feature may transform captured bytes before close (the shipped
`redact` feature does); the attempt descriptors are computed afterwards and
therefore describe the final stored artifacts, not the earlier subprocess
buffers.

## `record.json`

| key | meaning |
|---|---|
| `schema` | `hwbrun/v0.1` |
| `run_id` | this run's id, matching the directory name |
| `run_class` | `discovery`, `calibration`, or `confirmation` |
| `status` | `completed`, or how it stopped |
| `started_at` / `ended_at` | UTC, millisecond precision |
| `spec_digest` | digest of the spec that ran — the experiment's identity |
| `spec_path` | where the spec was read from |
| `seam_contract` | e.g. `seams/0.2.0`, the hook API this run used |
| `attempt_artifact_contract` | `attempt-artifacts/0.1`; final attempt byte counts and digests are sealed to stored stdout/stderr |
| `steps` | `[{"id", "argv"}]` — what was asked for |
| `features` | one entry per attached feature, see below |
| `features_source` | which route supplied them, see below |
| `extras` | per-feature namespaces — everything features recorded |
| `seam_timings` | dispatch cost per feature per seam |
| `failed_steps` | steps a wrap feature failed |
| `env` | `{"declared": {...}, "undeclared_names": [...]}` |
| `replicates` | run id this claims to reproduce, or `null` |
| `gates` | dormant; always `[]` |

### `features[]`

```json
{"name": "retry", "version": "0.1.0", "power": "wrap",
 "seams": ["around_step"], "provides": [], "requires": [],
 "order": 0, "status": "ok", "failed_at_step": null,
 "digest": "sha256:7fff1571f2a8...", "breaches": [],
 "intent": "capability",
 "inverts": {"seam": "around_step", "source": "invert.py",
             "decision": "whether the work beneath an attempt succeeded"},
 "self_attests": null}
```

`digest` covers that feature's own source tree, so **what actually executed
is identified regardless of where it came from.** `requires` is recorded
alongside `provides` so the dependency graph can be rebuilt from the record
alone — the blast campaign needed it, because breaking a provider
legitimately changes its consumers and without the edge that reads as blast
damage.

`breaches` is confinement evidence, collected during the run. It has to be:
after the fact, a reach-through and a declared write are identical bytes.

### `features_source`

Which of four routes supplied the code — `harness_workbench:builtin`,
`spec:features_root`, `spec-adjacent`, or `env:HWB_FEATURES`. Recorded
because "which code ran" is the most experiment-changing fact about a run,
and the digest tells you *what* it was without telling you *where it came
from*.

### `env`

Declared variables are captured **with values**; everything else is recorded
**by name only**.

```json
"env": {"declared": {"HOME": "/home/you"},
        "undeclared_names": ["PATH", "SHELL", "TERM", ...]}
```

The split exists because iterating `os.environ` alone made "the spec declared
nothing" and "the environment was empty" the same record. Names without
values say *the run's environment is named but not known* — which is why
`hwb fidelity` reports that question as **partial** rather than answered when
a spec declares nothing.

Values are only captured for what you declared, so a spec that declares a
secret puts it in the record. Declare what determines the experiment.

## `attempts.jsonl`

One JSON object per line, in execution order, **never collapsed**:

```json
{"step_id":"check","n":0,"exit":1,"duration_ms":8,"started":"...","stdout_bytes":65,"stdout_digest":"sha256:...","stderr_bytes":25,"stderr_digest":"sha256:...","caused_by":[{"feature":"retry","i":0}]}
{"step_id":"check","n":1,"exit":1,"duration_ms":8,"started":"...","stdout_bytes":65,"stdout_digest":"sha256:...","stderr_bytes":25,"stderr_digest":"sha256:...","caused_by":[{"feature":"retry","i":1}]}
{"step_id":"check","n":2,"exit":0,"duration_ms":7,"started":"...","stdout_bytes":81,"stdout_digest":"sha256:...","stderr_bytes":0,"stderr_digest":"sha256:...","caused_by":[{"feature":"retry","i":2}]}
```

`n` indexes attempts within a step and maps to `steps/<id>/attempts/<n>/`.

`stdout_bytes`, `stderr_bytes`, `stdout_digest`, and `stderr_digest` are
finalised after `after_run`, over the files that are actually sealed into the
run. `hwb verify` covers post-close edits; conformance independently checks
that these descriptors still agree with the files. Agreement makes an output
rewrite visible in the record but does not identify which feature wrote it.

Finalisation writes `attempts.jsonl.finalising`, closes it, then atomically
replaces `attempts.jsonl`. The temporary name can remain after interruption;
it is retained as evidence and the directory is `incomplete`, never silently
cleaned during inspection.

`caused_by` is a **stack**, outermost first, one frame per wrap activation
carrying that feature's own call ordinal. Nested wraps produce multiple
frames, which is how `retry(sample(step))` is distinguishable from
`sample(retry(step))` in the record rather than only in the spec. The first
inner call reads `i: 0`, matching OpenTelemetry, where the initial attempt
carries no resend count.

**A non-zero `exit` is not an error.** It is what your command did. Only
harness failures change `status`.

### The attempt with no bytes behind it

A `wrap` feature may decline to run the step at all. That produces one
attempt line marked structurally:

```json
{"step_id":"check","n":0,"exit":null,"duration_ms":0,
 "executed":false,"note":"no attempt executed"}
```

`executed: false` is the only line in the file with no `steps/<id>/attempts/0/`
directory behind it. It is marked rather than merely noted in prose because a
checker comparing the stream against the store cannot otherwise tell a
legitimate no-execution from a fabricated line — the two look identical.

### Absent fields mean different things — the rule is per field

Missing fields require field-specific interpretation because records can come
from different versions and unknown keys are always ignored:

| field | absent means |
|---|---|
| `caused_by` | **provenance was not recorded** — *not* "no wrap feature ran" |
| `timed_out` | the attempt did not time out (safe) |
| `seam_timings` | no hook was dispatched (safe) |
| `replicates` | this run makes no reproduction claim (safe) |
| `attempt_artifact_contract` | the run predates close-time artifact sealing; byte counts may be capture-time and digests may be absent |

A field recording something **positive and rare** is safe to read as
absent-means-no. A field recording something **structural and usual** is not,
because its absence is indistinguishable from the thing not being tracked
yet. Treating a missing `caused_by` as "unwrapped" makes two incomparable
runs look equal, which corrupts exactly the ordering comparisons it was added
for.

## `integrity.json`

A sha256 per regular stored file in the run, written last:

```json
{"schema": "integrity/v0.1", "written_at": "2026-08-11T12:00:00.000Z",
 "files": {"record.json": "sha256:1447...", "spec.json": "sha256:cfec...",
           "steps/check/attempts/0/stdout.bin": "sha256:898b..."}}
```

`hwb verify` first requires that exact schema, a non-empty string
`written_at`, and an object-valued `files` inventory. It recomputes the
digests and rejects any regular stored file absent from the inventory.
Directories are structure, not digest subjects. Symlinks, FIFOs, sockets,
devices, and other non-regular leaves are not followed or opened: they are
reported as unsupported and prevent a clean result. The writer likewise
refuses to close an integrity baseline while one is present.

Integrity answers *"have the stored bytes changed since close"* — a different
question from *"is what was written valid"*, which is conformance. **A record
can be untampered and still malformed**, so `verify` reports both and neither
implies the other.

This is a tamper-evidence mechanism, not a tamper-proofing one. There is no
key, so anyone who can edit the run can recompute the digests. It catches
accident and drift, which is what it is for.

## Lifecycle state

`record.status: completed` is necessary but not sufficient for a completed
run. The reader-side lifecycle oracle uses four states:

- `absent`: the announced directory does not exist.
- `incomplete`: some evidence exists, but no readable conforming completed
  record exists, or an integrity baseline exists and disagrees with the store.
- `recoverable`: the record and attempt artifacts conform, but
  `integrity.json` is absent. The evidence is readable; execution is not
  resumable and regenerating the baseline would not restore its original
  authority.
- `complete`: the record conforms, its embedded `run_id` matches its directory
  name, and `integrity.json` verifies every regular stored file with no
  missing, edited, untracked, or unsupported subjects.

`hwb ls` lists every directory in the run store, including incomplete ones.
`hwb show` prints retained inventory and exits 1 for incomplete state; it may
display a recoverable record, but still exits 1. `hwb verify` exits 0 only for
`complete`. None of these readers delete, move, repair, or resume evidence.

## `spec.json` and `features/`

The spec is preserved verbatim; every attached feature's source is copied in
whole. Together these are what make `hwb replay` possible at all, and what
`hwb fidelity` reads to answer *"which feature code actually executed?"*

The spec and its original `spec_path` travel with the run. `hwb replay` uses
the recorded path's parent as the workload directory when that directory still
exists. Use `--in` to supply a different root after moving the checkout or the
record. If the recorded parent is unavailable and `--in` is omitted, replay
uses the current working directory.
Before execution, replay checks recorded input digests against the copied
files. Its manifest records whether the workload directory was recovered from
the record or supplied by hand.

Preservation is not containment. Replaying imports the preserved feature
source and executes the preserved command description as trusted code with the
current user's permissions. The copied replay workload keeps routine state
away from the original directory; it does not form a security boundary.

## Reading one without `hwb`

```console
$ python3 -m json.tool runs/<id>/record.json
$ while read l; do echo "$l"; done < runs/<id>/attempts.jsonl
$ cat runs/<id>/steps/check/attempts/2/stdout.bin
$ diff <(cat runs/A/steps/01/attempts/0/stdout.bin) \
       <(cat runs/B/steps/01/attempts/0/stdout.bin)
```

Everything `hwb show`, `hwb diff` and `hwb fidelity` report is derived from
these files and nothing else. **No derived view is ever stored** — the
projection `diff` compares is computed at read time, every time, so there is
no cached summary that can disagree with the evidence underneath it.
