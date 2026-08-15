# Pi Coding Agent experiment learnings

This is the learning record for the Pi reference integration. It follows
[`docs/experiment-writeups.md`](../../docs/experiment-writeups.md).

Pi-specific event names and extension behavior stay in this directory. A
finding becomes a shared-code candidate only when it describes a boundary that
another independent harness also needs.

## E01 — Guard inversion with a positive control

**Question.** Can Harness Workbench prove that a Pi extension's block/allow
decision causes the corresponding durable filesystem effect, rather than merely
recording that the extension loaded?

**Expectation.** With identical pinned inputs, `permitted.txt` must be written
in both arms. `forbidden.txt` must exist only in the allow arm, and the guard
decision plus Pi tool result must invert with that effect.

**Setup.** Pi `0.84.1`, Node `22.22.3`, the same offline scripted provider and
guard extension, separate `block.json` and `allow.json` confirmation specs,
`freeze` plus `receipt`, and `verify_pair.py` as the causal-isolation oracle.

**Evidence.** Block run `20260815T200809Z-f9d432-e4ae`; allow run
`20260815T200810Z-d81a75-1999`. Both conform. The pair verifier passed with 12
matching input digests and exactly three declared differences: guard decision,
forbidden tool result, and `forbidden.txt` effect.

**Result.** The positive control succeeded in both arms. The treatment write was
blocked without a file effect in one arm and allowed with the exact file effect
in the other. No undeclared stable difference survived the pair projection.

**Learned.** Loading a policy is not evidence that the policy controls behavior.
The useful proof crosses three layers: policy decision, subject-harness tool
result, and external durable effect. A positive control is necessary to
distinguish selective enforcement from a generally broken workload.

**Code consequence.** **Candidate for reuse.** Keep `guard_extension.ts`, its
sidecar schema, `control_oracle.py`, and Pi lifecycle details local. Reuse the
three-layer decision/result/effect pattern when onboarding another harness. Do
not add a Pi-aware policy abstraction to Workbench core. Promote a generic
controlled-external-subject helper only after a second harness repeats the
shape.

**Limits.** This proves one `write` decision under an offline deterministic
provider. It does not establish shell-policy coverage, sandboxing, network
containment, extension composition, or live-model performance.

**Next.** Run the plan-mode enforcement pair with read-only positive controls
and obfuscated write attempts.

## E02 — Reusable one-shot adapter probes

**Question.** Is `adapter.py` genuinely reusable, or is it the guard experiment
with its verdict renamed?

**Expectation.** The same adapter must support workloads with different
fixtures, prompts, providers, tools, and effects without importing the guard or
coding oracle.

**Setup.** Three independent consumers exercise text-only/no-tools, Unicode and
space-containing `read`→`edit`, and `read`→test→edit→test coding flows. Fault and
concurrency tests vary malformed evidence, provider/extension failure, timeout,
signals, output pressure, and eight simultaneous retained workspaces.

**Evidence.** `python3.11 -m unittest test_experiment.py` passed all 50 tests on
2026-08-15, including real pinned Pi runs. `test_generic_adapter_contains_no_control_oracle`
guards the separation directly.

**Result.** One adapter launched and captured all three workload shapes. It
retained raw JSONL, produced a strict canonical projection, bound consumed
inputs, excluded ambient Pi state, and kept concurrent workspaces distinct.

**Learned.** The reusable boundary is not “an adapter that knows coding.” It is
the smaller mechanism that pins a subject, constructs a controlled workspace,
launches one bounded session, preserves raw evidence, and normalizes only the
lifecycle facts needed by an external oracle.

**Code consequence.** **Promote after repetition.** Treat the current envelope
as a candidate adapter contract for Claude, ChatGPT, or Hermes, but copy the
responsibilities—not the Pi schema—into the next integration. After a second
harness, compare envelopes and extract only the common fields. Keep provider
registration, Pi arguments, and session version checks local.

**Limits.** One-shot JSON mode cannot faithfully test steering, queued
follow-ups, forced compaction, reconnects, or interactive cancellation. POSIX
process-group cleanup is not containment of escaped sessions.

**Next.** Implement the second external-harness adapter before extracting a
shared adapter base class or canonical lifecycle package.

## E03 — Coding repair outcome and false-success matrix

**Question.** Does a structurally valid Pi run prove that the coding repair
succeeded?

**Expectation.** A valid repair must prove the exact sequence red test → intended
edit → green test, change only `slugger.py`, preserve the task and tests, and
finish normally. Known false-success mutations must fail the outcome oracle.

**Setup.** Pi `0.84.1` uses its real `read`, `bash`, and `edit` tools against a
synthetic Python fixture. `coding_runner.py` wraps the generic adapter with
`coding_oracle.py`. The sealed Workbench spec freezes and receipts all 11
consumed inputs.

**Evidence.** Confirmation run `20260815T200401Z-2d764f-3bc0` completed and
conformed with no freeze drift. The comparison reports initial failure, final
success, `changed_paths: ["slugger.py"]`, and unchanged invariants. The mutation
matrix rejects an initially green test, missing initial test, missing command,
signaled test result, wrong edit target, still-red final test, absent final
test, volatile final output, unchanged implementation, changed invariant,
surprise file, reordered tools, and adapter failure.

**Result.** The real Pi workload satisfied the complete oracle. Every injected
false-success case was rejected.

**Learned.** Adapter success means transport and lifecycle success, not task
success. Pi's JSON stream already exposes enough correlated evidence—ordered
tool calls, argument digests, result digests, error states, and terminal
lifecycle—to build a strong task oracle without changing Workbench core.

**Code consequence.** **Change now, completed locally.** Commit `72756fb` added
the coding runner/oracle and changed the release-facing coding command to use
them. **Candidate for reuse:** every external-harness workload needs an outcome
oracle above its transport adapter. The oracle interface should not move into
core until a second harness demonstrates the same useful boundary.

**Limits.** The mutations are controlled counterexamples, not observed Pi bugs.
The deterministic provider tests mechanism, not model intelligence. Exact
result digests are intentionally tied to the pinned Pi fixture and are not a
cross-platform compatibility claim.

**Next.** Apply this separation to plan-mode enforcement: generic Pi capture,
plan-specific decision/effect oracle, then a negative matrix that tries to make
writes escape the read-only arm.

## Cross-harness footing established by Pi

The next harness should deliberately test this candidate stack:

1. pin the harness, runtime, dependencies, and protocol version;
2. suppress ambient configuration, sessions, tools, and credentials;
3. create a disposable fixture and bind every consumed byte;
4. preserve raw output before normalizing it;
5. normalize strict lifecycle and tool correlation without inventing a verdict;
6. bound owned processes and state the containment limit precisely;
7. use a deterministic provider or scripted backend for mechanism confirmation;
8. place the workload outcome oracle above the transport adapter;
9. add a positive control and invert one treatment at a time;
10. mutate plausible false-success paths and prove the oracle rejects them;
11. seal confirmation runs with frozen inputs and receipts;
12. compare the new adapter with Pi before extracting shared code.

The main architectural lesson is restraint: Pi gives us a tested reference
shape. Claude, ChatGPT, or Hermes must supply the second observation before the
shape becomes a Workbench abstraction.
