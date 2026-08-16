# Extracting the adapter capture primitive

What this decides: which code moves into `src/harness_workbench/` as a
supported API, and which code stays adapter-local forever. It does not
promote the `cross-harness-adapter-run` envelope, and it does not make any
external harness a dependency of anything.

The distinction matters because the two are routinely confused. The envelope
is a *schema* describing what five named third-party harnesses emit; its
promotion is still blocked, for the reasons its own gate lists
([SHARED_ADAPTER_CONTRACT.md](../src/harness_workbench/subjects/SHARED_ADAPTER_CONTRACT.md)).
The primitive below is *running a hostile subprocess and coming back with
evidence you can defend*, which is a Workbench concern whether or not a single
adapter ever ships.

## The evidence

Two implementations were written independently, months and one design
generation apart, against different harnesses, by someone who was not
consulting the other file. They converged on the same function set. That is
the whole argument for promotion: not that the code is nice, but that the
second author could not avoid rewriting the first author's list.

| Concern | `subjects/common.py` | `experiments/pi_coding_agent/adapter.py` |
| --- | --- | --- |
| bounded subprocess | `run_bounded` | `run_bounded` (identical name) |
| canonical digest | `canonical_digest` | — |
| file digest | `file_digest` | `sha256_file` |
| tree snapshot | `manifest` | `file_manifest` |
| credential hygiene | `credential_values` + `redact_bytes` | `minimal_environment` |
| capture envelope | `capture_bytes` | `capture_evidence_file` |
| JSONL decode | `parse_jsonl` | (inline in `capture_evidence_file`) |
| path containment | `normalized_path` | `_regular_relative`, `_directory_relative`, `_evidence_path` |
| process-group control | inline `signal_group` | `_signal_group`, `_wait_for_group_exit`, `_process_group_exists` |

Two rows are not what the convergence list claimed.

**Credential hygiene is not one function written twice — it is two opposite
halves.** `subjects/` copies the whole ambient environment into the child and
then scrubs secrets back out of the captured bytes. The Pi adapter builds a
minimal environment so there is nothing to scrub, and has no redaction code at
all. Each covers the hazard the other cannot: a minimal environment cannot
scrub a secret the subject legitimately needs and then echoes (Hermes has
exactly one such key, restored by name); a scrubber cannot see a secret that
leaks into a file the subject wrote rather than into stdout. Promoting either
alone would ship a known hole, so the promoted API offers both and the
existing behaviour of each caller is reachable.

**Path containment converged three-to-one and was on nobody's list.** Both
implementations independently grew "reject a path that escapes its root",
because both discovered subjects that propose them — Hermes proposed
outside-workspace paths during probing. It is promoted.

## What core already has

`canon.py` already implements three of the pairs above: `digest_obj` is
`canonical_digest` (same JSON rule, plus `allow_nan=False`, which is strictly
stronger), `digest_file` is `sha256_file` with chunking, and `digest_tree` is
`package_tree_digest` minus the `node_modules` skip that its `skip` argument
already takes.

So the digest functions are **not promoted — they are deleted and their
callers repointed at `canon`.** Adding a second digest implementation to a
project whose central claim is "the digest binds the experiment" would be the
worst possible outcome of a promotion exercise.

One incompatibility is load-bearing. `canon` returns `"sha256:" + hex`; the
adapter manifests and capture envelopes store **bare hex**, and sealed
discovery records already contain those bytes. The promoted code computes
through `canon` and strips the prefix at the wire edge, in one named place,
rather than reformatting sealed evidence. The prefix is a display convention;
the sealed bytes are a commitment.

## What Gate P7 asks to be named

### Repeated fields

Every adapter, on every subject, records the same eleven facts about a run and
nothing about the harness produces them: `returncode`, `termination_reason`,
`stdout`/`stderr` stored bytes, `source_bytes` per stream (what the subject
*tried* to emit, which is not what was kept), `sha256` per stream,
`overflow` per stream, `redaction_count`, the before-manifest, the
after-manifest, and whether the process group outlived the process.

`source_bytes` and `redaction_count` are the two that look optional and are
not. Without `source_bytes`, a truncated capture is indistinguishable from a
quiet subject. Without `redaction_count`, a scrubbed capture is
indistinguishable from one that never contained a secret, and the digest of
the stored bytes silently means something different.

### Lifecycle

Spawn in a new session (so the child is a process-group leader) → read both
streams until EOF, deadline, or byte limit → on any bound, signal the *group*,
not the process → wait a declared grace → escalate to `SIGKILL` → observe
whether the group is still alive → record the reason. Terminal state is
whichever bound fired first, recorded by name.

The group is the unit of control, not the process. A subject that spawns a
shell that spawns a build leaves orphans that hold the workspace open and
corrupt the next run's before-manifest. The Pi implementation observes group
liveness after cleanup and the `subjects` one does not; that observation is
promoted, because a cleanup you never check is not a cleanup.

### Provenance

Every captured byte carries how it was obtained and what it is a claim about.
Stored digest and source count travel with the bytes, never alongside them.
The primitive stops at "here is what the process emitted, bounded and
digested"; the *meaning* of those bytes — which tool ran, whether the task
succeeded — is a normalizer's and an oracle's problem, and neither is promoted.

The primitive therefore never parses harness-specific events. It offers
`parse_jsonl` because "these bytes are line-delimited JSON" is a format fact.
It offers nothing that knows what a `tool_use` is.

### Failure semantics

Stated as rules, because each was learned by having it fail:

- **A bound firing is not an error.** Timeout, byte-limit, and nonzero exit
  are *measurements*, returned in the envelope. The primitive raises only when
  it cannot measure — bad limits, unspawnable argv.
- **A synthesized exit code is a lie and is not promoted.** `common.py` maps
  timeout to `124` and byte-limit to `125`, which are indistinguishable from a
  subject that genuinely exited `124`. The promoted API keeps the real
  `returncode` and puts the reason in `termination_reason`. This is the one
  behaviour change in the promotion, and it is deliberate.
- **Missing evidence is a recorded state, not an exception.** `exists: False`
  with `required: True` yields an error *in* the envelope. A run that failed
  to instrument must stay comparable to one that did.
- **Absence of error is never a receipt.** Nothing in the primitive reports
  success by staying quiet; every bound and every cleanup returns a positive
  observation. Three silent instrumentation failures on DeepSeek produced a
  perfectly clean run that measured nothing, which is why.

## Promotion set

Into `src/harness_workbench/`, as public API:

- bounded subprocess execution with per-stream limits, deadline, process-group
  termination and escalation, and post-cleanup group-liveness observation
- the capture envelope (stored bytes, source count, digest, overflow,
  redaction count, UTF-8 decode, optional JSONL decode)
- file-backed capture with `exists`/`required` semantics
- tree manifests (path, mode, size, digest)
- credential discovery and byte redaction
- minimal-environment construction
- path containment against a declared root
- JSONL decoding

## Stays out

Target-specific, by construction: every `_normalize_*`, every `_*_command`,
`dsh_patch.yml`, `hermes_config.yaml`, `verify_pi_install` and all pin
identities, `package_tree_digest`'s `node_modules` skip (an argument, not a
behaviour), Pi's `PI_OFFLINE`/`PI_CODING_AGENT_DIR` defaults (overrides passed
by the caller), and the `pi-stdout.jsonl` spool filenames.

Workload oracles, absolutely: `outcome`, `repair_outcome`, `EXPECTED_CONTENT`.
These answer "did the subject do the task", which is the question the
primitive exists to stay out of.

The adapters themselves are not promoted and never will be. They are pinned to
exact third-party releases, one of them a breaking-change developer preview.
They ship as an opt-in tree, they are data, and a test enforces that no module
under `src/harness_workbench/` imports from `subjects/`.

## How this gets checked

An extraction is only real if something consumes it. One adapter is rewritten
to import the promoted API and nothing else from the old helpers; if it has to
reach past the boundary, the boundary is wrong and moves. Then a determinism
soak runs the primitive alone against fake executables — success, nonzero
exit, malformed output, saturation, timeout, ignored termination, orphan
child, corrupt evidence — and requires an identical projection digest across
runs. Steadiness under those eight, not an outcome rate against any model, is
what gates this.
