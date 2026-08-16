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

## E03 — Shared red → edit → green repair

**Question.** Does the candidate adapter boundary survive a second workload
that needs reads, a failing command, a code mutation, and a passing command,
without requiring identical implementations from every harness?

**Expectation.** Each structurally valid record must preserve its subject's
native evidence. A passing task outcome additionally requires an externally red
initial suite, only `slugger.py` changed, an externally green final suite, and
recognized `python3.11 -m unittest -v` evidence ordered as red command → write →
green command.

**Setup.** Claude Code `2.1.233` with pinned Haiku, Codex CLI `0.144.1` with
`gpt-5.6-luna`, and Hermes Agent `0.16.0` with content-pinned local
`qwen3.5:9b`. All arms receive the same four-file fixture, prompt, ten frozen
inputs, capture limits, and semantic oracle. Claude receives Read/Edit/Bash,
Codex uses its workspace sandbox, and Hermes receives file/terminal tools plus
pre/post shell hooks.

**Evidence.** Final sealed discovery runs: Claude
`20260816T051134Z-775f00-4aaf`, Codex
`20260816T051134Z-e20662-04c1`, and Hermes
`20260816T051220Z-9be918-2cb9`. All records conform. `compare.py` returns
`contract_passed: true` with matching ten-input freeze, receipt, and adapter
maps.

**Result.** Claude and Codex both passed. Claude exposed three reads followed by
the exact red → edit → green sequence. Codex exposed three setup commands,
followed by the same recognized sequence and one final command. Their final
`slugger.py` SHA-256 values differ, while both external suites pass and all
invariants match. Hermes completed one successful read hook pair and terminated
normally, but never attempted the required tests or edit; `slugger.py` remained
unchanged and the external final suite remained red. Its adapter passed while
its outcome failed, without invalidating the three-record contract comparison.

**Learned.** A reusable outcome boundary must permit semantically equivalent
effects while tightly constraining changed files and causal sequence. Tool
family alone is too weak: “some failing command” could manufacture a false red
control, so the normalized evidence needs a recognized operation marker. A
contract-compliant record can still describe a subject failure.

**Code consequence.** **Promote after repetition.** `effect_kind` and optional
`operation` now extend normalized tool attempts, and the adapter accepts named
workloads with separate fixtures/prompts/oracles. Keep workload recognition and
the repair oracle outside Workbench core. Core promotion remains blocked because
Hermes has not completed the second workload.

**Limits.** The recognized operation is one exact unittest substring, not a
general command parser. The suite is small and visible to the subject. No hidden
tests, dependency install, multi-file repair, or interactive continuation is
covered.

**Next.** Diagnose why local Hermes stops after a successful read, then retry
with a smaller context/model configuration without weakening the oracle.

## E04 — Bounded capture, partial timeout, and credential scrubbing

**Question.** Can the shared process boundary stop unbounded output, preserve a
partial effect on timeout without inventing a terminal event, and prevent an
inherited credential value from reaching persisted evidence?

**Expectation.** Output beyond 128 bytes terminates the owned process group with
a typed capture-limit reason; a timed-out process leaves its declared partial
file and records no native terminal; a synthetic secret printed on both streams
is absent from text, base64-decoded bytes, and serialized capture metadata.

**Setup.** `fault_runner.py` uses the same streaming process primitive and
capture serializer as the live adapters. The Workbench discovery spec freezes
and receipts `fault_runner.py` plus `common.py`.

**Evidence.** Sealed discovery run `20260816T051134Z-1cee3c-d7b8` conforms and
passes. The deterministic suite now passes 27 tests, including sidecar-limit
refusal, hook-payload scrubbing, and an escaped child holding inherited output
pipes open after its parent exits.

**Result.** The output-pressure child was terminated with return code 125,
`stdout_limit`, 10,001 observed source bytes, and exactly 128 stored bytes. The
timeout child returned 124 with `timeout`, retained `partial.txt`, and explicitly
reported `native_terminal_event: false`. Both stdout and stderr stored only
`[REDACTED]\n`; the synthetic credential does not occur in serialized evidence.

**Learned.** “Raw evidence” cannot mean unlimited or secret-bearing bytes.
Useful evidence is bounded, integrity-digested after redaction, and explicit
about the gap between source bytes and stored bytes. Timeout and task effect are
independent facts just like adapter and outcome success.

**Code consequence.** **Candidate for reuse.** The shared primitive now streams
stdout/stderr under positive limits, terminates on overflow or timeout, records
typed termination/overflow metadata, scrubs credential-looking environment
values before serialization, removes those variables from local Hermes, and
bounds the Hermes hook sidecar. The capture loop also returns after its grace
period when an escaped child keeps a pipe open. Do not move it into core until
escaped-child containment and adversarial redaction encodings are tested.

**Limits.** The credential is synthetic. Exact-value replacement does not detect
encoded, transformed, split, or exfiltrated secrets. The escaped-child test
proves reader liveness, not containment: the new session briefly survives.
Source byte counts stop when the owned group is terminated; they are not the
number an unbounded process would eventually have emitted.

**Next.** Add adversarial encoding mutations and an actual escaped-session
containment mechanism before treating the redaction and process boundary as
security controls.
