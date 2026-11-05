# Bring your harness, model and task: implementation plan

Agreed direction, 2026-10-30: builders should use Harness Workbench with Pi,
OpenCode or Hermes and a model/provider they choose, without editing adapter
source or writing a provider extension for routine use. Model access and tool
calling belong to the harness/provider; Workbench records execution and checks
outcomes. A compatible model needs a supported connection protocol, working native tool
calling, enough actual context and valid model/account access. Compatibility
does not imply task performance or universal model support.


Current priority (2026-11-03): **local inference only**. The next work is the
[local native-control roadmap](#local-native-control-roadmap): declared native
controls, effect measurements and integration with Workbench campaigns. Hosted
accounts and legacy five-harness recertification are deferred; their gates are
not relabeled as passing.

## Delivery order

1. Define an explicit provider/model configuration contract with harness-specific
   options. Keep scripted controls available and existing configs compatible.
2. Start with Pi: replace the custom-extension requirement for ordinary model
   selection with its native model/provider interfaces. Support an explicit
   OpenAI-compatible endpoint and credential reference, then native providers.
3. Extend OpenCode and Hermes beyond unauthenticated endpoint mode. Use native
   provider/auth interfaces; do not build a competing inference client.
4. Make own-task setup usable: task, working files, timeout and independent check
   commands should be configurable without editing adapter source.
5. Validate local, authenticated compatible and native provider paths. Test
   missing credentials, auth failure, protocol/tool errors, context limits,
   timeouts, cleanup, and failed independent checks. Record untested paths as such.
6. Write runnable walkthroughs for measured paths, with scripted providers as
   optional diagnostics. Retain failed attempts alongside successful evidence.

## Configuration and credential requirements

- Select harness, provider, model, task/working files, timeout and independent
  checks explicitly; preserve harness-specific options.
- Keep runtime identity/provenance checks and fresh per-run configuration.
- Reference credentials by an explicitly selected environment variable or
  supported credential source. Do not embed secrets in task/config/argv or copy
  unrelated account files. Keep secret values out of retained configuration and
  diagnostic evidence; verify this with a synthetic credential in live tests.
- Record selected provider/model, endpoint, authentication method/reference and
  runtime identity without credential values. A caller-authored identity is not
  proof of the serving model's actual weights.
- Do not silently inherit accounts or reconfigure/download models. Fail clearly
  on unsupported configuration and missing credentials.
- Keep capture validity, independent task success and sealed-record conformance
  separate. A passing scripted repair is not a general model benchmark.

## Acceptance

A builder installs Workbench, chooses a supported harness and model/provider,
runs their own task with independent checks, and gets inspectable evidence
without writing a provider extension or changing adapter code. Each claimed
connection type needs actual integration evidence. Local Qwen 27B is available
for validation; genuine hosted-account validation requires actual access.

## Working constraints

The documentation and implementation work remain local and uncommitted until
the user asks otherwise. Do not push. Research experiments remain local and
separate from supported adapter source. No merge, public release or website
publication is authorized by this plan.

## Progress

- [x] Record direction, requirements and acceptance criteria.
- [x] Pi native model/provider configuration and named API-key references (local implementation).
- [x] OpenCode authenticated compatible endpoints and selected native SDK routes (local implementation).
- [x] Hermes authenticated compatible endpoints and selected native provider routes (local implementation).
- [x] Independent argv checks in all three adapters, with separate outcomes and nonzero failure exits.
- [x] Local provider-path validation and user walkthroughs; hosted account validation remains separate.

## Local evidence and remaining validation · 2026-10-31

The implementation provides Pi `configure`, explicit provider/model and named
API-key references, OpenCode/Hermes compatible and selected native routes, and
independent argv checks in all three adapters. Routine provider/task selection
does not require a provider extension or adapter-source changes. The
[shared builder guide](builder-adapters.md) owns the introduction, compatibility
contract, custom-task walkthrough, result interpretation and troubleshooting;
the linked adapter guides own runtime setup and harness-specific configuration.

The supplied local `adapter-assurance/README.md`, `model-results.json`, retained
run evidence and logs support these observations:

- All three adapters passed the local Qwen 27B repair at a 65,536-token context.
  Strict success required failing baseline tests, native test exits `[1, 0]`,
  only `slugger.py` changed in the retained fixture, passing independent final
  tests, unchanged inputs and successful process/gateway cleanup.
- The complete suite passed **458 tests with no skips**. The installed suite
  passed six tests including all nine native API routes. Those nine routes are
  Pi's four compatible APIs, OpenCode's OpenAI/Anthropic/Google native SDKs and
  Hermes' OpenAI/Anthropic native routes. OpenRouter and compatible connections
  also have separate coverage. Actual harness clients and native terminal tools
  contacted authenticated local protocol fixtures with synthetic credentials.
- Wheel and normalized source packaging, checksums/content/modes, clean installs,
  offline subjects, sealed-record checks and source/expanded-artifact secret
  scans passed. All 155 installed runtime assets matched source. These packages
  are local working-tree snapshots, not released artifacts.

The full suite ran before the final Hermes workspace-query clarification. That
changed path then passed source scripted/negative-check and final installed
provider/protocol regressions. Documentation updates followed the validation.
The 458-test result is recorded evidence, not a rerun of this documentation edit.

The Hermes native Anthropic fixture exposed a missing optional SDK. The owned
installer/pin now requires the frozen upstream `anthropic` extra / SDK 0.87.0;
doctor rejects the earlier core-only runtime. No global runtime was changed.
Lazy installs remain disabled.

Earlier failed Hermes model attempts remain retained. One edited the input copy
outside the retained fixture and reported passing native tests. Unchanged
retained files, input hashes and failed independent checks exposed the failure;
Workbench rejected it. Explicit `--in` plus retained-workspace query guidance
corrected targeting, and the final run passed (449.29 seconds). Guidance and
process cleanup do not provide an OS sandbox or filesystem containment.

Selected credentials are supplied by environment reference, excluded from argv
and persisted configuration and redacted in captured output. Independent checks
receive a separate environment without provider keys. Tools still run with the
user's OS permissions; redaction cannot stop arbitrary file writes or network
exfiltration. Review retained data before sharing.

## Follow-up evidence · 2026-10-31

A separate local `validation-gaps/` evidence bundle records commands, input/source
hashes, failures and results for the reviewed working tree. It supplements the
historical assurance bundle; it does not replace its failed attempts or imply
that old artifacts contain later review fixes.

- The current default source suite ran 464 tests: 451 passed and 13 optional
  runtime tests skipped. This is distinct from the earlier 458/no-skip run.
- All nine native API routes passed again from current source and from a fresh
  installed wheel, using authenticated local protocol fixtures and actual native
  terminal tools. Each suite has three test methods covering nine routes.
- A forced Hermes CLI timeout passed the owned-gateway cleanup regression with
  the required Anthropic runtime profile. An older core-only runtime was rejected
  before execution; those failed attempts are retained. Initial socket-denied
  attempts are retained separately from permitted local-socket retries.
- Clean wheel and source-distribution installations passed offline metadata,
  CLI, offline-subject and sealed hello-record checks using the retained pinned
  build toolchain. A final docs-inclusive wheel passed fresh init/help and
  source/installed asset parity. This does not reproduce external runtime
  downloads, uncached build-isolation dependency resolution or a release gate.
- Guide/packaged-README differences were corrected. The guides state venv/build
  prerequisites; copied READMEs identify checkout-only installation commands.

### Model-backed documented custom task

The exact `total(values)` walkthrough was executed against the existing local
Qwen endpoint, using current-source adapters and fresh owned workspaces. Every
independent baseline exited 1 with two failures. Pi and OpenCode passed capture,
declared checks and separate final tests; only retained `total.py` changed,
test bytes and every original declared input hash stayed unchanged, and cleanup
passed. Pi's native test exits were `[1, 0]`. OpenCode's observations were
`[1, null, 0]`: a mistyped working directory caused one denied call before the
model recovered; `null` is not an invented successful exit.

Hermes' matching frozen profile reached the fixed 600-second deadline while
waiting for its third local API response. It successfully searched/read the
correct retained fixture but made no edit or native test call. Capture failed
for timeout/incomplete stream; declared and separately rerun final tests failed;
original inputs/tests remained unchanged. Process-group cleanup and forced stop
of the matching owned gateway both passed. The older core-only profile's setup
rejection and an offline inspection-script error/correction are also retained.

That initial phase established model-backed observations but left successful
custom-task validation for Hermes open. Its previous strict slugger pass remains
separate historical evidence. The subsequent local work below adds a successful
new attempt without replacing the failed sample. None of these direct task runs
created an outer sealed record; three-case checks do not prove arbitrary-workload
correctness.

## Subsequent local gap work · 2026-10-31

The separate `remaining-gap-work/` evidence bundle preserves new commands,
snapshots, scans and failures; the earlier assurance and validation bundles stay
unchanged.

- Hermes passed the exact documented custom task in 348.53 seconds in a NEW
  attempt with a 1200-second allowance. Native tests exited `[1, 0]`, only
  `total.py` changed, original inputs/tests remained unchanged, separate final
  tests passed and process/owned-gateway cleanup passed. Task/config/test bytes
  matched the failed 600-second attempt. Different tool choices and faster
  responses mean the larger allowance cannot be credited as the cause of success;
  reliable completion within 600 seconds is still unestablished. Prior measured
  API waits were hundreds of seconds while native tools were subsecond. No
  context/output cap exhaustion or adapter defect was established; no model
  service was reconfigured.
- Fresh Linux runtimes now have installation evidence: Pi's exact locked npm
  install from a copied package-only offline cache; unchanged OpenCode official
  binary download/verification; unchanged Hermes official source/frozen
  Anthropic-profile install with owned Python/cache. Doctors and actual native
  scripted repairs passed for all three, with retained files/input hashes and
  cleanup inspected. Node was already available, not downloaded anew.
- Uncached source build dependency resolution succeeded with setuptools 84.2.1;
  that wheel was clean-installed and all three adapters passed help/init/doctor
  and actual native scripted repairs. Its initialized adapter bytes matched
  source. This validates builder execution, not release-artifact mode conformity:
  the newer backend produced non-executable subject scripts. Release preflight
  used pinned setuptools 83.0.0; the newer clean Mac failure below leaves
  release-artifact mode conformance unresolved even with that pin.
- Additional local Python 3.11.16, 3.12.14 and 3.13.15 cells each passed 464 source
  tests (13 optional skips) and 251 offline-subject tests (no skips). The focused
  six regression tests passed after the fixture correction on Python 3.11–3.14.
  These are local Linux observations, not hosted CI or macOS evidence.
- Draft package preflight passed pinned builds, normalized source archive,
  strict metadata/artifact/checksum checks, current-tree and expanded-artifact
  Gitleaks scans, and a separate history scan over 140 commits. Initial copied
  file-mode and synthetic-fixture scan failures remain retained. The synthetic
  truncation credential is now generated at test runtime, with an exact
  fail-closed-marker assertion; no allowlist was broadened. Neutral-mode staging
  is not pristine committed release provenance; version remains the already
  published 0.1.0, so these artifacts are development snapshots only.
- Seven offline native Hermes refresh-helper tests and three Pi storage
  scenarios passed using synthetic credentials, fresh homes and blocked/mocked
  networking. They establish limited upstream behavior, not Workbench OAuth
  integration, login/refresh orchestration, hosted entitlement or account access.

## Remaining validation and documentation gaps

### Hosted API-key accounts

No selected provider API-key reference was available in the investigation's
process environment, and no potentially paid inference was authorized or
attempted. Existing account-file metadata is not authorization to read/import
credentials. To close one hosted route, select its supported provider, endpoint
and model, supply a dedicated named environment reference privately, and
authorize a bounded potentially paid native-tool task. Record account access,
protocol/native tools, independent checks and task outcome separately. Local
fixtures establish none of the hosted-account or subscription entitlements.

### Subscription, OAuth and saved profiles

These are intentionally excluded by the current adapter contract, rather than
merely an untested authentication option. Pinned upstream Pi, OpenCode and Hermes
contain account/OAuth mechanisms; Workbench's fresh homes, safe/offline settings
and explicit API-key selection do not expose their saved accounts. No login,
account import or authentication-support expansion was performed.

Support would require a separate explicit per-harness authentication-source
contract, one selected provider/account, native token loading/refresh, deliberate
refresh write-back and concurrency ownership, and tests for expiry, denial,
rotation, refreshed-secret redaction, timeouts and cleanup. Hermes' single-use
refresh grants make copying live account stores unsafe. Subscription entitlement
must be checked per route; a subscription is not proof of generic API-key access.
These remain implementation requirements. Offline helper/storage research above
does not establish a supported or tested adapter integration.

### Current-candidate macOS and hosted CI

Following local wrap-up, authorized macOS execution used a new owned temporary
workspace on macOS 26.5 arm64, with an integrity-checked 355-file candidate
snapshot. Fresh pinned Pi 1.4.0 / checksum-verified Node 26.13.0, OpenCode 1.19.5
and Hermes 0.22.5 / frozen Anthropic 0.87.0 installations passed their doctors.
Python 3.11.15 and 3.14.7 source suites each ran 464 tests: 463 passed and one
historical core-only Hermes runtime test skipped because that old environment
was absent. The current full Hermes profile was exercised. A clean wheel built
with setuptools 83.0.0 passed all nine native authenticated local protocol routes
and all three scripted reference repairs from outside the source tree. Original
source hashes remained unchanged. Each Python cell also passed 251 offline-subject
tests without skips. Three installed negative cases each retained capture validity,
recorded actual independent-check exit 7 and returned CLI exit 1. An installed
Hermes run forced past a 45-second deadline returned CLI exit 1, invalid capture
and successful owned-process/gateway cleanup. This is local macOS arm64 fixture evidence,
not hosted-account access, model task outcomes by themselves, x86_64 support
or current hosted CI. Commands, logs, hashes and the initial npm configuration
setup failure are retained in the local `macos-validation/` evidence bundle.
The setup failure used the same npm config file twice; a new retained attempt
used separate empty files. No adapter correction was needed for that failure.

Read-only GitHub retrieval confirmed successful Pi, OpenCode, Hermes and general
CI runs at base `c83e25dd1504a6c50f9bc720a3dd6de559c43ee9`. In particular,
[Hermes base run 41127308998](https://github.com/explorefailure/harness-workbench/actions/runs/41127308998)
contains successful Linux/macOS Python 3.11/3.14 jobs. Later duplicate base runs
were cancelled. These results do not validate the current uncommitted changes.
Exact-candidate hosted execution needs a reviewed commit and separately
authorized push/CI execution; this investigation forbids those actions. A
configured matrix or older successful job is not a current-platform test.

### Further installation and cancellation observations

The documented custom task subsequently passed one installed ARM64 Mac client
attempt per adapter using existing Linux Qwen 27B inference through an owned
loopback-only SSH tunnel. All attempts used a 1200-second bound: Pi 463.60s with
native test exits `[1,0,0]`, OpenCode 752.71s with `[1,0]`, Hermes 1102.12s with
`[1,0]`. Every external baseline exited 1, final independent checks passed, only
`total.py` changed and original inputs/tests stayed unchanged. Each retained
implementation passed 100 additional deterministic integer-list cases. Hermes'
recorded owned gateway was independently confirmed stopped. Frozen source hashes
and global Mac runtime versions remained unchanged; the owned tunnel was closed.

Pi's exact `[1,0]` comparison flag remains false because of the extra passing
test; independently verified repair success is reported separately. OpenCode's
initial result reader failed on the wrong exit field after CLI success; the
correct independent audit used native `metadata.exit`, preserved the original
inspection failure and performed no model rerun. These are single task samples,
not reliable deadline guarantees, Mac inference benchmarks, hosted accounts or
outer sealed model-task verification. OpenCode/Hermes exceeded 600 seconds, so
the custom-task example now explicitly uses 1200; historical bounds/failures
remain unchanged. Successful normal runs do not supersede the cancellation
failure below. All new command/result/audit evidence is in `further-validation/`.

Fresh ARM64 Mac normalized-source-archive installs passed all three adapter
help/init/doctor and scripted repair workflows. The explicit setuptools 83.0.0
backend was provisioned into an owned cache before offline/no-build-isolation
installation. Both raw and normalized archives and the installed package
preserved the two subject executables as 0755; 341 source/archive members and
51 adapter assets matched their declared sources. A separate standard isolated
pip source-archive install selected setuptools 84.2.1, also passed those adapter
workflows, but produced wheel/installed subject executables with mode 0644.
The unchanged release artifact verifier rejected that wheel. Those earlier results used pinned 83.0.0, but the newer clean Mac failure below
shows that pin alone is insufficient. Default-isolation checkout installation on Mac and
pristine committed release reproducibility were not measured by these archive
installs. The initial unavailable offline backend cache attempt is preserved.


Earlier clean Mac packaging check: the cleanup-fix candidate also failed release
artifact verification with pinned setuptools 83.0.0. Both `hook.py` and
`runner.py` were 0755 in the source but 0644 in `build/lib` and the wheel; wheel
metadata confirmed generator 83.0.0. The verifier correctly rejected the
non-executable artifact. Earlier pinned-backend passes remain historical
measurements, but pinning 83.0.0 is not sufficient evidence for this fresh clean
Mac build. A local Linux reproduction traced the failure to automatic namespace discovery
treating subject scripts as modules: module copying strips executable bits, and
the later data recopy depends on timestamp precision. With integral source
timestamps, setuptools 83 leaves the bits stripped. Disabling namespace discovery
in a scratch-only build restored both scripts and passed the artifact verifier.
The repository now applies that correction (`namespaces = false`), preserving the
explicit package-data declarations. A clean-wheel regression compares helper
bytes and executable modes under both integral and fractional source timestamps;
the old configuration fails its integral-timestamp negative control. New corrected
artifact/install evidence is retained separately under local
`outputs/packaging-blocker-fix/`. Historical failures are not erased, and adapter
success remains separate from artifact conformance.
Evidence is retained under local `outputs/opencode-cleanup-fix/native-validation/macos/`
(`artifact-mode-probe.txt` and `installed-driver-v2.log`).

An ordinary OpenCode native test survived the adapter deadline in three Mac
probes and one Linux probe. The harness detached that tool into its own
session/group; the recorded native-client process group correctly reported gone,
while the same owned test PID/start identity remained alive with parent PID 1 in
the retained fixture. Audits preserved the observations before stopping only
those independently matched processes. CLI failure and invalid capture correctly
reported timeout, but group cleanup must not be treated as complete native
child-tree cleanup. The subsequent fix opts the OpenCode native invocation into
bounded observation of owned descendant lineage/birth identity and cleanup of
those same recorded identities, including detached/reparented children. Linux
uses pidfds when available; Darwin's birth check/signal is not atomic. Polling
can miss between-sample fork/reparenting, so no complete child-tree containment
claim follows. Identity/discovery errors, budget exhaustion and survivors fail
cleanup/capture. An unavailable initial identity fails setup even after actual
exit 0; Darwin may hide an unreaped zombie from its identity API.

Linux source and clean installed-wheel native regressions passed blocked-tool
timeout and normal-exit/lingering-detached-tool cases. Mac source native
regressions passed both; Mac clean wheel and source-archive installed native regressions also passed.
These functional results do not establish release-artifact conformance. Independent
kernel audits checked child birth identities gone, an unrelated sentinel alive
and unchanged original inputs. The correction and earlier failures are retained
under local `outputs/opencode-cleanup-fix/`; release gates must validate the
exact final source and installed artifact, not only process-group flags.

Pi/Hermes native tool timeouts passed twice each; all three adapters' independent
check timeouts passed twice each with sleeping same-group children; Hermes also
passed a native-phase SIGINT case. These cases do not establish containment or
erase the earlier OpenCode failure. Historical evidence is under `further-validation/`;
older normal scripted repairs and gateway-cleanup successes remain valid within
their own measured scope.

### Versioned installation and release

Read-only metadata confirmed [v0.1.0 was already published on 2026-08-25](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0),
with a wheel, source archive and checksums; local tag target is
`773d2b10fa4500ff24e8fdf9ae1a911185ad8f2a`. Current local source still reports
0.1.0, but that release cannot contain these uncommitted adapter changes.
Do not reuse its number/tag/assets or advertise installing 0.1.0 as this work.

Before versioned instructions replace checkout installation: choose an unused
version, review and commit exact sources, update historical release procedure
variables/conformance records, run the pinned clean source/artifact gates and
fresh required platform/security checks, verify exact published/downloaded
artifact identity, and obtain separate release authorization. Current wheel and
source install checks are development evidence, not the complete release gate;
fresh native installs now have local Linux and macOS arm64 evidence; uncached
backend resolution has local Linux evidence as recorded above. Earlier scans remain historical; new draft scans
cover their explicitly hashed post-fixture-fix snapshot and history, not a future
versioned release commit or later narrative-only edits.

No commit, push, merge, hosted dispatch, release, publication or private Studio
update is part of this investigation.


## Local native-control roadmap

Planned 2026-11-03; not implemented or validated by this planning edit.

### Goal and scope

Let builders configure and measure supported native behavior in Pi, OpenCode and Hermes with local inference: tool policies, hooks/extensions, native limits, sessions and memory. Attach each experiment to the existing Workbench recording and campaign machinery, without editing adapter source for every feature under test.

Expose supported capabilities of each pinned harness explicitly. Do not promise every native control, a common implementation for different harness internals, or support for interactive controls in headless runs. Hosted accounts, subscription/OAuth work and legacy five-harness live recertification are deferred. Their gates stay honestly reported; they do not become the acceptance gate for local three-adapter use.

### Starting point verified in source

| Adapter | Existing control surface | Important gap |
| --- | --- | --- |
| Pi | Explicit local provider/model; declared extension inputs and some `pi_arguments` in legacy mode; private config/home; native JSON evidence | Explicit provider mode currently rejects nonempty `extensions`. Local model + user extension needs an intentional supported path, not removal of validation alone. |
| OpenCode | Generated per-run native config; selected local provider/model; native JSON evidence | Permissions are fixed to deny-by-default plus read/bash/edit; plugin/MCP lists are empty; LSP/formatter are disabled. These are not currently builder-selectable experiments. |
| Hermes | Local provider/model; explicit `--in`; streaming evidence and owned gateway cleanup | Current invocation fixes file/terminal tools, eight turns and automatic approval. Native hook/plugin/session/memory behavior needs separate capability discovery and evidence contracts. |

The existing outer Workbench can execute these commands. That does not imply automatic instrumentation of an internal tool decision, memory lookup or native retry. Outer retry restarts the adapter command; native retry is a separate measured control.

### Non-negotiable experiment contract

- Keep current configs/defaults compatible. New native controls are optional, validated and versioned. Unknown, incompatible or unsupported controls fail clearly before execution; no silent fallback or ignored settings.
- Maintain an adapter-owned boundary for provider/auth selection, evidence format, working directory, capture bounds and required cleanup. Do not expose arbitrary argv/config/environment passthrough that can override it. Use typed control fields and narrowly allowlisted harness-specific options.
- Declare every behavior-bearing file: native settings, instructions, hooks/extensions/plugins, dependency locks, memory seeds and session snapshots. Hash/copy the complete declared dependency set into fresh owned locations; reject missing/unbound files and path escapes. Do not mutate pinned runtimes or inherit ambient user profiles.
- Record requested configuration, rendered native configuration, observed activation and measured effect separately. An emitted argv or self-reported marker is not proof of effect. Unknown activation stays unknown.
- Keep capture validity, task correctness, control/experiment outcome and outer sealed-record conformance as independent verdicts. A denied edit can be the expected control outcome while task correctness fails.
- Retain baseline, enabled, inverted and failed attempts. Match task/model/config inputs except the intended experimental variable; record intentional differences. Never reset old frozen baselines to hide changes.
- Native policies, plugins, process cleanup and workspace instructions are not an OS sandbox. Extensions/plugins are trusted executable code. Preserve the existing independent-check environment and redaction boundaries.

### Milestone 0 — Pin the capability map

Inspect the exact installed/pinned source and headless invocation for each harness, using owned runtimes only. For every proposed control record native setting/flag or API, supported version, required files/dependencies, default behavior, evidence source, inversion method and limitations. Mark each item available/unavailable/unverified; no implementation claim from discovering a flag.

Start with tool allow/deny policy and hooks/extensions. Then inspect turn/context limits, instruction files, sessions/resume, native memory and native retry. Keep MCP, subagents, LSP, formatter and interactive approval as later candidates until their complete execution/dependency/cleanup contracts are understood. Support native differences rather than inventing fake cross-harness equivalence.

**Done when:** a small capability matrix identifies the first real control and effect oracle for all three harnesses, including explicit unsupported entries. No model request is needed.

### Milestone 1 — Control schema and Workbench evidence bridge

Define the optional configuration shape and compatibility rules before adding flags. Add per-harness validators/renderers and a machine-readable capability description. Config names/examples remain proposals until implemented; existing guides continue to describe only working commands.

Create a supported way to generate an outer spec for the user's actual config/task. Include controls and their complete input set automatically. Use a fresh adapter workspace, home, result path and optional session copy for every retry/sample; fixed output names must not collide or reuse the previous attempt's mutations. Separate adapter deadlines, independent-check deadlines and the outer timeout with cleanup headroom.

Inspect/reuse the existing record/feature/artifact contracts first. Bind the inner result, native evidence, independently checked retained-file manifest and necessary sidecars into the outer sealed record. Absolute references to unsealed external workspaces are insufficient. If the current store lacks this attachment mechanism, design a narrowly scoped core change with its own compatibility review; do not claim sealing stdout automatically seals external artifacts. Define missing/changed/oversized/unsafe attachment handling and redaction.

**Done when:** an account-free scripted custom task passes source and installed `run`/`show`/`verify` paths; artifact tampering is detected; task/check failure remains visible despite outer recorder completion. Current direct Qwen task passes remain separate until model-backed outer records are measured.

### Milestone 2 — First complete slice: Pi + local model + extension/control

Remove the current provider/extension incompatibility through explicit ownership rules: preserve the selected provider/model and recording mode, load only declared user extension files, and reject extension/config conflicts. Bind extension dependencies and record load failures.

Use one benign native hook/extension and a tiny tool-policy task. With a deterministic local protocol fixture driving the actual Pi client:

1. Baseline permits a designated edit; independent checks confirm the effect.
2. Enabled control denies that same attempted edit; native evidence confirms an actual attempted/denied call and the file remains unchanged.
3. Inverted/broken control allows the prohibited effect or fails activation; the experiment oracle must detect it.
4. Undeclared/changed extension inputs and conflicting provider/evidence flags fail setup. Native/tool/check errors and timeouts retain their real outcomes.

Put all variants through the Milestone1 outer spec and sealed verification. Then, only after deterministic controls pass, confirm the same supported path with the existing local Qwen endpoint and inspect both retained and original input hashes. Do not represent planned provider-fixture behavior as model performance.

**Done when:** the control is configured, activated, exercised, measured and sealed; the inverted case is detected; source and installed package behavior agree. This is the first usable deliverable, not a wait for all later features.

### Milestone 3 — OpenCode and Hermes control parity; Workbench campaigns

Implement the same experiment contract through each harness's real native mechanisms. OpenCode needs explicit selected permission/config/plugin handling rather than an unrestricted merge into its generated config. Hermes needs explicit tool/approval/turn controls and validated declared hook/plugin paths while preserving `--in`, streaming and owned gateway cleanup. Keep each adapter's current defaults when controls are absent.

Do not remove isolation or change pinned runtimes to enable a feature. Where an add-on requires dependencies, provision a new owned fingerprinted profile and include its lock/version identity. Disable lazy/global package installation and undeclared network services in local tests. Unsupported headless approval modes must fail explicitly.

Use the first slice to build this **measured compatibility matrix**, first with deterministic local fixtures:

| Workbench capability | Required demonstration |
| --- | --- |
| `run`, `show`, `verify`, `diff` | Correct inputs/config/evidence/verdicts retained; tampering rejected; baseline/enabled changes identifiable |
| `retry`, `sample`, timing | Correct number/order of native invocations; independent fresh state; all failed attempts retained; timeout layers reported separately |
| `steady`, `replay` | Deterministic fixture repeatability and declared inputs reconstructed; no assertion that a local LLM must reproduce identical output |
| `sweep`, `interfere`, `order` | Selected control variants/combinations actually reach native config and yield attributable observations |
| `efficacy`, `catch`, `effects` | Applicable controls have real feature seams/detectors and inversions; known feature or input damage is detected. Command completion alone is insufficient. |
| `interrupt` | Interrupt during native execution and independent checks; partial evidence retained; only verified owned processes/gateways cleaned |

A native control is not automatically a Workbench feature. Implement a declared feature/controller and observation seam where needed; leave inapplicable/unobservable campaign combinations explicitly unsupported. Preserve generic feature composition semantics, including native-vs-outer retry distinctions.

**Done when:** each adapter has baseline/on/inverted evidence for one tool control and one extension/hook path, plus the applicable campaign matrix. Missing native capability is an explicit scoped result, not a fake pass.

### Milestone 4 — Sessions and memory as explicit experimental state

Keep fresh-session behavior as the default. Add resume only from an explicitly declared, immutable session snapshot into a new owned copy. Record parent run, snapshot digest, runtime compatibility, model and relocation restrictions. Never resume a user's global account/session or mutate the parent evidence.

Define a memory task that requires retaining a synthetic fact between declared stages. Compare fresh/no-memory, seeded/enabled, resumed and reset conditions. Record actual retrieval/use evidence plus independent outcomes; instructions containing the answer are a different mechanism from native memory. Test stale, corrupted, missing and cross-workspace state. A replay must bind the required state or reject the request.

**Done when:** state provenance and reset semantics are visible; enabled/off/reset controls have distinguishable measured behavior; no cross-case state contamination. If native telemetry cannot establish retrieval, document that observational limit rather than claiming native memory was proved.

### Milestone 5 — Local qualification and existing-guide updates

For each completed capability, use this progression: schema/negative tests → actual pinned clients with deterministic local protocol fixtures → source and clean installed wheel/source-archive tests → bounded local-model confirmations → Linux and Mac coverage for changed platform-sensitive paths.

Model runs are sequential against the existing owned endpoint, with declared context, output, turn and wall-time bounds and no global model-service changes. Start with a small confirmation per adapter after fixture gates pass; choose additional A/B sample sizes from observed variability and compute cost, not an assumption of deterministic model behavior. Count harness attempts separately from provider requests; no provider-call/spending cap is inferred from a draw count. No model runs are part of this planning change.

Keep failures and hash-bound configurations/commands/results. Review retained workspace and original inputs independently, including instructions, extensions, session/memory files and test bytes. Recheck timeouts, descendant/gateway ownership, independent-check environments, truncated output and installed helper/dependency parity for each new surface.

Update the shared builder guide, existing adapter guides, packaged READMEs and this implementation plan together. Provide one working local custom-task walkthrough that composes declared controls with outer Workbench features. Report native settings supported, native settings unobservable and combinations untested separately. Leave the old hosted certification and full release status accurate and deferred.

**Final acceptance:** a builder installs the tested candidate, selects a local model, supplies a task/checks and a declared supported native feature, runs baseline/on/inverted experiments using the applicable Workbench campaigns, and can verify self-contained evidence explaining what was requested, activated and changed—without editing adapter source. This does not promise every native feature or an OS sandbox.

### Start order

1. Capability map and shared control/evidence contract.
2. Pi local-provider + extension vertical slice, including an outer sealed custom task and detected inversion.
3. OpenCode then Hermes equivalent control paths and campaign compatibility matrix.
4. Explicit sessions/memory, followed by broader native controls only where evidence and native support justify them.

Each milestone is separately reviewable. Implementation preserves the current dirty tree and earlier evidence; commit/push/release, hosted access, global runtime/service changes and certificate promotion are outside this plan.


### Milestone 0 outcome — pinned capability map

Step one completed 2026-11-03 as **local-only source inspection**, not native
control/effect integration testing. The retained `native-control-capabilities/`
bundle contains 27 entries (nine control families per harness), 54 precise
source excerpts from 29 files, file hashes, dependency/input requirements,
proposed effect oracles and inversion cases. Each entry distinguishes native
source presence, present adapter exposure and unexercised headless effects.

Identity checks matched Pi1.4.0's package/launcher/package-tree pin (1,279 owned
package files), OpenCode1.19.5's source commit and Linux x86_64 binary hash, and
Hermes0.22.5's source commit/lock. Both inspected Git source trees were clean.
Node's owned executable reported26.13.0. No models, account probes, runtime
installation or runtime-setting changes were involved.

| Native control family | Pi1.4.0 adapter | OpenCode1.19.5 adapter | Hermes0.22.5 adapter |
| --- | --- | --- | --- |
| Tool selection | Partial through argv; generated local config selects read/edit/bash | Fixed permission/tool policy | Fixed file/terminal toolsets |
| Tool denial | Native `tool_call` can block, but selected-provider mode rejects extensions | Native allow/deny/ask rules; current policy fixed | Native `pre_tool_call` hooks can block; safe-mode prevents custom config/plugin path |
| Hooks/extensions | Existing declared-extension path conflicts with explicit local-provider path | Native local plugins + tool before/after callbacks; adapter disables plugins | Native shell/Python hooks; safe-mode skips config customizations/plugin discovery |
| Instructions | Generated local config disables discovery; controlled opt-in needed | Project discovery disabled; native instructions not exposed | Safe-mode implies ignore-rules; task query guidance is separate |
| Sessions/resume | Native session/fork flags; generated config disables sessions | Native run continue/session/fork; owned per-run state currently fresh | Native resume can restore saved cwd; no supported snapshot interface yet |
| Persistent memory | Extension/session building blocks; standalone native memory selector not established | Instructions/session/plugin building blocks; standalone selector not established | Builtin memory store/provider layer exists; current safe-mode and toolset choices do not expose it |
| Reasoning/limits | Thinking flags and provider limits partly available; max-turns flag not established | Agent steps/model variant exist but not exposed | Native turn/run-budget/reasoning flags; current adapter fixes eight tool iterations |
| Native retry | Owned settings and auto-retry events exist; settings not exposed | Internal retry implementation/events; user-facing toggle not established | Retry implementation exists; general user-facing toggle not established |
| Compaction | Settings/events exist; not exposed | Native compaction configuration; not exposed | Implementation and auxiliary compression route exist; settings/routing still need verification |

The sole dynamic check called Pi's native instruction-loader function with
synthetic files in a disposable directory. It discovered both fixture and
ancestor `AGENTS.md` files. Generated local-provider configs already include
`--no-context-files`; this is a constraint on future instruction opt-in, not a
claim that current defaults leak ancestor instructions. No agent/model task ran.

Important design constraints found in source:

- Pi `--tools` alone does not remove all MCP tools. A tool-name policy is not
  filesystem containment, and an edit denied through one tool may still be
  possible through an allowed shell. Test a known attempted call and its effect.
- Pi's tool-call input can be mutated by extensions without native revalidation.
  Extension trust and effective input tracing belong in the new contract.
- OpenCode's headless run auto-rejects permission requests unless auto-approval
  is selected. Native interactive `ask` is not a supported headless prompt UI.
  Its tool-before callback signature is not itself a universal veto return;
  use native permission rules for the initial denial experiment.
- Hermes cannot gain declared hooks merely by adding `--accept-hooks` while
  keeping forced safe-mode. Design an owned, explicit customization profile;
  preserve current isolated defaults and provider/evidence/cwd/gateway ownership.
  Native malformed shell-hook entries may warn/skip; shell-hook failures can
  fail open unless `fail_closed` is applicable and selected for `pre_tool_call`.
- Hermes resume may restore an old cwd. A future resume interface must enforce
  the new retained fixture's ownership and preserve immutable parent state.
- Compaction/auxiliary routing needs explicit local identity and routing before
  model-backed tests. Selecting a local primary model is insufficient evidence
  about an auxiliary model. External memory providers, MCP/services, subagents,
  LSP/formatters and interactive approval remain deferred.

First experiments are now specified for all three harnesses:

1. **Pi:** local provider plus a declared benign `tool_call` extension. Baseline
   allows a planned edit; enabled handler blocks that same attempt; inverted
   handler permits it and the independent oracle detects the unwanted change.
2. **OpenCode:** expose a typed edit permission first, then a benign local tool
   counter plugin. Observe the actual permission/tool result and independent
   file/counter effects; do not infer activation from emitted config alone.
3. **Hermes:** expose a selected toolset first. Add hook control only after the
   owned customization path is validated; test block, fail-closed timeout/error,
   missing hook/matcher and unintended cwd restoration with explicit outcomes.

All three need separate requested/rendered/activated/effect evidence and a sealed
outer custom-task record. Those experiments have **not** run in this step.
Next is Milestone1: the typed control/configuration and evidence contract,
including attachment binding and fresh state per outer retry/sample. Then the
Pi vertical slice can implement the first complete experiment.


### Milestone 1 implementation — versioned contract and owned evidence bridge

Implemented 2026-11-04 for local-only qualification. The optional
`hwb-native-controls/v1` envelope is checked by all three adapters. The
machine-readable capability command lists no implemented non-default controls;
unknown/unsupported requests fail, and absent controls preserve defaults.
No flag discovery is promoted to an activation/effect pass.

`hwb-adapter spec` now generates an outer spec for an actual declared custom
config, a hash/mode-bound request and a worker snapshot. The narrow core change
is opt-in attempt context: a fresh owned attempt directory, bounded output and
observed descendant cleanup. Existing steps keep their execution path. The
worker stages fresh inputs, retains only selected bounded artifacts within the
outer run, independently compares the final retained manifest, and distinguishes
capture/check/control/sealed outcomes. The existing integrity inventory binds
those files without a second attachment store or unsealed external references.
Absolute paths inside native JSON are historical scratch provenance; retained
files use bundle-relative paths. Forced interruption may leave partial evidence.

Scope: unauthenticated HTTP loopback/scripted routes only; declared executable
code is trusted, with no OS containment claim. Source and clean-install checks,
account-free native-client observations and retained failures are recorded in
the `native-control-contract/` assurance bundle. Native OpenCode/Linux is the
first bridge qualification. Pi/Hermes bridge native runs, local-model outer
runs, macOS qualification of the new path, and the broader native-control
campaign matrix remain pending. Existing direct Qwen results remain valid for
their original measured scope. The next implementation is Milestone2's Pi
local-provider plus declared-extension baseline/on/inverted slice.


### Milestone 2 implementation — first Pi control slice

Implemented 2026-11-04 through a deliberately narrow
`controls.values.tool_call_extension` profile. The hook is a declared `.mjs`
function of a frozen native event/settings copy, not the complete Pi extension
SDK. It can allow/block the native call. Up to64 declared `.mjs`/`.json` inputs
are SHA256-bound and staged into an owned module tree; normal imports are
restricted to this tree and Node builtins. Full native extensions, package
profiles and TypeScript remain outside this supported surface.

The new profile requires the exact generated local invocation and an
unauthenticated loopback Chat Completions provider. Legacy configurations keep
their defaults and restrictions. Provider/model/auth and capture/session flags
cannot be overridden through the profile. Rendered controls and selected
provider configuration are checked for post-run drift, and assistant model/
provider identity is checked. These checks are not protection against malicious
trusted code, nor an OS sandbox.

The native Pi fixture gate covers baseline/on/inverted dependency behavior and
seven failure cases: syntax/load, undeclared import, async deadline, thrown error,
invalid result, corrupt stdout and synchronous hang. Native decisions are correlated to native
execution identities and independent retained-file hashes; task, experiment,
capture and sealed verification stay separate. Inverted success is detected,
while an expected blocked edit can pass the experiment and fail the task. All
variants use the outer sealed bridge. No broader campaign/efficacy-controller
qualification is inferred. The first failed attempt had an incorrect normalizer
projection lookup; retained native evidence allowed correcting it without
changing the frozen inputs or hiding that attempt.

Source/clean-install qualification, local Qwen confirmation, retained failures,
exact commands and the phase-only change audit are in the `pi-control-slice/`
assurance bundle. This is local-only work; no hosted account, release, global
runtime change or macOS qualification is claimed. OpenCode/Hermes control parity
and Workbench campaign mapping remain the next milestone.

Local Qwen27B confirmation completed through the sealed bridge for the tiny
single-edit task. Baseline had valid capture, allowed edit and passing checks/
experiment; enabled had valid capture, a denied native edit, failing task check
and passing experiment; the inverted dependency had valid capture and passing
task but a failed experiment. Original input hashes were unchanged in all three.
The preceding120s attempt edited correctly but timed out before Pi completed its
stream, so capture failed despite passing file/check observations. That failure
is retained. The bounded confirmation used240s native deadlines and a more
explicit single-edit/stop task with a `/no_think` instruction; this instruction
is not proof of a native reasoning toggle. No universal model-quality or OS
containment claim follows from these three runs.


### Milestone 3 local implementation · 2026-11-04

The typed OpenCode edit-permission/owned-counter and Hermes toolset/owned-patch-hook
profiles are implemented locally. These are narrow adapter-owned native paths;
arbitrary plugins, package profiles and full native-control parity remain unsupported.
OpenCode pure mode suppresses even explicitly configured local plugins, so the
counter uses an explicit owned profile with the other isolation/discovery/offline
restrictions preserved. Hermes safe mode suppresses custom hooks; its new owned
profile explicitly accepts only the generated hooks and preserves account-free
homes, ignored project rules, disabled plugins/MCP and pinned runtime identity.

Native Hermes blocked calls are absent from its CLI stream. The oracle uses
correlated pre/post hook identities and blocked/plugin_block status, plus retained
file effects and independent checks. Hook cwd is gateway process provenance,
not task workspace evidence. Missing/skipped hooks cannot pass by absence of edits.
The control-parity assurance bundle retains all setup, fixture and oracle failures.

The previous Pi candidate's exact wheel/source artifacts passed Mac ARM64 source,
clean-wheel and clean-source-archive qualification: each ran 15 bridge regressions
and the 10-case native Pi gate. This does not qualify newer adapter bytes automatically.
Campaign and clean-install results for the new controls are recorded separately;
no hosted CI, release, API/OAuth, general sessions/memory or OS containment claim follows.


## Adapter branch checkpoint · 2026-11-04

This development branch includes explicit provider/task/check configuration,
sealed local evidence and narrow native controls for Pi, OpenCode and Hermes.
It is not a new release or full native SDK/control parity. The latest source
suite passed 523 tests (505 passed, 18 optional skips). Earlier clean Linux
wheel/source-archive native gates cover all three control profiles; the final
Hermes expectation restriction is checked separately in source and clean installs.
`tool_selection.expect_terminal` must be true: filtered terminal absence is
unobservable in the Hermes CLI stream and is rejected rather than called a pass.

Mac qualification remains incomplete. The latest candidate passed its source
Python 3.11 suite, then failed under Python 3.14 before native gates; the detailed
failure remains remote. An earlier native Mac attempt failed closed on a Darwin
process identity lookup despite passing task/control effects. Proven-absence
handling now tolerates denied metadata only after a separate ESRCH result;
live/inaccessible PID identities still fail closed. The original intermittent
cause is not established. Earlier Pi Mac results do not qualify every later change.

One tiny Hermes local Qwen allow/baseline confirmation passed independent checks,
retained-file inspection, input integrity, cleanup and sealed verification.
OpenCode's new control confirmation timed out after a native read before any edit;
that capture/task/experiment failed and is retained. The older strict three-adapter
Qwen repair results remain separate evidence. Outer campaign tests include fresh
retry/sample state, tamper rejection and named interruption checkpoints; steady
reported UNSTABLE. Native feature/controller efficacy, sessions/memory, exact
candidate hosted CI and the existing legacy release-certificate failures remain
open. No hosted account access or release success is inferred.
