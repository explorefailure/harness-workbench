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

**Evidence.** `python3.11 -m unittest test_experiment.py` passed all 59 tests on
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

## E04 — Plan-mode tool policy versus action mode

**Question.** Can Pi remain useful for read-only inspection while preventing
both direct `write` calls and shell-mediated writes, then permit those same
effects in an action-mode control arm?

**Expectation.** `read` and an allowlisted read-only `bash` command must succeed
in both arms. Plan mode must leave the workspace unchanged; action mode must
create the two exact declared files from the same provider sequence.

**Setup.** Both confirmation specs bind the same 12 inputs and run Pi `0.84.1`
with the same deterministic provider and extension. The plan arm activates only
`read,bash` and blocks non-allowlisted bash. The action arm activates
`read,bash,write`. `plan_oracle.py` checks tool/result digests, active-tool and
hook evidence, unchanged controls, and exact durable effects.

**Evidence.** Plan run `20260815T203038Z-2eb055-ff2d` and action run
`20260815T203039Z-ac33a9-4684` both completed and conformed. The sealed-pair
verifier passed with one shared map of 12 frozen and receipted inputs. The plan
comparison reports both positive controls true and both write effects false.
The action comparison reports all four true. The experiment suite also rejects
a falsely successful blocked shell, a leaked plan-mode file, and missing policy
evidence.

**Result.** Plan mode preserved read usefulness and produced no write effects.
Action mode produced both exact effects. Pi returned a typed failed execution
for the inactive direct `write`; the plan extension did not receive a
`tool_call` event for that inactive tool. The unsafe `bash` remained active,
reached the extension hook, and was explicitly blocked there.

**Learned.** “Blocked by plan mode” has two distinct mechanisms in Pi. Removing
a tool from the active set prevents its extension preflight hook from seeing the
call, while constraining an active general-purpose tool requires a separate
policy decision. An audit that expects one uniform block event would falsely
report missing evidence for the direct write.

**Code consequence.** **Keep local, with a candidate for reuse.** The Pi oracle
now distinguishes inactive-tool rejection from hook-policy rejection and
requires durable-effect absence for both. Future adapters should preserve a
typed enforcement layer (`tool availability`, `policy hook`, or later
`containment`) when their subject exposes it. Do not add these Pi event semantics
to Workbench core until another harness shows an equivalent distinction.

**Limits.** The bash allowlist contains one exact safe command and one exact
unsafe command. This does not establish parser completeness, obfuscation
resistance, mode transitions within a live session, or hostile-code containment.

**Next.** Expand the plan arm with command chaining, substitution, redirects,
encoded commands, and path indirection; then test plan→act transition using RPC.

## E05 — Extension mutation and guard ordering

**Question.** If one Pi extension mutates tool arguments and another guards the
call, does reversing their load order change the durable effect?

**Expectation.** Mutate-first should let the guard inspect and block the final
target. Guard-first should expose whether a later mutation can bypass an earlier
allow decision while the positive control remains healthy.

**Setup.** Both arms use the same pinned Pi, provider, fixture, mutator, guard,
and 12 declared inputs. Only the order of the two `tool_call` extensions changes.

**Evidence.** Mutate-first run `20260815T204412Z-0dd831-4036` and guard-first
run `20260815T204413Z-808612-6669` both conformed with 12 frozen/receipted
inputs. The positive-control write succeeded in both arms. Mutate-first let the
guard observe `redirected.txt` and block it; guard-first let the guard approve
`requested.txt`, after which the mutator redirected and created
`redirected.txt`.

**Result.** Mutate-first blocked the treatment effect. Guard-first created the
mutated target even though the guard had inspected and allowed a different path.

**Learned.** Handler order is security-relevant. A guard can validate stale
arguments when a later extension mutates them. Pi's JSON `tool_execution_start`
also retains the original target even when the mutated target is the durable
effect, because the event precedes extension preflight.

**Code consequence.** **Change now, completed locally.** Normalized execution
records now label their arguments as `pre_tool_call_hook`; the composition
oracle reconciles proposal, mutation evidence, guard observation, result, and
filesystem effect. **Candidate for reuse:** future adapters must distinguish
proposed from effective tool arguments and policy must validate the final form.

**Limits.** This covers one deterministic mutation and guard. It does not prove
that every extension chain is observable or that policy-last ordering cannot be
bypassed through another execution layer.

**Next.** Test throwing handlers and conflicting block/allow handlers in both
orders, then determine whether Pi supplies a post-mutation observation seam.

## E06 — Throwing handler order and audit visibility

**Question.** When a Pi `tool_call` handler throws, does the tool fail closed,
do later handlers still run, and can the session continue to an independent
positive control?

**Expectation.** The treatment write must fail without a durable effect in both
orders. If handler dispatch stops at the exception, the downstream audit hook
must be absent only in the throw-first arm. The next control write must still
succeed.

**Setup.** Both arms use the same pinned Pi, provider, fixture, throwing hook,
audit hook, and 12 declared inputs. Throw-first registers the throwing hook
before the audit hook; audit-first reverses them. `failure_order_oracle.py`
checks exact handler evidence, Pi tool results, lifecycle completion, and
filesystem effects.

**Evidence.** Throw-first run `20260815T205310Z-c443c2-2a7c` and audit-first
run `20260815T205311Z-60a3c8-44e5` both completed and conformed with 12
frozen/receipted inputs. The 53-test Pi suite passed. Throw-first recorded only
the thrower; audit-first recorded audit then thrower. Both produced the same
failed treatment result, no treatment file, and the exact positive-control file.

**Result.** Pi converted the thrown preflight exception into an errored tool
result and prevented the write. It stopped that handler chain at the exception,
then continued the agent loop and successfully executed the next tool call.

**Learned.** Fail-closed execution and complete audit coverage are separate
properties. A missing audit record can mean that the audit hook was registered
after a failing hook, not that the tool escaped enforcement or that the audit
extension was absent. Handler-order provenance is therefore necessary to
interpret negative evidence.

**Code consequence.** **Change now, completed locally.** The new runner and
oracle expose `observed_handlers`, `failed_closed`, `treatment_effect`, and
`positive_control` separately instead of collapsing them into one pass flag.
**Candidate for reuse:** external-harness adapters should preserve handler order
and failure position whenever the subject exposes them. Do not treat missing
downstream telemetry as proof of fail-open behavior, and do not move Pi's hook
semantics into Workbench core.

**Limits.** This covers one synchronous exception during preflight and one
subsequent write. It does not test asynchronous failures, `tool_result` handler
exceptions, multiple throwing hooks, or whether an external audit sink observes
the attempted call independently of Pi's extension chain.

**Next.** Reverse conflicting block/allow handlers, then throw from a
`tool_result` handler to see whether Pi preserves the completed effect and how
the failure is represented.

## E07 — Conflicting allow/block policy order

**Question.** If one Pi `tool_call` handler explicitly returns `{block: false}`
and another returns `{block: true}`, does registration order determine the
result, or does either decision have precedence?

**Expectation.** A block-first arm should reveal whether the terminal denial
short-circuits the later allower. An allow-first arm should reveal whether an
earlier allow authorizes immediately or merely permits evaluation to continue.
The independent control write must succeed in both arms.

**Setup.** Both arms use the same pinned Pi, provider, fixture, allower,
blocker, and 12 declared inputs. Only handler order changes.
`policy_order_oracle.py` requires exact decision order, failed treatment result,
absence of the treatment effect, normal lifecycle completion, and the exact
positive-control effect.

**Evidence.** Block-first run `20260815T211543Z-50d393-ba91` and allow-first
run `20260815T211544Z-c70e85-c076` both completed and conformed with matching
maps of 12 frozen/receipted inputs. The 54-test Pi suite passed, including
mutations that falsely report treatment success or remove the terminal block
evidence.

**Result.** Block-first recorded only `block` and denied the write. Allow-first
recorded `allow` then `block` and also denied the write. Both arms created the
exact positive-control file and no treatment file.

**Learned.** Pi policy results are asymmetric. `{block: false}` is not a final
authorization and cannot override a later block; it means evaluation continues.
`{block: true}` is a terminal veto and prevents later policy handlers from
observing the call. This is a deny-overrides chain, not voting or last-write-wins
composition.

**Code consequence.** **Change now, completed locally.** The policy-order
runner and oracle expose `observed_decisions`, `allower_reached`,
`blocker_reached`, and `terminal_block_won` separately. **Candidate for reuse:**
a cross-harness policy model must represent allow/continue and deny/terminal as
different control-flow outcomes, not symmetric booleans. Keep Pi's exact return
shape local and do not add a generic policy algebra to Workbench core until a
second harness exposes comparable semantics.

**Limits.** This covers two synchronous preflight handlers over an unchanged
write request. It does not test multiple blockers and competing reasons,
argument mutation between decisions, asynchronous handlers, inactive tools, or
post-execution policy.

**Next.** Combine mutation with allow/block precedence to test whether a
terminal guard sees the final arguments, then throw from a `tool_result` handler
to measure post-effect failure semantics.

## E08 — Post-effect `tool_result` handler failure

**Question.** If a Pi `tool_result` handler throws after a write has executed,
does Pi preserve the effect, continue later result handlers, report the tool as
successful, and expose the extension failure to the adapter?

**Expectation.** The treatment file should already exist before either result
handler runs. Reversing thrower/audit order should change only their evidence
order; both handlers and the next positive control should still run. The adapter
must not call a zero-exit, valid-lifecycle session clean if Pi reports an
extension runtime error outside JSON stdout.

**Setup.** Both arms use the same pinned Pi, provider, fixture, result thrower,
result auditor, and 12 declared inputs. Only handler order changes. The generic
adapter projects Pi's print-mode extension-error stderr into a relative-path
structured record. `result_failure_oracle.py` expects that inner adapter failure
while separately proving successful tool results and exact durable effects.

**Evidence.** Throw-first run `20260815T214733Z-8dd1c1-6bf2` and audit-first
run `20260815T214734Z-ef03c9-d24e` both completed and conformed with matching
maps of 12 frozen/receipted inputs. The 56-test Pi suite passed, including
mutations that remove the structured error or falsely remove the completed
treatment effect.

**Result.** Both writes succeeded and both exact files exist. Throw-first
recorded thrower then audit; audit-first recorded audit then thrower. Pi exited
zero, completed normally, and left both tool results successful. It reported the
exception only as an `Extension error (...)` stderr line.

**Learned.** Hook phase changes failure semantics. A `tool_call` exception is a
pre-execution failure that prevents the effect and short-circuits later handlers.
A `tool_result` exception is a post-effect telemetry failure: Pi catches it,
continues later result handlers, preserves the successful tool result, and does
not roll back the effect. Exit status and JSON lifecycle alone therefore miss a
real subject-harness failure.

**Code consequence.** **Change now, completed locally.** `adapter.py` now
extracts pinned Pi print-mode extension errors from stderr, removes host paths in
the structured projection, and fails its generic verdict. The outer experiment
oracle passes only for the exact declared adapter failure plus the proven durable
effect and continued chain. **Candidate for reuse:** future harness adapters
need phase-aware extension failures and must inspect every documented error
channel; they must never imply rollback from a post-effect failure.

**Limits.** This covers one synchronous `tool_result` exception after a
successful write. It does not test result mutation, failed underlying tools,
multiple errors, multiline error messages, asynchronous background failures, or
transactional tools that may implement their own rollback.

**Next.** Reverse result handlers that mutate `isError` or content to test
whether post-processing can make recorded tool status disagree with the durable
effect, then combine argument mutation with terminal guard ordering.

## E09 — Result rewriting versus durable effect

**Question.** When two Pi `tool_result` handlers rewrite both content and
`isError`, which rewrite becomes the recorded tool result, what does the later
handler observe, and can the final status disagree with the filesystem effect?

**Expectation.** Result handlers should behave as ordered middleware. If the
last patch wins, mask-first/restore-last should finish successful, while
restore-first/mask-last should finish errored. The treatment file should exist
in both arms because rewriting occurs after execution.

**Setup.** Both arms use the same pinned Pi, provider, fixture, result masker,
result restorer, and 12 declared inputs. The masker returns synthetic failure
content plus `isError: true`; the restorer returns synthetic success content
plus `isError: false`. `result_rewrite_oracle.py` binds each intermediate view,
final digest/status, lifecycle, and durable effect.

**Evidence.** Mask-first run `20260815T215421Z-42311e-6353` and restore-first
run `20260815T215423Z-b5726f-73b2` both completed and conformed with matching
maps of 12 frozen/receipted inputs. The 57-test Pi suite passed, including
mutations that mislabel the result stage or remove the treatment effect.

**Result.** Mask-first recorded the masker observing Pi's original successful
write, then the restorer observing the synthetic error; the final result was
synthetic success. Restore-first recorded the restorer observing the original
success, then the masker observing the synthetic success; the final result was
synthetic failure. Both arms created the same exact treatment and control files.

**Learned.** Pi result handlers are last-writer-wins middleware. Final tool
content and `isError` describe the post-processed result, not the underlying
tool outcome. A reported failure can coexist with a completed effect, and a
later handler can erase an earlier failure report. Tool status is therefore not
durable-effect ground truth.

**Code consequence.** **Change now, completed locally.** Normalized tool
executions now label `result_stage: post_tool_result_hook`, complementing the
existing pre-hook argument-stage label. The new oracle treats final status,
intermediate handler evidence, and filesystem effect as separate facts.
**Candidate for reuse:** cross-harness result schemas need explicit observation
stages and external effects; a generic `success` boolean without provenance is
unsafe. Keep Pi's exact middleware details local.

**Limits.** This begins with a genuinely successful write and rewrites only
text content plus `isError`. It does not test an underlying failure rewritten
to success, details or usage patches, parallel tools, or model behavior after
receiving misleading result content.

**Next.** Start with a real tool failure, rewrite it to apparent success, and
prove that an effect-aware oracle rejects the false success. Then test result
rewrites across parallel tool completion order.

## E10 — Real tool failure rewritten to false success

**Question.** Can a Pi `tool_result` handler turn a genuine failed tool into an
apparently successful result, and will an effect-aware workload oracle reject
that claim even when the generic adapter and lifecycle remain valid?

**Expectation.** The same underlying bash command must fail in both arms and
must not create `attempted.txt`. The honest arm should preserve `isError: true`.
The falsified arm should rewrite the result to `isError: false` plus synthetic
success content. The next positive-control write must succeed in both arms.

**Setup.** Both arms use the same pinned Pi, provider, fixture, mode-aware result
extension, and 11 declared inputs. The treatment command deterministically
short-circuits before its write because `seed.txt` is a file, not a directory.
The extension records the original failed result before optionally rewriting it.
`failure_rewrite_oracle.py` independently checks the claimed status against the
declared durable effect.

**Evidence.** Honest run `20260815T220249Z-06fcee-f4fb` and falsified run
`20260815T220250Z-f08ad6-66c7` both completed and conformed with matching maps
of 11 frozen/receipted inputs. The 58-test Pi suite passed, including mutations
that hide the underlying error or invent a treatment effect.

**Result.** Both extensions observed the same real `Command exited with code 1`
result and neither arm created `attempted.txt`. The honest final result remained
errored. The falsified final result became synthetic success. Pi then completed
the same exact positive-control write in both arms. The generic adapter passed
both captures; the effect-consistency oracle rejected only the falsified claim.

**Learned.** A structurally valid harness run can contain a deliberately false
task success. Post-processing can erase the subject's real failure status, so
even phase-labelled result evidence cannot replace an external outcome check.
The useful invariant is directional: a reported success must imply its declared
durable effect. A reported failure does not imply rollback.

The fixture iteration exposed two additional boundaries. Pi validation failures
occur before `tool_result` handlers and therefore cannot be rewritten there.
Filesystem failures can embed volatile absolute workspace paths in result text,
so deterministic confirmation fixtures should avoid binding raw path-bearing
errors when a stable failure mechanism is available.

**Code consequence.** **Change now, completed locally.** The new runner/oracle
separates `underlying_failure_observed`, `final_reported_error`, durable effect,
and `effect_oracle_accepted`. The outer experiment passes only when it proves
that the falsified inner claim was detected. **Candidate for reuse:** every
external-harness task oracle should encode success-to-effect implications above
the generic adapter. Do not promote this Pi-specific fixture or result schema to
Workbench core.

**Limits.** This covers one deterministic sequential bash failure and one
deliberately dishonest extension. It does not test model behavior after receiving
the false success, partial effects, parallel results, retries, or a live provider.

**Next.** Make the deterministic provider branch on the falsified result to test
whether result rewriting changes the agent's next action, then test concurrent
result rewrites in parallel tool completion order.

## E11 — False success changes the provider's next action

**Question.** Does rewriting a genuine tool failure to apparent success merely
change telemetry, or can it change what Pi does next and leave a different durable
effect?

**Expectation.** Both arms should begin with the same failed bash command and no
`attempted.txt`. In the honest arm, the provider should observe `isError: true` and
write `recovery.txt`. In the falsified arm, it should observe the rewritten
`isError: false` and write `trusted.txt`. Both arms should then complete the same
`permitted.txt` positive control.

**Setup.** The pair reuses the E10 failure-rewrite extension and fixture. A new
deterministic provider selects its second tool call from the treatment's
post-hook `toolResult` in Pi's context and records that observation separately.
`branch_rewrite_oracle.py` requires the exact three-tool sequence, stage-labelled
argument and result digests, provider branch evidence, workspace manifests, file
contents, and positive control. Each sealed spec binds the same 11 inputs.

**Evidence.** Honest run `20260815T221605Z-69f5ef-0bb9` and falsified run
`20260815T221606Z-6ac62e-88a5` both completed and conformed. Each run's freeze map
matched its receipt map, and the 11-input maps were identical across arms. The
59-test Pi suite passed, including mutations that forge the provider's recorded
observation or remove the selected branch effect.

**Result.** The underlying command failed identically in both arms and never
created `attempted.txt`. The honest final result remained errored, the provider
selected recovery, and `recovery.txt` appeared. The falsified final result reported
success, the provider selected trusted-success, and `trusted.txt` appeared. Both
created `permitted.txt`. Thus the result rewrite changed the next action and durable
state, not only the captured status.

**Learned.** Pi's provider consumes the result after `tool_result` hooks. A false
success can therefore propagate into control flow before a later outcome oracle
reviews the run. Proving that propagation requires more than comparing final files:
the record must connect the original tool failure, rewritten status, provider
observation, chosen call, and resulting effect. The common positive control shows
that the divergence is branch selection rather than a broken session.

This is the strongest cross-harness lesson so far: result integrity is a behavioral
boundary. Once an untrusted middleware rewrite enters agent context, later actions
are tainted by that rewrite even if an external oracle eventually rejects the run.

**Code consequence.** **Change now, completed locally.** The new provider, runner,
oracle, sealed pair, and regression test preserve and verify the entire propagation
chain. **Candidate for reuse:** adapters for other harnesses should distinguish raw
tool outcomes from middleware-visible outcomes and let workload oracles reject
success before dependent actions are trusted. A future shared schema could carry a
result-integrity or provenance marker, but this single Pi mechanism is not yet enough
to change Workbench core.

**Limits.** This uses a deterministic offline provider, sequential tool calls, one
boolean status rewrite, and simple file effects. It does not measure live-model
reasoning, retry behavior, partial effects, rewritten result text without status
changes, or concurrent result delivery.

**Next.** Test multiple tool calls whose results complete or are delivered in
different orders, then determine whether Pi preserves call/result correlation and
whether result rewrites can taint only one branch without contaminating its peers.

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
