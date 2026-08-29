# Declarative agent-task implementation plan

Plan date: 2026-08-26 UTC. Last revised after independent contradiction,
termination, and scope audits on 2026-08-28 UTC.

Convergence basis: four independent GPT-5.6 Sol High audits covering Workbench
architecture, safety/process integrity, certification/evidence, and
implementation/testing feasibility, followed by three fresh independent audits
focused on internal consistency, state-machine termination, and finite delivery
scope.

Historical clean source baseline before this plan was written:
`991dffe373ce7922bb57cf859fb23a5f4f82e835` on `main`.

## Outcome

Build an experiment-local path that accepts one immutable coding task and runs
it, without task-specific adapter code, against Claude, Codex, DeepSeek,
Hermes, or Pi.

The first supported envelope is intentionally narrower than “reinterpret any
existing `argv` as a model prompt”:

> Any trusted, single-agent filesystem task with an immutable bounded input
> tree, a prompt, a declared effect policy, and bounded deterministic
> verification can run against any ready subject.

Each subject produces one sealed Workbench run store. Every base step execution
caused by sampling or retry is retained as a distinct attempt inside that store.
Every attempt receives a fresh agent workspace. Adapter validity, safety
eligibility, and task outcome remain separate verdicts.

## Fixed decisions

1. `write`, `repair`, and `guard` remain checked-in certification baselines.
2. `write` and `repair` also run through the declarative path from canonical
   shared facts. `repair` remains the primary fixed live certification.
3. `guard` remains specialized because its provider-specific hooks, paired
   arms, startup receipts, and causal containment oracle are not a normal task.
4. The declarative path receives one checked-in conformance probe rather than a
   fourth branch in the closed `WORKLOADS` registry.
5. `agent-task/v0.1` remains in the materialized subject experiment. No public
   `hwbspec/v0.1` field or core-to-subject dependency is added.
6. The subject is selected outside the task, so all five subjects consume the
   same task bytes.
7. The existing fixed comparator’s behavior is not weakened. Declarative
   evidence uses separately versioned validation and cannot promote
   `adapter_certification.json`.
8. Applying a selected artifact to a real checkout is a later explicit review
   action. Execution never applies an artifact automatically.

## Threat model and claim boundary

Phase 1 is a reproducible measurement system, not an OS sandbox.

- Task documents, verifier apparatus, and checked-in harness integrations are
  trusted inputs.
- Provider output and workspace effects are untrusted evidence.
- Candidate-local files, digests, process registries, and call-control journals
  provide integrity checks and tamper evidence against accidental drift.
- On workflow-controlled exits and tested signals, all registered process
  groups are given bounded cleanup and a terminal receipt. A host crash,
  supervisor `SIGKILL`, power loss, or deliberately hostile same-UID process
  that escapes into another session is outside the proved cleanup and
  containment boundary; the next run can detect only stale registry state.
- The workflow detects source-tree drift before and after each run; it does not
  claim that a hostile same-UID subject is technically prevented from touching
  the checkout.
- Credential proof covers enumerated credential values and configured detector
  matches. Absolute proof for unknown host credentials would require a future
  OS-enforced process/filesystem boundary.

Any documentation, report, or candidate must use “tamper-evident,” “registered
process-group cleanup,” and “detected workspace effects.” It must not claim
hostile-subject containment, complete same-UID immutability, or arbitrary
descendant control.

## Experiment-local schemas and files

- `agent-task/v0.1`: subject-neutral task document.
- `agent-workspace-archive/v0.1`: immutable input-tree archive.
- `agent-effects-archive/v0.1`: authoritative agent effect set.
- `cross-harness-agent-task-run/v0.1`: one attempt envelope.
- `cross-harness-agent-task-comparison/v0.1`: exact-five comparison.
- `cross-harness-agent-task-phase-candidate/v0.1`: one exact-five phase
  candidate.
- `cross-harness-agent-task-campaign/v0.1`: generic campaign manifest binding
  the write-smoke and repair-matrix phase candidates.
- `agent-task-call-control/v0.1`: paid-call permit, state, and journal protocol.
- `agent-task-process-registry/v0.1`: registered phase process groups and
  cleanup receipts.
- `agent-task-phase-checkpoint/v0.1`: an immutable smoke-boundary validation
  over durable journal/registry prefixes and smoke-child cleanup receipts.
- `agent-task-supervisor-stop/v0.1`: a supervisor-owned stop latch and abnormal
  control-plane termination witness.
- `cross-harness-certification-continuity/v0.1`: narrow authorization for a
  fixed-workload recut after expected certification-input drift.
- `cross-harness-candidate-validation/v0.1`: upstream offline certification
  validation that never contains proposed certification or patch bytes.
- `agent_task.py`: plan/build/run entry point.
- `agent_task_schema.py`: schema declarations and pure parsing.
- `agent_task_runtime.py`: producer-side episode lifecycle.
- `agent_task_validate.py`: independent retained-evidence validator.
- `agent_task_compare.py`: exact-five comparator.

New schemas use unambiguous digest names, including:

- `execution_plan_sha256`;
- `comparator_program_sha256`;
- `comparison_report_sha256`;
- `run_store_tree_sha256`;
- `record_json_sha256`;
- `integrity_json_sha256`; and
- `artifact_archive_sha256`.

No schema uses one field name for both program bytes and result bytes.

## Evidence topology

Plan-only execution is read-only and authorizes zero calls. It does not create
the requested live destination.

Live execution requires a destination that does not exist. The supervisor
resolves its parent, rejects symlinks and aliases, creates the destination
atomically with mode `0700`, and creates:

```text
bundle/                  retained pre-run task and experiment inputs
session/                 call-control state, journal, and execution plan
records/write-smoke/     exactly five write-smoke Workbench stores
records/repair-matrix/   exactly five repair-matrix Workbench stores
process/                 registrations, captures, and cleanup receipts
review/write-smoke/      exact-five comparison and phase candidate
review/repair-matrix/    exact-five comparison and phase candidate
review/campaign.json     manifest binding both generic phase candidates
```

`bundle/` contains the task, prompt, workspace archive, verifier apparatus,
candidate-local scripts, generated specs, validator, comparator, schema files,
and a complete machine-checked manifest of experiment-local imports and data.
These files execute from retained copies through absolute paths.

Installed Workbench core modules, the resolved Python interpreter, and provider
executables remain external apparatus. Their resolved paths, versions, and
complete digest maps are bound in `execution-plan.json`. Python uses an
absolute interpreter, isolated import mode, no `PYTHONPATH`, disabled user-site
loading, and a fixed digest-bound bootstrap. The bootstrap validates the
absolute retained bundle root before explicitly loading the candidate-local
entry point; it cannot import the source checkout or an ambient working
directory. The plan does not call this arrangement hermetic.

The supervisor keeps the expected execution-plan digest as the authorization
root, rechecks the complete bundle and external apparatus before each use and
after every run, and fails closed on drift. `freeze` is supporting digest
evidence, not retention or independent authorization.

Plan-only computes and displays a virtual bundle manifest and digest set without
creating the live destination. After live mode atomically creates the
destination, it materializes and revalidates those exact bytes before any paid
authorization or call.

The pre-run `execution-plan.json` does not claim its own digest. Each phase
candidate binds the execution plan, its exact five-store set, artifacts, scans,
usage, call journal, cleanup registry, comparator program, and phase comparison
report. The campaign manifest binds both generic phase candidates. None claims
its own digest.

## Canonical archive contracts and bounds

Both archive schemas use deterministic `ZIP_STORED` bytes with sorted member
names, fixed timestamps and modes, no comments or extra fields, canonical JSON
manifests, and content-addressed `blobs/<sha256>` payload members. Original
workspace paths exist only in the canonical manifest.

Paths are relative POSIX paths normalized to NFC. Absolute paths, empty
components, `.`, `..`, NUL, duplicate names, case-fold collisions, and Unicode
normalization collisions are rejected. Extraction uses descriptor-relative
no-follow traversal and exclusive creation.

`agent-workspace-archive/v0.1` permits only directories and regular files with
normalized `0644` or `0755` modes. It rejects symlinks, hard links, devices,
FIFOs, sockets, sparse files, unsupported xattrs, and unsupported metadata.

`agent-effects-archive/v0.1` contains a canonical ordered operation list:

- `add_file`;
- `modify_file`;
- `delete_file`;
- `mode_change`;
- `mkdir`; and
- `rmdir`.

Each operation binds before/after kind, mode, size, and digest where applicable.
Created and modified regular files reference content-addressed payloads;
deletions retain tombstones. Type changes are recorded in the complete delta
but are unsupported and candidate-invalid in v0.1.

Independent hard v0.1 rejection ceilings are:

- 1,024 total input nodes;
- 512 input regular files;
- 4 MiB per input file;
- 64 MiB aggregate decoded input bytes;
- 72 MiB input archive bytes;
- 256 UTF-8 bytes per path and depth 32;
- 256 effect operations;
- 512 KiB per changed-file payload;
- 1 MiB aggregate changed-file payload bytes;
- 1.5 MiB effects archive bytes;
- 512 KiB aggregate retained raw provider-event bytes;
- 256 MiB aggregate temporary scratch/spool use;
- 4,194,304 bytes for complete serialized attempt stdout; and
- 65,536 bytes for runtime stderr.

Tasks may request lower ceilings, never higher ones. The independent ceilings
are not a promise that all maxima can be combined: the accepted domain is their
intersection with the worst-case envelope equation below. File count, path,
depth, source size, and aggregate size are checked before allocation. Reading,
scanning, spooling, archive construction, and base64 encoding are streaming and
bounded. Exact-limit tests include sparse, incompressible, binary, and
concurrently growing inputs.

The schema defines a worst-case byte-budget equation covering the two complete
manifests, canonical delta, receipts, JSON escaping, base64 expansion, raw
events, normalized captures, and effects archive. Task-declared ceilings are
accepted only when that equation proves the maximum valid attempt fits within
4,194,304 bytes. The complete attempt JSON is also serialized and measured
before emission. A maximum-limit fixture must produce a valid envelope; an
oversized actual result is replaced by a small fixed measurement-invalid
envelope containing only bounded redacted diagnostics, counts, digests, and
overflow flags.

## Session-wide provider-call control

Each paid campaign has one supervisor-owned call-control service shared by its
route canary and every subject store. Fixed recertification and generic
acceptance are separate campaigns with separate authorization and journals.
Provider calls and subject stores are strictly sequential: at most one
provider invocation may be live campaign-wide.

The service holds the authoritative state in memory and writes an exclusive,
append-only, `fsync`ed journal under `session/`. During sealed-spec generation,
the supervisor creates one stable random store nonce for each expected
Workbench store and embeds it in every invocation of that spec. A locked
request carries the campaign nonce, phase kind, subject, and stable store
nonce. In one atomic state transition, call control allocates the next
contiguous base-attempt ordinal for that store, derives a unique base-attempt
token from the campaign/store identity and ordinal, allocates a monotonic call
ID, records and `fsync`s the permit request, and only then replies with the
permit identity. The caller must use the returned ordinal and token; it never
chooses or predicts them. The service enforces the displayed phase and global
maximums, obtains a fresh usage snapshot, and applies the configured usage
gates immediately before the broker releases every provider invocation. Direct
HTTP route-canary calls
must acquire the same permit before sending a request. Unreadable usage or a
reached threshold atomically sets `hard_stop` before release. Every permit-time
usage snapshot is retained and digested.

The request/reply crash boundary is explicit. A request is not allocated until
its allocation row is durably `fsync`ed; after that row exists, loss of the
reply is an uncertain consumed ordinal and permit, never permission to issue a
replacement call. Reconnection may query that exact campaign/store request
identity and receive the existing allocation only while the service can prove
that no release occurred; otherwise it latches `hard_stop`. A reply without a
matching durable allocation is invalid. Offline reconciliation joins stable
store nonce plus allocated ordinal/token to later Workbench attempt order, then
checks the exact `caused_by` frames Workbench attaches after child exit. Missing,
duplicate, skipped, reordered, or differently caused attempts make the phase
ineligible; they are never repaired by rewriting journal identity.

States are:

- `ready`;
- `inflight`;
- `retry_pending`; and
- `hard_stop`.

Transitions are exact:

1. A credential finding, positive redaction count, safety overflow, digest
   drift, malformed state, uncertain state update, or unproved registered-group
   cleanup sets `hard_stop` and is journaled before the attempt returns.
2. A first ordinary operational failure with proved cleanup sets
   `retry_pending` keyed to the campaign nonce, subject, stable store nonce,
   prior call ID, and next atomically allocated base-attempt ordinal/token; it exits nonzero and
   permits exactly that configured retry. The service, not the next caller,
   owns the predecessor identity.
3. A successful retry returns the service to `ready`.
4. A failed retry sets `hard_stop` before returning.
5. A valid measurement, including a valid task failure, returns to `ready`,
   exits zero, and neither retries nor safety-latches.
6. Each permit has a finite lease. A stale `inflight` or `retry_pending` state,
   wrong-owner retry, competing request, exhausted budget, lost service, or
   interrupted transition becomes `hard_stop`.
7. Any later Workbench base execution observing `hard_stop` emits a bounded
   `provider_invoked:false` refusal envelope and exits zero, so wrapper loops
   may finish but no later provider call or paid retry occurs.

The journal retains call ID, campaign nonce, phase kind, subject, stable store
nonce, base-attempt ordinal and token, retry predecessor, draw/retry provenance
when known, permit lease, transition, reason code, usage-snapshot digest,
timestamps, and receipt digest—never secrets.
Because Workbench attaches `caused_by` frames only after the child exits,
pre-call ownership comes from the attempt token and strict sequential
execution. The comparator reconciles the journal with retained Workbench
attempt order and `caused_by` frames afterward.

Required black-box tests use the real `sample(retry(step))` composition:

- credential finding on call 1 produces exactly one provider call;
- two operational failures produce exactly two calls, then latch;
- one operational failure followed by success produces two calls and later
  draws continue;
- three valid task failures produce three calls and no retries;
- corrupt or interrupted state produces zero calls;
- a competing permit request is rejected and cannot steal a retry;
- stale `retry_pending` and wrong-owner retry requests latch without a call;
- a threshold crossed by one call prevents the next call; and
- the global maximum cannot be exceeded under adversarial concurrent requests.

## Cross-layer process and output control

The live supervisor owns a persistent experiment-local spawn broker and
call-control service outside the `hwb` process. Both are registered control-
plane processes with finite startup, request, lease, and shutdown deadlines.
Every precheck, provider, postcheck, and helper process is spawned behind a
start barrier. Before release, the broker records call ID, PID, PGID, supported
platform start identity, executable identity, and phase in the append-only
process registry.

On normal completion the broker applies the existing bounded TERM/grace/KILL
sequence, verifies the registered group is absent, and writes a terminal
cleanup row marked `clean_self_issued`. The broker also watches an authenticated supervisor control
channel; on EOF or liveness loss it latches call control, terminates registered
groups, seals the registry, and exits. On outer timeout, signal, or abnormal
`hwb` exit, the surviving supervisor sets `hard_stop` before terminating `hwb`,
then asks the broker to terminate and verify every uncleared group before
collecting usage-after or final scan evidence.

The supervisor is an independent witness when either control-plane child dies,
hangs, loses its authenticated channel, or cannot issue its own terminal
receipt. It appends and `fsync`s a supervisor-owned abnormal-termination record
containing the child identity, last validated journal/registry prefix digest,
observed wait status or channel failure, and timestamp; separately appends and
`fsync`s a supervisor stop-latch record; and refuses every later phase. Using
only previously recorded PID, PGID, platform start identity, and executable
identity, it performs bounded TERM/grace/KILL cleanup for every registered
uncleared group and records the observation as
`abnormal_supervisor_witnessed`, never as a clean self-issued receipt. Identity
uncertainty prevents signaling an unproved target, latches the stop record, and
is retained as incomplete cleanup. An abnormal control-plane termination can
therefore be audit-complete when all facts and cleanup observations are
durable, but it is always candidate-ineligible.

Registry corruption, missing closure, identity uncertainty, a surviving
registered group, or any `abnormal_supervisor_witnessed` receipt sets the
campaign stop latch and makes the candidate ineligible. Final candidate
eligibility also requires clean self-issued terminal bounded shutdown receipts
for the broker and call-control service. The evidence states what was
cleaned on workflow-controlled exits; supervisor `SIGKILL`, host crash, power
loss, and deliberately detached same-UID sessions remain outside the guarantee
and yield detectable stale state when the host survives.

The Workbench step invokes a small bounded launcher rather than the runtime
directly. The launcher captures runtime stdout and stderr under the v0.1 limits,
prevents inner processes from inheriting those streams, catches exception and
serialization paths, and emits only the fixed bounded envelope or a fixed
bounded diagnostic. The outer supervisor independently bounds complete `hwb`
stdout and stderr at 4 MiB each.

Tests interrupt immediately before and after broker registration, after child
release, during output, while a grandchild holds pipes, during cleanup, and
during simultaneous stdout/stderr saturation. Tests also kill `hwb`, the
supervisor control channel, broker, and call-control service at each defined
window. Acceptance requires a terminal cleanup row for every registered phase,
bounded control-plane shutdown receipts, and no live registered group.

Every outer timeout is derived rather than open-ended: the maximum number of
base executions multiplied by the bounded episode duration, plus bounded
verification, comparison, scan, and finalization headroom. Broker RPCs, permit
leases, and shutdown waits have independent finite deadlines below that outer
bound.

## Independent verification model

Each attempt uses three disposable workspaces:

1. **Precheck workspace:** a fresh input extraction. Run the precheck, capture
   before/after manifests, require the declared return code, and require no
   mutation. Then delete it.
2. **Agent workspace:** an independent fresh input extraction. Capture the
   initial manifest, invoke the provider, complete registered-group cleanup,
   and capture the complete final state and effect evidence. This is the only
   workspace whose effects can enter the agent artifact.
3. **Postcheck workspace:** a fresh reconstruction of the sealed agent
   post-state. Run the postcheck, capture before/after manifests, require the
   declared return code, and require no mutation. Then delete it.

Verifier commands use no shell, an absolute digest-bound executable, a minimal
allow-listed environment, `PYTHONDONTWRITEBYTECODE=1`, a fixed cwd contract,
and a separate bounded scratch directory. Verifier timeout, overflow, apparatus
drift, or workspace mutation is measurement-invalid. Tests include verifiers
that create caches, edit or delete files, and “repair then pass”; none can alter
or validate the authoritative agent effect.

After registered provider cleanup, the final agent manifest, credential scan,
and artifact payload are produced in one descriptor-relative no-follow
traversal. Each regular file is read through one descriptor; `fstat` identity
is checked before and after; digest, scanner, and payload consume those exact
bytes. Accepted files are never reopened by pathname. Identity change,
concurrent mutation, symlink, unsupported node, or directory race is a safety
failure.

Retained attempt evidence includes:

- the complete initial manifest reconstructed from the retained input archive;
- the complete final manifest for every workspace node;
- a complete canonical delta, including unsupported/type-change observations;
- exact effects archive payloads for accepted created/modified files;
- deletion tombstones and directory operations;
- bounded raw provider event bytes;
- producer lifecycle projection;
- precheck, provider, postcheck, scan, and cleanup receipts; and
- call-control state and call ID.

The independent validator may import only the standard library, schema
constants, canonical JSON, and digest primitives. An AST/import-graph test
forbids imports from the runtime, adapters, workloads, oracles, profiles, pins,
or fixed comparator.

The producer alone may reuse existing adapter decoders. The validator
independently decodes retained raw events, reconstructs the initial manifest,
applies effects to reconstruct the post-state, recomputes the complete delta,
projects the vendor-neutral lifecycle, evaluates ordered assertions, checks
artifact membership and bytes, and recomputes task outcome. Validation runs
successfully after producer code, live pins/profiles, task source files, and
provider executables are made unavailable.

## Credential policy

Before the first retained write, the workflow scans the task document, prompt,
raw input archive, every decoded archive entry and filename, verifier apparatus,
and all other untrusted inputs. Only accepted bytes are stream-copied into
`bundle/`. A pre-run finding refuses live execution.

The credential inventory includes snapshotted provider credential values,
credential-shaped environment values, configured credential files, and an
independent configured pattern scanner such as gitleaks. For clean paths,
scanner reports may retain rule IDs and paths but never matched values or
surrounding secret-bearing context. If the filename or path itself matches,
the raw path and scanner context are never retained.

Process captures, sidecars, verifier captures, filenames, and changed-file
bytes are scanned before retention. Any positive redaction count or detector
match sets `hard_stop` and makes the attempt and campaign ineligible.

For a credential-bearing output, retained quarantine evidence contains metadata
only: an ordinal, path digest, node kind, mode, source byte count, source digest,
scanner rule IDs, a fixed `PATH_WITHHELD` marker when the path matches, and a
fixed `PAYLOAD_WITHHELD` marker. No matching raw path, original, redacted,
encoded, or otherwise source-derived payload is retained. An accepted exact
artifact requires zero redactions and zero detector findings.

The final credential proof is scoped honestly: all enumerated values and
configured detector matches are absent from every retained and decoded file.

## Workbench provenance and comparison invariants

Generated smoke and matrix specs are separate ordinary one-step
`hwbspec/v0.1` files. Their feature array is exactly
`[freeze, receipt, retry, sample, timing]`. The retry/sample subcomposition
remains `sample(retry(step))`; only retry and sample contribute `caused_by`
frames. Smoke uses `sample.n=1`; matrix uses `sample.n=3`; retry permits at most
two base executions per draw. No competing `step_timeout_ms` is present.

Every generated freeze lock is created and validated during live bundle
assembly, before the bundle manifest is sealed or calls are authorized. The
lock is itself a retained manifest member. Execution must report
`freeze.baseline == "compared"`, `freeze.drifted == false`, and receipt inputs
identical to freeze inputs; runtime creation or mutation of a freeze lock is
bundle drift and latches the campaign.

The generic validator/comparator requires:

- exactly one expected step and exactly five expected subjects;
- exact feature names, order, configs, code digests, and `ok` statuses;
- empty `failed_steps`, a clean freeze result, and consistent receipt;
- contiguous base-attempt ordinals;
- exact nested `caused_by` frames and draw/retry ordinals;
- one logical draw for smoke or three logical draws for matrix, each with at
  most two base executions;
- journal call IDs and provider-invocation counts consistent with attempts;
- exact task, input archive, execution plan, apparatus, validator corpus,
  comparator program, store, record, integrity, artifact, and report digests;
- exactly five accepted stores and zero unexpected or partial run directories;
  and
- independent adapter-validity, safety-eligibility, and task-outcome verdicts
  for every draw.

Each phase comparator receives only its declared phase root and exactly five
immediate run-store children. The workflow discovers every immediate child of
both declared run roots and runs `hwb verify` on all of them, including
unexpected or partial stores. Any extra, partial, invalid, or unbound immediate
child makes that phase and the campaign ineligible. Offline review independently
recomputes store-tree, `record.json`, and `integrity.json` digests.

The write-smoke boundary uses a phase-validation checkpoint rather than a final
phase candidate. After all smoke children have terminal cleanup receipts, the
supervisor asks both shared services to `fsync` and expose immutable journal and
registry prefix lengths and digests. The independent checkpoint validator binds
those prefixes, all five verified smoke stores, the smoke comparison, usage,
scans, and every completed smoke-child cleanup receipt. It does not require the
shared broker or call-control service to shut down. Only a valid checkpoint plus
a fresh passing usage gate authorizes the repair matrix. Final smoke and repair
phase candidates, and the campaign manifest, are created only after matrix
finalization and clean self-issued broker and call-control shutdown receipts;
they bind the checkpoint and the final journal/registry closures.

For mirrored `repair`, the canonical expected command bytes are exactly
`python3.11 -m unittest -v`. A command operation matches only when the complete
retained structured tool argument equals those bytes. The assertion binds
command digest, tool-call identity, result identity, exit evidence, and order:

```text
exact failing command → permitted edit → exact passing command
```

Substring matches, prefixes, suffixes, wrappers, extra arguments, repeated
occurrences, `&&`, `;`, pipes, substitutions, directory/environment wrappers,
and edits chained into either invocation are nonmatches.

## Finite delivery slices

The work is intentionally split so implementation scope and paid evidence do
not become one indivisible PR:

1. **Contract PR:** this plan, normative schemas, error taxonomy, dependency
   closure, resource equation, phase topology, and finite test-vector manifest.
   It changes no adapter behavior and makes no paid call.
2. **Offline-foundation PR:** archive/effect codecs, independent readers,
   call-control state machine, broker/launcher, fake-provider tests, package
   data, and CI discovery. It may merge independently only when the structural
   certification audit proves all existing fixed input/apparatus maps unchanged
   and doctor remains ready; otherwise it stays stacked in the integration
   branch.
3. **Declarative-runtime draft PR:** three-workspace runtime, routing, mirrors,
   conformance task, comparisons, documentation, and source/wheel/sdist offline
   acceptance. Hosted review and checks occur before live evidence.
4. **One final integration generation:** freeze Eₙ, perform explicitly
   authorized fixed recertification, create promotion-only Pₙ, pass hosted
   checks, then perform separately authorized write-smoke and repair-matrix
   acceptance. Any bound change stops the generation; it never loops
   automatically.
5. **Merge/post-merge:** merge the exact accepted head and run offline-only
   verification. Differing merged tree bytes stop the workflow rather than
   silently causing a paid recut.

## Implementation steps and gates

### Step 0 — Freeze the planning baseline

Deliverables:

- Re-read applicable instructions, source, history, adapter documentation, and
  retained evidence.
- Record `991dffe` as the historical clean source baseline.
- Create a feature branch and commit this reviewed plan and README link as the
  first contract commit.
- Add normative JSON Schemas, transition tables, error taxonomy, dependency-
  closure definition, byte-budget equation, phase evidence topology, and a
  finite versioned test-vector manifest with stable case IDs for canonical
  success, valid task failure, one successful retry, exhausted retry, hard stop,
  stale state, permit collision, usage-gate crossing, archive node kinds, path
  collisions, verifier mutation, output overflow, credential value, credential
  filename, process interruption, and digest/provenance mutation.
- Merge the reviewed plan and normative contract as a standalone contract PR
  before runtime implementation. It contains no adapter behavior change or paid
  call.
- Recompute current certification, comparator-program, and comparison-report
  digests.
- Run and record existing adapter and core suite counts.

Gate:

- Baseline and plan commits are distinct and retained in the PR record.
- The normative schemas and finite test-vector IDs are reviewed and frozen for
  the first implementation generation. Corpus expansion requires an ordinary
  reviewed contract change, not an open-ended acceptance condition.
- Stop for overlapping user changes or certification inputs that do not match
  the currently promoted manifest.

### Step 1 — Implement codecs and independent readers against the contract

Deliverables:

- Implement parsing and validation for the frozen task, archive, effects,
  attempt, comparison, call-control, process-registry, continuity, phase-
  candidate, campaign, and candidate-validation schemas.
- Implement canonical archive builder/extractor and the validator skeleton
  before the producer.
- Implement the validator import allowlist and AST/import-graph enforcement.
- Implement the pure structured certification audit used by both ordinary
  doctor and continuity readiness. It returns complete expected/actual input
  and apparatus maps, pins/profile, subject rows, identity/auth/schema-replay
  facts, and the complete mismatch set. No code parses doctor prose.
- Split `comparator_program_sha256` from `comparison_report_sha256` and update
  the internal certification schema so doctor rehashes the current fixed
  comparator program.

Gate:

- Schema, archive, structural-readiness, digest-swap, and import-boundary
  mutation tests pass without provider calls.
- The largest schema-accepted task passes the worst-case envelope calculation
  and produces a valid maximum-limit offline fixture.
- The fixed comparator’s judgment behavior remains unchanged.

### Step 2 — Implement call control, spawn broker, and bounded launcher

Deliverables:

- Implement the session-wide call state machine, global budget, journal, and
  no-call refusal envelopes, including strict single-flight permits, retry
  ownership, permit-time usage gates, and finite leases.
- Implement the supervisor-owned start-barrier spawn broker and process
  registry for every model-bearing and verifier/helper phase.
- Implement bounded step and complete-run launchers for normal, exception,
  signal, and overflow paths.

Gate:

- The real `sample(retry(step))` black-box matrix proves exact call precedence.
- Kill-window, registry-corruption, stale-inflight, stale-retry, wrong-owner,
  supervisor-EOF, stdout/stderr saturation, and cleanup tests pass with zero
  live registered groups.

### Step 3 — Implement the three-workspace runtime and artifacts

Deliverables:

- In plan-only mode, compute the virtual bundle manifest without writes. In live
  mode, assemble and validate `bundle/` only after atomic destination creation
  and before paid authorization or calls.
- Implement independent precheck, agent, and postcheck workspaces.
- Implement descriptor-stable manifests, scan, complete delta, effects archive,
  resource bounds, quarantine metadata, and cleanup evidence.
- Implement producer routing to the existing subject-specific command builders
  and decoders. Only the producer may reuse them.

Gate:

- Fake tasks cover success, valid task failure, operational retry, fatal latch,
  verifier mutation, unexpected effects, deletion, mode change, unsupported
  nodes, path collisions, concurrent mutation, oversize resources, credential
  findings, and attempt-envelope overflow.
- No verifier effect can enter the authoritative agent artifact.

### Step 4 — Mirror fixed probes and add the conformance task

Deliverables:

- Keep fixed `write`, `repair`, and specialized `guard` behavior unchanged.
- Mirror `write` from the same prompt, fixture, exact content digest, and
  exact-effect policy.
- Mirror full `repair` prompt, fixture, exact command bytes, normalized ordered
  lifecycle, and exact changed-path policy.
- Add the declarative conformance task with a nested path containing spaces,
  Unicode, one edit, one creation, one unchanged neighbor, red precheck, green
  postcheck, and retained effect assertions.

Gate:

- Fixed/declarative drift tests and every named repair case in the finite,
  versioned test-vector manifest pass; the gate cannot expand implicitly during
  implementation.
- No guard apparatus appears in `agent-task/v0.1`.

### Step 5 — Complete independent comparison and offline conformance

Deliverables:

- Retain bounded raw event corpora for all five subjects.
- Complete independent lifecycle, manifest/delta, artifact, provenance,
  call-journal, and task-outcome validation.
- Implement exact-five comparison, digest-bound phase candidates, and the
  generic campaign manifest.

Gate:

- Validation succeeds with producer code and live subject state unavailable.
- Mutations of raw events, producer summaries, effects, manifests, provenance,
  call journals, stores, and digests are independently rejected.
- Every accepted offline store passes `hwb verify`; no unexpected store exists.

### Step 6 — Packaging, CI discovery, documentation, and offline release gates

Deliverables:

- Change every subject-test discovery surface from the single
  `test_experiment.py` file to an intentional `test*.py` pattern, including CI,
  `RELEASING.md`, installed-artifact verification, subject documentation, and
  release-engineering assertions.
- Add a control proving a second subject test module runs from wheel and sdist
  materializations.
- Add package-data coverage for every new nested fixture and archive extension.
- Document authoring, call control, archive/effects review, verifier trust,
  non-containment, cost multiplication, candidate review, and stale-evidence
  rules in README, subject README, `CHANGELOG.md`, and `RELEASING.md`.
- Add an experiment learning record or an explicit documented reason this work
  is not an `experiments/` entry.

Gate:

- Focused agent-task, all adapter, and all core tests pass with reported counts.
- `gitleaks` passes over source and all retained/decoded offline fixtures.
- From source checkout, clean wheel, and normalized sdist installations:
  materialize subjects, run all five fake routes, verify every store, run the
  independent comparator, and compare canonical facts.
- These checks run from an unrelated cwd with an ambient `PYTHONPATH` that the
  launcher removes, and
  the retained candidate still runs after its original materialized source is
  made unavailable.
- Isolated-mode tests prove that only the validated absolute bundle root can be
  inserted by the fixed bootstrap; neither the source checkout nor ambient cwd
  is importable.
- Archive member paths and modes are inspected explicitly.

### Step 7 — Freeze one finite integration generation and restore fixed certification

An integration generation uses explicit roles rather than permanent A/B names:

- **Eₙ**: clean execution revision containing all implementation and
  documentation that the paid evidence will exercise;
- **Pₙ**: promotion-only child of Eₙ containing the exact reviewed
  `adapter_certification.json` change; and
- **Hₙ**: accepted PR head, which must equal Pₙ in that generation.

Deliverables:

- Finish all certification- and generic-bound code and documentation, run every
  offline gate, commit clean Eₙ, open or update a draft PR, and complete review,
  hosted CI, and CodeQL on Eₙ before paid work wherever those checks do not
  require the promoted manifest.
- Freeze tracked changes for the generation. One authorization covers one paid
  campaign attempt only; no failure automatically repeats a paid step.
- Generate a continuity manifest binding Eₙ, the prior certification digest,
  full prior/new repair-input maps, exact mismatch set, pins/profile, apparatus,
  comparator program, and prior comparison report.
- Use the structural continuity audit—not doctor prose—for `certify`, its route
  canary, and every preflight/postflight. All non-expected facts must pass.
- Show fresh usage and explicitly authorize the fixed campaign: 18 nominal and
  33 maximum calls (`3` route canary plus `15` nominal/`30` maximum matrix).
- Run the hard-coded three-draw repair recut at Eₙ, verify every produced store,
  run the unchanged fixed comparator, retain the candidate, and perform offline
  review from a clean materialization of Eₙ.
- Produce immutable `candidate-validation.json` first. It binds the reviewed
  fixed candidate and evidence but contains no proposed certification, patch,
  promotion review, Pₙ, or downstream receipt digest.
- Produce proposed certification JSON binding the upstream candidate-validation
  digest, then produce its exact patch digest. Apply only that reviewed proposal
  with `apply_patch`, verify exact target bytes, and commit only that promotion
  as Pₙ.
- After Pₙ exists, retain an external promotion receipt binding Eₙ, Pₙ,
  old/new target digests, fixed candidate, candidate validation, proposal,
  patch, continuity manifest, route canary, comparator program, and comparison
  report. The receipt is downstream and is not referenced by the in-tree
  certification document.

Fixed certification evidence executes at and binds Eₙ. The resulting
reviewed certification is promoted only by the Pₙ child, so it necessarily
certifies the pre-promotion execution revision rather than claiming that paid
fixed evidence ran at Pₙ. Generic evidence executes at Pₙ and binds Pₙ
as its promotion revision in every generic plan, store, checkpoint, candidate,
and campaign manifest.

The promoted internal certification retains:

- `evidence_source_commit` set to Eₙ, the code actually exercised;
- `certification_candidate_sha256`;
- `candidate_validation_sha256`;
- `continuity_manifest_sha256`;
- `route_canary_report_sha256`;
- `comparator_program_sha256`;
- `comparison_report_sha256`; and
- per subject `run_id`, `run_store_tree_sha256`, `record_json_sha256`, and
  `integrity_json_sha256`.

It does not contain Pₙ, a promotion-receipt digest, or a proposal-bearing
review digest, so neither the Git commit nor retained artifacts are
self-referential. Doctor validates schema shape and bound source/comparator
relationships without requiring HEAD to equal Eₙ or gitignored evidence to
remain locally present.

Gate:

- Exactly five fixed-repair stores exist, each with three successful draws and
  at most six base executions; no unexpected directory exists.
- Every produced directory is discovered and verified; the comparator reports
  3/3 adapter and outcome passes for all subjects with zero timeouts.
- Continuity proves the complete mismatch set was exactly authorized at Eₙ.
- Doctor and all offline suites pass normally at Pₙ with all five adapters
  ready.
- The exact reviewed promotion-only change from Eₙ to Pₙ is the sole
  certification-bound change exempt from recertification. Any other tracked
  change ends the generation; it does not trigger an automatic recut.

### Step 8 — Run guarded declarative acceptance at promotion revision Pₙ

Run hosted checks required by the promotion-only change on exact Pₙ before
generic calls. Generic acceptance then runs only from clean Pₙ. Its campaign
manifest binds Pₙ and every generic-bound input.

Before calls, display exact execution-plan, task, archive, apparatus, validator,
spec, and comparator-program digests; subject pins; fresh usage; rolling `80`
and weekly `90` stop thresholds; derived phase timeouts; call-control maximum;
and nonexistent resolved destination. A comparison-report digest cannot exist
before comparison and is computed, displayed, and bound only afterward.

Use a new generic-campaign call-control service. Authorization is split and is
never inferred across the phase boundary:

1. Explicitly authorize one canary-plus-write-smoke attempt of 8 nominal and 13
   maximum calls (`3` route canary plus `5` nominal/`10` maximum write smoke).
2. Only after the immutable smoke phase-validation checkpoint passes and a
   fresh usage gate passes, display the remaining plan and explicitly authorize
   one repair-matrix attempt of 15 nominal and 30 maximum calls.

The informational combined generic ceiling remains 23 nominal and 43 maximum;
the informational fixed-plus-generic ceiling remains 41 nominal and 76 maximum.
Neither combined number is an authorization.

Usage evidence is retained, digested, and independently validated at every
phase boundary and at every provider permit:

- session before canary;
- nested canary before/after and post-gate;
- immediately before every canary, smoke, and matrix provider call;
- before smoke;
- after smoke/before matrix; and
- final after matrix.

The matrix starts only after the write-smoke phase-validation checkpoint passes
independent review, a fresh usage reading passes the gate, the workflow
displays the remaining nominal/maximum calls, and the separate repair-matrix
authorization is recorded. A validated nested post-canary snapshot may
serve as the smoke boundary gate only when its schema, timestamp, limits,
bytes, and delta are independently accepted; it does not replace permit-time
usage gates.

Gate:

- `records/write-smoke/` has exactly five stores, each with one draw and at most
  two base executions. Its exact-five comparator reports 1/1 adapter-valid,
  safety-eligible, and task-passing for every subject with zero timeouts before
  the smoke checkpoint and matrix authorization.
- `records/repair-matrix/` has exactly five stores, each with three successful
  draws and at most six base executions. Its separate exact-five comparator
  reports 3/3 adapter-valid, safety-eligible, and task-passing for every subject
  with zero timeouts.
- No unexpected immediate child exists in either run root, and every produced
  store passes `hwb verify`.
- The smoke checkpoint passes before matrix authorization. After repair and
  clean control-plane shutdown, both final phase candidates and the campaign
  manifest pass independent digest and topology validation.
- Call journal, process registry, cleanup, usage, source-drift, and credential
  evidence pass independently.
- No generic evidence edits or promotes `adapter_certification.json`.

### Step 9 — Accept one exact head, merge, and prove post-merge state

Deliverables:

- Set Hₙ = Pₙ. The accepted generic campaign’s source and input maps must
  equal that exact PR head, and all required hosted CI and CodeQL checks must be
  green on it.
- Make no tracked changes after Pₙ within the generation. If review or CI
  requires any change, stop the generation. A new generation Eₙ₊₁/Pₙ₊₁
  requires a new destination, fresh offline gates, fresh usage, and explicit
  authorization; the workflow never loops automatically into paid recuts.
- If the same operational or acceptance failure recurs once under unchanged
  inputs, stop and redesign instead of authorizing another identical recut.
- Merge only the exact accepted head. On merged `main`, rerun offline suites,
  package checks, gitleaks, doctor, and one plan-only all-five command from an
  unrelated cwd. If merged tree bytes differ from Hₙ, stop; do not silently
  run another paid campaign.

Gate:

- `main` is clean and matches `origin/main` at the merge commit, and its relevant
  tree maps equal the accepted Hₙ maps.
- Report PR and hosted-check URLs, merge commit, exact local test counts,
  comparator results, evidence paths, usage before/after/delta, call-journal and
  cleanup summaries, promoted certification lineage, and remaining threat-model
  limitations.

## Global stop conditions

No further provider call is permitted when:

- usage is unreadable or meets a configured gate;
- the destination is not nonexistent and atomically creatable;
- the call-control state is missing, corrupt, stale, uncertain, or latched;
- the global call maximum would be exceeded;
- a prerequisite, capability, route, pin, profile, identity, authentication,
  structural readiness, or schema-replay fact fails;
- any bundle, apparatus, task, source, spec, validator, comparator, or continuity
  digest drifts;
- any credential detector or redaction count is positive;
- any resource, stdout, stderr, archive, or serialized-envelope bound fires;
- any registered process group lacks a clean terminal receipt;
- any verifier mutates its workspace or its authority cannot be revalidated;
- any unexpected, partial, invalid, or unbound store/directory exists; or
- satisfying a test would require weakening fixed adapter or outcome validation.

The permit path revalidates the allowed phase-root shape, bundle/apparatus
digests, call state, call budget, and fresh usage immediately before every
release. A condition discovered only during post-run review cannot retroactively
prevent an earlier call; it makes the candidate ineligible and latches every
later permit. Boundary review remains mandatory in addition to permit-time
checks.

An ordinary first operational failure may consume only its already-authorized
single retry. A second failure latches the campaign before any later provider
call. Valid task failure is retained without retry or safety latch.

Operational failure never authorizes evidence deletion. The surviving
supervisor, or the broker after supervisor-channel loss, retains all evidence it
can safely finalize: usage-after when readable, call control, process cleanup,
partial stores, source-drift checks, and credential-scan results. Host crash,
power loss, and uncatchable supervisor-and-broker termination retain only the
evidence already durably written and are reported as stale/incomplete on the
next inspection.

## Definition of done

This milestone is complete only when a user can author one bounded immutable
agent task, generate one exact-five ordinary Workbench spec set per phase,
inspect a zero-call plan, execute explicitly authorized guarded fixed and
generic campaigns, verify every produced store, compare all five subjects, and
review exact effects without automatic artifact application or declarative
certification promotion.

The completed milestone proves reproducible, bounded, tamper-evident execution
for the stated trusted-task threat model. Transparent reinterpretation of
arbitrary deterministic `argv`, arbitrary multi-step agent workflows, hostile
same-UID containment, every Workbench campaign, automatic artifact application,
and promotion into the public Workbench schema remain future milestones.
