# Cross-harness adapter experiment learnings

This record follows
[`docs/experiment-writeups.md`](../../docs/experiment-writeups.md).

## E01 — One effect across three independent harnesses

**Question.** Which evidence responsibilities remain common when Claude Code,
Codex CLI, and Hermes Agent perform the same exact file-write workload?

**Expectation.** All subjects can share identity, bound-input, invocation,
raw-capture, lifecycle-provenance, workspace-manifest, adapter-verdict, and
outcome-verdict fields without pretending that their native events are equal.

**Setup.** Claude Code `2.1.228` with pinned Haiku, Codex CLI `0.144.1` with
`gpt-5.6-luna`, and Hermes Agent `0.16.0` with content-pinned local
`qwen3.5:9b`. Every arm receives the same prompt and exact-byte oracle. The
Workbench specs declare the same inputs and enable `freeze` plus `receipt`.

**Evidence.** `python3.11 -m unittest -v test_experiment.py` passes 19
deterministic tests. Final sealed discovery runs: Claude
`20260815T225759Z-fc8bed-7f5d`, Codex
`20260815T225759Z-b50088-4b02`, and Hermes
`20260815T225818Z-eae7d0-dd8e`. All three Workbench records conform. Running
`python3.11 compare.py` on those directories returns `contract_passed: true`
with one shared eight-input freeze/receipt/adapter digest map.

**Result.** Claude and Codex both produced valid native terminal lifecycles and
the exact durable effect. The final Hermes run terminated after three correlated
write attempts, but every proposed path was outside the disposable workspace.
`HERMES_WRITE_SAFE_ROOT` denied all three and the expected workspace remained
unchanged, so both the adapter and outcome verdicts failed. Other Hermes probes
were not stable: one wrote the exact workspace effect but did not terminate,
one timed out before a tool call, and one followed stale inherited cwd state and
wrote the effect outside the disposable workspace. The escaped generated file
was removed. The adapter now binds all Hermes cwd variables and rejects any
hook-observed outside-workspace path. An earlier probe under macOS's default
temporary root also showed that protected-path policy could dominate the
intended workload.

**Learned.** The common contract is a shared evidence envelope, not a shared
event vocabulary. Acquisition method and completeness must be explicit. A
valid adapter lifecycle does not prove task success, and a valid durable effect
does not prove clean termination.

**Code consequence.** **Candidate for reuse.** Keep the candidate in this
experiment and require capabilities, raw evidence, stage-labelled tool attempts,
external manifests, and separate verdicts. Do not yet introduce a base adapter
class or Pi/Claude/Codex/Hermes branches into Workbench core.

**Limits.** This is one write-only task. Claude and Codex rely on hosted services;
their model labels are not immutable model digests. Hermes has not produced one
clean end-to-end success in the final frozen configuration, and environment
binding plus a tool safe root is not OS containment. No cancellation,
bounded-capture pressure, credential-redaction mutation, or second workload has
been run here.

**Next.** Seal and compare the three records, then run a read-edit-test workload
through the same adapters and add timeout/cancellation mutation cases.

## E02 — Ambiguous literal and lifecycle false success

**Question.** Can the experiment distinguish a structurally valid run from a
wrong exact effect, and reject malformed lifecycle ordering?

**Expectation.** A subject that writes extra punctuation must pass lifecycle
normalization but fail the outcome oracle. Duplicate terminals, duplicate tool
IDs, orphaned completions, and post-before-pre hooks must fail normalization.

**Setup.** The first Codex prompt placed a sentence period immediately after the
literal. Synthetic mutation tests exercise each lifecycle false-success class.

**Evidence.** The Codex probe completed normally but produced a 23-byte file
ending in `control.\n`; the oracle rejected its SHA-256. After the prompt moved
the literal into a fenced block and stated its byte count, Codex produced the
expected 22-byte file. All 19 deterministic tests pass.

**Result.** Transport remained valid in the wrong-byte run while outcome failed.
Every injected malformed lifecycle is rejected by its subject normalizer.

**Learned.** Exact-effect tasks need unambiguous byte-level prompts, but prompt
clarity is not a substitute for an external oracle. Lifecycle validity and
workload correctness are orthogonal.

**Code consequence.** **Promote after repetition.** The separate verdict boundary
now repeats the Pi result. Keep the contract requirement, but wait for the second
workload and bounded-capture work before changing Workbench core.

**Limits.** The mutation set is small and does not yet cover truncated raw
captures, forged digests, altered hook results, or escaped child processes.

**Next.** Add contract-comparison mutations for raw-capture digests, receipt
maps, capability claims, and partial timeout effects.
