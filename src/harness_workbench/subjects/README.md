# Cross-harness adapter experiment

This experiment runs an exact file-write task and a red → edit → green repair
task through Claude Code, Codex CLI, DeepSeek Harness, Hermes Agent, and Pi,
then projects their different evidence surfaces into the candidate contract in
[`SHARED_ADAPTER_CONTRACT.md`](SHARED_ADAPTER_CONTRACT.md).

The common workload creates `shared.txt` containing exactly
`cross-harness control\n`. The adapter captures the subject lifecycle and raw
streams. A separate oracle compares before/after manifests and exact bytes.
Adapter success and outcome success are intentionally independent. The repair
oracle accepts different valid implementations, but requires only `slugger.py`
to change, an externally red/green suite, and subject evidence for the exact
unittest operation ordered around the write.

## Run

The identities in `pin.json` must match the installed clients. Claude and Codex
use their existing authenticated clients. DeepSeek Harness uses the official
`dsh` headless profile with temporary `HOME`, `DSH_HOME`, and XDG directories.
Which model runs is declared in `model_selection.json`, which is the model
authority; `pin.json`'s `ollama` block is only the optional local profile's
content pin and says so. The committed active profile is **`opencode-go`**.
DeepSeek Harness, Hermes, and Pi runs under that profile require a valid
`HWB_OPENCODE_KEY`, outbound network access to the declared remote gateway,
and permission to make a potentially paid or spend-bearing model call. The
adapter refuses to run when that key is absent; it does not substitute a
placeholder for a gateway credential. The gateway model is a label the
service promises to honour rather than a digest, and `identity_strength` in
the declaration records that weaker identity.

Hermes is pinned to official stable `0.20.5` / `v2026.8.19`, including the
annotated tag object, peeled source commit, dependency lock, and launcher. It
therefore makes a remote API call through the active gateway profile in the
configuration shipped here. It gets a temporary `HERMES_HOME`, and only its
workload toolsets are enabled. The Hermes source checkout defaults to
`~/.hermes/hermes-agent`; set `HERMES_AGENT_ROOT` if it is installed elsewhere
and put that checkout's matching environment ahead of any older `hermes` on
`PATH`. `local-ollama` is a separate, optional local profile: selecting it
routes the content-pinned `gpt-oss:20b` model through Ollama's loopback
OpenAI-compatible endpoint and uses a non-secret placeholder because that
endpoint does not authenticate. (`qwen3.5:9b` was the earlier local pin and is
no longer selected for any subject.) `dsh_patch.yml` retains workspace
confinement while making DeepSeek runs noninteractive.

`harness_workbench` must be importable: the adapters take bounded capture,
credential redaction, manifests and digests from `harness_workbench.capture`
rather than carrying a second copy of them. Installing the package (which is
how you got `hwb subjects`) is enough; from a source checkout, prefix each
command below with `PYTHONPATH=../../src`. Every adapter record names the
version and digests of the primitive that produced it, under `apparatus`,
because `freeze` binds the files beside the spec and cannot bind that one.

`hwb subjects --into` also writes `apparatus.json`, a baseline recording which
build of the primitive this copy of the tree was cut against, and every run
compares itself to it. A mismatch is an **adapter-verdict** fault: the subject
is not in question, but whether this run can be compared with the ones beside
it is. An unmaterialized tree has no baseline and says so rather than going
quiet. This is what catches the likely shape of the hazard — one machine, one
`pip install -U`, every subject upgraded together — which `compare.py` cannot
see, because in that case all five subjects agree with each other perfectly.
The manifest is deliberately not a spec `input`: `freeze` creates its baseline
lock on first run, so materializing into a fresh directory would write a new
manifest and a new lock together and report no drift at all.

`runner.py` uses the Workbench's own exit codes, one level down. **0 means
the subject was measured validly, whatever it did about the task** -- the
same rule as "a harness that worked exits 0, whatever the steps did". 1
means the measurement is not trustworthy; 2 means nothing could be run at
all; 3 is a refusal, for a run interrupted partway. Whether the subject did
the task is `outcome.passed` in the record and is deliberately not in the
status, so a wrap like `retry` cannot mistake a declined task for a broken
measurement and re-run it at full cost.

Pass `--record PATH` to any of these to keep the record. A run is not
reproducible after the fact -- the workspace is a temporary directory that
deletes itself, and the printed record is the only artefact it ever produces.
The first containment matrix was measured without it. Re-cut through the specs
at `sample.n = 3`, two of its ten arms reported a count never observed again
and four more reported a quantity that moves between samples. Prefer
`hwb run guard_<subject>_<variant>.json`, which retains a full store and a
`freeze` lock without being asked.

```sh
python3.11 -m unittest discover -p 'test*.py' -v # offline; no subject installed
python3.11 runner.py --subject claude
python3.11 runner.py --subject claude --workload guard --variant block \
    --record ../../../measure/guard/claude-block.json
python3.11 runner.py --subject codex
python3.11 runner.py --subject deepseek
python3.11 runner.py --subject hermes
python3.11 runner.py --subject claude --workload repair
python3.11 runner.py --subject codex --workload repair
python3.11 runner.py --subject deepseek --workload repair
python3.11 runner.py --subject hermes --workload repair
python3.11 runner.py --subject pi
python3.11 runner.py --subject pi --workload repair
python3.11 fault_runner.py
```

The declarative path has a separate finite offline gate. Its default is
plan-only and authorizes no network or paid provider calls:

```sh
python3.11 agent_task.py
python3.11 agent_task.py \
  --live-plan-destination /tmp/hwb-agent-task-live-new
python3.11 agent_task.py --run-offline \
  --offline-destination /tmp/hwb-agent-task-offline-new
python3.11 agent_task.py \
  --review-smoke-destination /tmp/hwb-agent-task-retained-smoke
```

The live-plan command performs no network operation and does not create its
destination. It displays the exact task/archive/apparatus/validator/comparator
and per-subject spec digests, real-route templates and provider pins, rolling
80 and weekly 90 stop thresholds, derived outer timeouts, stable store nonces,
and nominal/maximum call counts. With no freshly injected usage snapshot it
marks usage unreadable and remains ineligible. Real-route release is always
disabled and requires a separate one-attempt authorization artifact; campaign
failure never authorizes an automatic repeat. The authenticated call-control
service validates that artifact against the exact execution-plan, provider-route,
permit-time usage, campaign, phase, subject/model, store, request, and base-attempt
identities. Service startup independently recomputes the full plan, exact-five
route map, apparatus map, fresh-usage eligibility, key permissions, and fresh
destination. The supervisor atomically claims that resolved destination with
mode `0700` and creates the fixed bundle/session/records/process/review skeleton.
Immediately before each release it rejects aliases, unexpected topology,
non-directory or overfull phase stores, and re-hashes every bound apparatus
file, then writes an exclusive, fsynced consumption marker. Artifacts expire
within ten minutes, cover exactly one provider call,
and cannot be replayed after a process restart. Missing, malformed, mismatched,
expired, overbroad, or reused authorization hard-stops the campaign. The real
provider transport remains plan-only until a separately reviewed execution
entry point is implemented; this boundary alone cannot invoke a provider.

The execution coordinator is deliberately two-step. `prepare` allocates one
permit and returns its exact authorization binding; `execute` accepts only that
prepared identity and a matching artifact, revalidates the retained fake plan
and contained workspace, and launches once through the broker. It never requests
a retry. An operational failure leaves call control in `retry_pending` for a
new, separately authorized decision, while command-construction or uncertain
broker failures hard-stop. Coordinator conformance uses the fake transport;
the real transport still refuses command construction even after authorization.
The authorized episode wrapper retains independent precheck, agent, and
postcheck workspaces under `process/`, reconstructs the observed effects in the
postcheck workspace, and emits the same run envelope and verdict fields as the
offline runtime. Failure to resolve an authorization after permit allocation
hard-stops rather than abandoning an ambiguous inflight permit.
The write-smoke wrapper then replays that closed-world envelope through the
independent validator before retaining it, materializes exactly one ordinary
single-draw Workbench store under `records/write-smoke/`, runs bounded
`hwb verify`, and records the store-tree, `record.json`, and `integrity.json`
digests. Producer-only workspace paths and authorization receipts are not added
to the normative episode; authorization consumption remains in the durable
control-plane evidence. A validation, partial-store, topology, or freeze
failure latches later release. This is still a fake-transport implementation
slice. Its exact-five smoke orchestrator scans and digest-checks the task,
workspace archive, and fake plan before atomically retaining them with a bound
bundle manifest; consumes five separate one-call artifacts; verifies all five
stores; retains the independent comparison, permit-usage evidence, and
credential scan; and seals a durable journal/registry phase checkpoint while
both services remain live. It refuses every non-fake transport. The planned
spec documents are still explicitly virtual at this milestone: pre-call
ordinary `hwbspec`/freeze-lock assembly, repair-matrix stores, and a reviewed
real-provider execution entry point remain absent.
Before returning success, a separate offline smoke reviewer reopens all five
stores, reruns `hwb verify`, binds each sealed episode subject to its checkpoint
tree digest, rechecks the exact comparison, permit-usage and cleanup sets,
validates the durable journal/registry prefixes, and repeats the configured
credential scan. The retained `offline-review.json` records that result; store,
review-file, or prefix mutation is rejected without another provider permit.
The `--review-smoke-destination` command reruns that same review later and exits
nonzero on any disagreement. It performs no network operation and allocates no
permit.

The destination must not exist. The offline run builds one deterministic task
and workspace archive, executes it through five route-specific fake providers,
reconstructs each effect set in an independent postcheck workspace, emits five
ordinary Workbench stores using `[freeze, receipt, retry, sample, timing]`, runs
`hwb verify` on every store, replays the independent exact-five comparator,
scans retained evidence for configured credential values, validates a durable
phase checkpoint while call control and the broker are still live, and only
then records clean control-plane shutdown. Passing this gate authorizes no live
campaign; live generic acceptance remains separately usage-gated and explicit.
Call control and spawning run as separate authenticated services. The
supervisor witnesses either service's death, latches later release, and uses
the durable registry to perform bounded cleanup; any abnormal witness makes the
candidate ineligible even when cleanup succeeds.

## Adapter operations

Run the preflight before spending a subject call. It loads the active gateway
credential from an owner-only file, activates the configured Hermes checkout's
environment, and then runs the all-five doctor. It submits no prompts:

```sh
python3.11 preflight.py
python3.11 preflight.py --json
python3.11 preflight.py --subject claude
```

The optional local config defaults to
`~/.config/hwb/adapter-preflight.json`. It contains paths, never credentials:

```json
{
  "schema": "cross-harness-adapter-preflight-config/v0.1",
  "credential_file": "~/.config/hwb/opencode.key",
  "hermes_root": "~/.hermes/hermes-agent"
}
```

The credential must be a regular UTF-8 file owned by the current user, with no
group or world permissions, at most 16 KiB, and exactly one non-empty line.
`HWB_PREFLIGHT_CONFIG`, `--config`, `--credential-file`, and `--hermes-root`
provide explicit overrides. Existing `HWB_OPENCODE_KEY` and
`HERMES_AGENT_ROOT` values take precedence over config values.

For a routine live acceptance run, `smoke.py` composes that preparation with
the usage gate, retained one-draw execution, post-run usage reading, independent
receipt validation, exact credential-value absence scan, and offline
postflight. It is plan-only by default and reads neither the credential nor the
gateway usage endpoint:

```sh
python3.11 smoke.py --workload repair
```

After reviewing the five-call plan, one explicit flag and a brand-new record
directory authorize the campaign:

```sh
python3.11 smoke.py --workload repair --live \
  --record-dir ../../../measure/smoke/all-five-repair-YYYY-MM-DD
```

The default hard gates refuse when the gateway's rolling window reaches 80%
or its weekly window reaches 90%. `--max rolling=PCT` and
`--max weekly=PCT` may lower or explicitly change those lines; omitted defaults
remain in force. `--subject` is repeatable, `--draws` is bounded by the same
hard maximum of three as `recertify.py`, and repeated subjects are refused so a
mistyped plan cannot silently duplicate spend.

The directory must not already exist. `smoke-report.json`, `usage-before.json`,
and `usage-after.json` are created owner-only; subject records and the lower
level `recertification-report.json` are retained beside them. The smoke passes
only when every planned result is retained and digest-bound, each workspace
manifest proves the one allowed effect, process cleanup is clean, repair runs
prove red → edit → green, the prepared credential value is absent from every
retained JSON file, and the postflight remains `ready`. An unreadable usage
counter is a refusal, never permission to spend.

This is an operational smoke, not a replacement for a three-draw certification
matrix. Use it to prove that the currently prepared adapters can execute a
bounded workload now.

For the full matrix, `certify.py` is also plan-only by default. The plan fixes
the subject set to exactly Claude, Codex, DeepSeek, Hermes, and Pi; fixes the
workload to repair; revalidates `sample.n = 3` inside `retry.max = 2`; reports
18 nominal and 33 maximum calls (three provider-route canaries followed by the
15-call matrix, whose retry ceiling remains 30); resolves the interpreter, source
root, specs, run store, comparator, and child `PYTHONPATH` to absolute paths;
and authorizes zero calls:

```sh
python3.11 certify.py
```

The plan reports whether `gitleaks` is currently available without making that
external executable a requirement for zero-call planning. `--live` fails
before creating a record directory unless the executable is present, usable,
and digest-bound into the plan.

The gateway canary is also a standalone plan-only command:

```sh
python3.11 route_canary.py
python3.11 route_canary.py --live \
  --record-dir ../../../measure/canary/provider-routes-YYYY-MM-DD
```

It is fixed to DeepSeek, Hermes, and Pi because those are the adapters sharing
the configured OpenAI-compatible gateway route. For each subject, the real
repair adapter renders its tool-bearing request against a loopback server using
a fake credential. The exact JSON body is retained and replayed directly to the
declared gateway with the real credential only in the authorization header. The
connection closes after the first valid streaming JSON event, before any
harness receives a response or can execute a tool. Redirects, elapsed time,
request/response bytes, stream lines, output capture, and cleanup are bounded.
The network waits reuse the existing repair latency envelopes: 120 seconds for
DeepSeek and Pi, and Hermes's workload-specific 180 seconds.
This is operational route evidence, not an adapter or outcome verdict.

After reviewing that plan, the current usage readings, and the default hard
stops at rolling 80% and weekly 90%, one explicit flag and a directory that
does not yet exist authorize the retained recut:

```sh
python3.11 certify.py --live \
  --record-dir ../../../measure/certification/five-repair-YYYY-MM-DD
```

Offline environment preparation and the all-five doctor run before the usage
gate, and no provider or Workbench request starts unless all of them pass. The
retained three-route canary then takes its own fresh usage reading and stops on
the first HTTP refusal, malformed stream, network failure, or execution-bound
failure. Its post-canary usage reading is gated again, so reaching a stop line
during those three calls prevents the matrix too. No Workbench spec starts
unless all three routes return a valid first stream event. Each spec runs
unchanged, so `sample(retry(step))` retains every failed or retried attempt in
its sealed store. The supervisor bounds each whole spec by its subject repair
timeout times the six-attempt ceiling plus cleanup headroom. Every store that
appears, including one left by an operational failure, is checked with
`hwb verify`; a comparator invocation is allowed only after exactly five clean
stores exist and receives those five paths in the fixed subject order.

The fresh record directory retains the complete nested `route-canary/` store,
`runs/`, bounded command captures under `process/`, `usage-before.json`,
`usage-after.json`, `comparison.json`, a
redacted `gitleaks-report.json`, `credential-scan.json`, and
`certification-report.json`. Operational failures stop later model-bearing
specs but still trigger post-run usage, offline postflight, cleanup accounting,
and security scans. Credential checking is recursive over every retained
regular file and covers configured credential values plus their JSON
spellings; oversized or non-regular evidence fails closed.

The retained-evidence scan uses the pinned `.gitleaks.toml` shipped beside the
workflow, so the same plan and scan remain available in a materialized subject
tree rather than depending on a repository-relative configuration file.

`certification-candidate.json` binds the exact 13-input map, all five spec
digests, the capture/canon apparatus, comparator program and output, each run
tree plus `record.json` and `integrity.json`, and the other retained record
digests. It is eligible for review only when the comparator passes, every
subject is adapter/outcome 3/3 with zero timeouts, every sealed store verifies,
usage and postflight remain readable, both security scans pass, and the
total call count stays under 33, with canary and matrix calls reported
separately. The command records the before/after digest of
`adapter_certification.json` and never edits it; promotion is a separate human
review action.

Review a retained candidate with `review_candidate.py`. Like the live workflow,
it is plan-only by default, but the plan authorizes zero model calls, zero
network calls, and no promotion:

```sh
python3.11 review_candidate.py \
  --candidate ../../../measure/certification/five-repair-YYYY-MM-DD/certification-candidate.json
```

One explicit flag and a directory that does not yet exist authorize the
offline checks and retained review evidence:

```sh
python3.11 review_candidate.py \
  --candidate ../../../measure/certification/five-repair-YYYY-MM-DD/certification-candidate.json \
  --review \
  --review-dir ../../../measure/review/five-repair-YYYY-MM-DD
```

The reviewer independently reproduces the candidate schema and call bounds,
the exact input, spec, apparatus, workflow, comparator, run-store, and retained
record digests, the three-route canary and usage evidence, and complete
credential-scan coverage. It then runs five bounded `hwb verify` processes in
the fixed subject order, the exact-five comparator, and gitleaks. Every process
capture and cleanup receipt is retained. A changed, missing, oversized,
non-regular, or unbound file fails closed.

The fresh review directory receives `promotion-review.json`,
`comparison-replayed.json`, `gitleaks-replayed.json`, bounded captures under
`process/`, `adapter-certification.proposed.json`, and
`adapter-certification.patch`. The proposed document and patch are emitted only
after every check passes. They are review artifacts: the command never edits or
applies `adapter_certification.json`, and promotion still requires a separate
human review and pull request.

For a cold review after the checkout has advanced, materialize the exact source
tree and pre-promotion certification manifest bound by the candidate, then pass
their resolved locations with `--source-root` and `--target`. These options do
not relax digest validation; they let the reviewer inspect historical bytes
without depending on its current working directory.

The offline doctor proves local authentication metadata, not that every
provider route has quota at the instant of a prompt. The canary adds a narrow
live check for the three shared gateway routes and prevents a gateway 4xx from
spending the 15-call matrix. It cannot prove the independent Claude or Codex
service will accept its later request, and it cannot predict a route changing
after the check. Any such failure remains a retained negative: retry stays
bounded and the candidate becomes ineligible rather than being made green by a
weaker validator.

Run the lower-level doctor directly when the environment is already prepared:

```sh
python3.11 doctor.py
python3.11 doctor.py --json
python3.11 doctor.py --subject claude
```

The doctor submits no prompts. It checks the installed version and executable
digest, the local authentication source the adapter will use, one frozen native
lifecycle replay per normalizer, and `adapter_certification.json`. That
manifest binds the exact repair inputs plus the imported `capture` and `canon`
bytes to the reviewed live matrix. Its statuses are deliberately operational:

- `ready`: every offline check passes and these exact bytes have reviewed live
  evidence;
- `pin_drift`: an installed client, launcher, dependency lock, or Node runtime
  differs from `pin.json`;
- `schema_drift`: a frozen native lifecycle no longer normalizes to its
  certified digest;
- `auth_missing`: the adapter's local credential source is unavailable; and
- `live_verification_required`: offline checks pass, but an apparatus input has
  changed since the reviewed live evidence.

`recertify.py` is plan-only by default. This prints the exact bounded work and
authorizes zero model calls:

```sh
python3.11 recertify.py --subject claude
```

After reviewing the plan, one explicit flag authorizes one retained draw:

```sh
python3.11 recertify.py --subject claude --live \
  --record-dir ../../../measure/recertification/claude-2.1.246
```

The default is one repair draw and the hard maximum is three. Each subprocess
has the adapter's subject/workload timeout plus a 30-second supervisor margin.
Hermes repair allows 180 seconds for its multi-turn gateway path while its
write workload remains at 120 seconds. Stdout, stderr, and descendant cleanup
remain bounded by the shared capture primitive.
The record directory must not already contain a report, so a recut cannot
silently overwrite evidence. A failed draw stops the remaining plan, limiting
spend after the first non-green result.

Use a one-draw recertification for the subject whose pin or native surface
changed. If that draw changes normalized evidence or task outcome, recut the
five frozen repair specs at three draws and compare their sealed stores before
updating `adapter_certification.json`. A passing one-draw smoke result is not a
replacement for that cross-subject matrix. When its normalized evidence and
task outcome are unchanged, retain it as a recertification bridge: the manifest
keeps the original 3/3 subject row and separately binds the new version,
executable and pin digests, one-draw record/report digests, and the unchanged
semantic comparison digest. The doctor validates both parts before returning
`ready`.

Pi was excluded while this contract was *derived*, so the shared envelope
could not simply inherit the shape of the reference integration. That exclusion
has done its job and is over: Pi is a subject on the same terms as the other
four, binds the byte-identical input set, and is required in the comparison.
Leaving it out of `compare.py` after it had become a peer everywhere else meant
the tree produced a conforming Pi envelope that the comparator would not read.

Pi's own richer schema stays in `experiments/pi_coding_agent`, and that is not
Pi being held apart — it is a different artifact answering a different
question. What Pi can express there and cannot express here is a finding about
this contract, and merging the two would delete the ability to ask.

Run each as a frozen, receipted Workbench discovery record:

```sh
PYTHONPATH=../../src python3.11 -m harness_workbench run claude.json
PYTHONPATH=../../src python3.11 -m harness_workbench run codex.json
PYTHONPATH=../../src python3.11 -m harness_workbench run deepseek.json
PYTHONPATH=../../src python3.11 -m harness_workbench run hermes.json
PYTHONPATH=../../src python3.11 -m harness_workbench run pi.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_claude.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_codex.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_deepseek.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_hermes.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_pi.json
PYTHONPATH=../../src python3.11 -m harness_workbench run faults.json
```

Compare either five saved outer-envelope JSON files or five Workbench run
directories:

```sh
python3.11 compare.py \
  runs/<claude-id> runs/<codex-id> runs/<deepseek-id> \
  runs/<hermes-id> runs/<pi-id>
```

`contract_passed` means the records satisfy the shared evidence contract, on
**every** draw — the shape of the evidence is not what is allowed to vary
between draws. It does not require any harness to finish the task. Each
subject reports `draws` alongside `adapter_passed`, `outcome_passed` and
`timed_out` as counts over those draws, deliberately not as rates: a rate over
three draws reads as a probability and is not one, and reduction belongs to
whoever is asking the question.

## Workbench features on these subjects

The specs run `freeze`, `receipt`, `retry`, `sample` and `timing`. Attaching
the Workbench's own instruments to an external harness is the point: an adapter
that reimplements what a feature already does is a second implementation to
keep honest, and the tree has been down that road once already.

`sample` runs each subject three times. Its own reason applies here more than
anywhere it currently ships: *one draw is not a measurement*, and every subject
in this tree is a nondeterministic agent.

**Order is the experiment, not formatting.** The last-declared wrap ends up
outermost, so `retry` before `sample` composes as `sample(retry(step))`: each
draw retries on its own until it is a valid measurement, and `sample` then
collects three of them. Reversed, `retry(sample(step))` re-runs the *whole draw
set* when any single draw fails, discarding draws that were already good and
paying for them again. Measured on a step that fails once and then succeeds:

| declared order | composes as | attempts |
| --- | --- | --- |
| `[retry, sample]` | `sample(retry(step))` | **4** |
| `[sample, retry]` | `retry(sample(step))` | 6 |

`retry` fires on a non-zero exit, which under the exit codes above means *the
measurement was not trustworthy* — never that the subject declined the task.
That distinction is the whole reason the status was split; without it, `retry`
re-runs a harness that captured perfectly and simply said no.

**Cost is bounded by arithmetic, not by a meter.** Nothing in the Workbench
observes spend, so the only real bound is the two numbers that multiply:
`sample.n × retry.max` = 3 × 2 = **6 subject invocations per spec worst case,
3 when nothing needs retrying**. A test asserts that product stays ≤ 6 so
raising either number is a deliberate act. Wall-clock is bounded separately by
`SUBJECT_TIMEOUT_SECONDS` in `runner.py` and by `step_timeout_ms` in a spec.
With `model_selection.json` on a gateway profile this is real money; on
`local-ollama` it is free.

`faults.json` is deterministic and gets `timing` only — sampling a
deterministic step buys nothing and triples its runtime.

### Reading the gateway's own budget — `usage_probe.py`

The active gateway publishes its consumption at `<base_url>/usage`, undocumented
and present only on the `/go/` path. `usage_probe.py` reads it, gates on it, and
reports the delta across a run:

```sh
IFS= read -r HWB_OPENCODE_KEY < ~/.config/hwb/opencode.key
test -n "$HWB_OPENCODE_KEY"
export HWB_OPENCODE_KEY
python3.11 usage_probe.py --save before.json --max rolling=80 --max weekly=90
python3.11 runner.py --subject pi
python3.11 usage_probe.py --baseline before.json
```

It lives here and not in the Workbench because it knows one vendor's URL and
one vendor's JSON — exactly the knowledge core must not acquire, and exactly
what the project's own scope note puts in an external adapter. It is not a
spec `input`: it observes a run, it does not decide what the subject does.

**It reports percentages and deltas of percentages, never dollars.** The Go
plan prices the windows at $12/5h, $30/week and $60/month, so multiplying is
tempting — but a 2% weekly reading was observed against $0.21 of actual
reported spend, where the arithmetic says $0.60 and a $0.21-at-2% window would
have to be a $10.50 cap rather than the published $30. The percentages and the
console's dollars do not reconcile. A delta across one run needs no conversion
and is robust to whatever the absolute scale turns out to mean; a number that
looks like money and is not is worse than a percentage that admits what it is.

Exit codes follow the same convention as `runner.py`: `0` under every declared
line, `1` a window has reached its line, `2` nothing could be run, **`3` the
counters could not be read**. Three matters most — an unreadable counter is an
*unknown*, not a pass, and a budget check that fails open is only a delay
before the same overspend.

#### Measured cost, 2026-08-16, `opencode-go` / `kimi-k3`

Four write-workload invocations across the three gateway-billed subjects moved
the **rolling** window from 0% to 2%. Claude and Codex authenticate to their
own first-party services and cost nothing here.

| subject | wall clock | tool calls | rolling delta |
| --- | --- | --- | --- |
| pi | 16 s | 1 | 0 pts |
| deepseek | 14 s | 2 | +1 pt |
| hermes | 27 s | 2 | +1 pt |

That is roughly **half a rolling point per invocation**, so a 10-arm matrix at
`sample.n=3` (30 invocations) lands near 15% of one 5-hour window, and its
`retry`-worst case of 60 invocations near 30%. Both fit inside a single window
with room to spare.

Two things this does **not** establish. The write workload is one small tool
call; a *denied* tool call may cost more, because a model that is refused tends
to reason again and try another route — which is the entire point of the guard
experiment. And the repair workload is multi-turn and materially dearer. Treat
these figures as a floor for guard arms, not a forecast.

Note also that `rolling` is the sensitive counter — it moved 2 points while
weekly and monthly barely registered — which is what the published $12 / $30 /
$60 denominators predict, and is why it is the one worth gating on.

**`redact` is deliberately NOT attached, and that is a finding rather than an
omission.** It is the right tool for the job — a run store outlives the reason
it existed, and this one can contain provider keys. Two things stop it:

1. Its `patterns` come from spec config, and a spec is a committed file. A
   literal credential can never go there. *Shape* patterns are safe and would
   work, so this half is a constraint, not a wall.
2. It is a `wrap`, and a wrap has no declared channel into the record. It
   scrubs **silently**. The only way to report what it did is `report: true`,
   which its own docstring calls a knowingly-recorded breach.

So "the Workbench's redaction tool works on external subjects" and "positive
receipts, never absence-of-error" cannot both hold at the seam where `redact`
sits. Attaching it would give this tree a control that cannot say it fired,
which is the exact failure mode the adapter's own redaction was built to avoid.
The adapter continues to redact by value, before serialization, where it can
report a `redaction_count`. Recorded here rather than worked around, because
the honest version is the one that fails the check.

## Safety and limits

The workspace is disposable and repo-local so Hermes does not reject macOS's
resolved `/private/var` temporary path. The adapter also binds `PWD`,
`TERMINAL_CWD`, and `HERMES_WRITE_SAFE_ROOT` to that workspace and rejects any
hook-observed outside-workspace proposal. Hermes runs with `--yolo`, but
receives only the file toolset and the test directory contains only copied
fixture inputs. This reduces exposure; it is not containment.

DeepSeek Harness keeps its `workspace-write` filesystem sandbox. The experiment
patch replaces interactive approval with a named noninteractive preset and
disables subagent, workflow, Ralph, background-job, skill, goal, todo, search,
and web tools. Automatic title generation and the irrelevant dynamic runtime
context snapshot are disabled; the selected provider declares reasoning `off` and
an 8,192-token per-step cap. Its supported headless stdout is final text only,
so the adapter collects the first-party uncompressed session log from the
isolated `DSH_HOME` and normalizes native `tool/call`, `tool/result`, and
`turn/end` events. The schema stores those raw bytes in the `sidecar` capture
slot with `sidecar_kind: native_persisted_session_jsonl`.

The slot is named for its position, not its source, because its source is not
the same on every subject: `sidecar_kind` is `shell_hook_jsonl` for Hermes,
`native_persisted_session_jsonl` for DeepSeek, and `none` where no sidecar
exists. Reading the discriminator is mandatory — a persisted session log and a
hook transcript are different evidence with different trust, and a slot named
after one of them invites the other to be read as it.

Stdout, stderr, and the sidecar have positive byte limits. Exceeding stdout or
stderr terminates the owned process group; the Hermes hook refuses a sidecar
append that would exceed its limit. Credential-looking environment values are
scrubbed before captured bytes are serialized. Hermes receives only the active
provider credential, in its private temporary config and the provider variable
its client expects; unrelated host credentials are removed. Stored captures
report source byte counts, stored digests, redaction counts, overflow state,
and the initiating termination reason. Overflow flags are independent: a
process may cross another stream limit while handling a timeout, forwarded
signal, or single-stream limit that already started teardown.
If a stream is truncated while credential redactions are configured, its whole
retained prefix is replaced with a fixed marker. This sacrifices partial
stdout/stderr evidence because an incomplete JSON string may contain a fully
escaped credential far before the truncation boundary; the source byte count
and overflow facts remain.
An escaped child can outlive the owned group, but it cannot hold the adapter's
capture loop open indefinitely.

Guard receipts use `cross-harness-guard-event/v0.3`. Each run gets a new
RSA-2048 signing key: the private exponent is materialized only into the
owner-only disposable guard source and is absent from the subject environment
and retained record, while `oracle_evidence.authentication` retains the public
key and binds it to the receipt schema, subject, variant, and run ID. The
adapter verifies receipts during capture and `compare.py` verifies them again
from retained bytes. A separate `capture.guard_binding`, created before the
subject runs, independently retains the subject, variant, run ID, and key ID;
comparison requires the oracle proof to match it, so replaying a whole proof
from another run fails. Knowing only `HWB_GUARD_RECEIPT`, `HWB_GUARD_MODE`,
and the JSON shape is therefore insufficient to forge an evaluable receipt.

That authentication is not an OS sandbox. A subject that deliberately
enumerates and reads another same-UID process's disposable guard source could
recover the in-source private exponent. The experiment prevents accidental or
receipt-path-only fabrication; stronger resistance to a hostile subject needs
an operating-system isolation boundary outside this adapter.

Do not put secrets in prompts, arguments, or fixtures. Agent behavior remains
nondeterministic. The active `opencode-go` profile is a remote service whose
model label does not content-pin the served model; only the optional
`local-ollama` profile pins local model bytes. These are discovery runs, not
confirmation runs.

## GitHub release readiness

The released `v0.1.0` tree deliberately preserved the measured negatives in
its same-apparatus repair comparison: Claude failed closed on a changed native
tool-result shape, Codex passed repair outcome 2/3, Hermes passed 3/4 with one
recovered timeout, and DeepSeek and Pi passed 3/3. That matrix remains
historical evidence rather than being rewritten after the release.

Post-release stabilization on 2026-08-25 updates the experimental Claude Code
pin to `2.1.245`, qualifies its now-optional `is_error` field against the
enclosing native result, and requires the repair test command to run standalone
so a later chained command cannot obscure a green test. A fresh five-subject
comparison on one apparatus passes both adapter and exact repair outcome 3/3
for Claude Code, Codex CLI, DeepSeek Harness, Hermes Agent, and Pi, with no
timeouts. Exact run IDs, record hashes, comparator hash, and the bounded
interpretation are in `SHARED_ADAPTER_CONTRACT.md`.

The explicit core API/schema review is complete and defers promotion for
`0.1.0`. The contract's exact-five-subject, live-pin, and closed-field rules
do not match Workbench's vendor-neutral, additive public API. The generic
`capture` and `canon` boundary is already core; the envelope, normalizers,
pins, model profiles, and workload oracles remain experiment-local. See
`docs/adapter-envelope-promotion-review.md` in the source repository.
The strict Hermes steadiness result has also been reviewed: no current
`steady` allowance is narrow enough, because each
allowed stdout axis would suppress the entire adapter envelope and hide a real
one-tool/two-tool routing difference. The no-allowance `UNSTABLE` verdict
therefore stands as execution-evidence nondeterminism, alongside stable exact
task outcomes. Before the next public GitHub release, rerun the repository's
existing release procedure and CI from the final commit, review the
developer-preview DeepSeek version pin, and update the release conformance
record; do not push or tag from this experiment alone.

Hermes live evidence collected under the former `0.16.0` pin is historical.
The current `0.20.5` recut is now complete for write, both guard arms, repair,
and steadiness; exact run IDs, record hashes, and bounded verdicts are in
`SHARED_ADAPTER_CONTRACT.md`. Write and repair produced successful exact
outcomes, and the guard result reproduced the earlier tool-name containment
failure: after `write_file` was denied, Hermes reached the same effect through
`terminal`. The canonical no-allowance steadiness campaign was structurally
valid and passed all nine exact-write outcomes, but is `UNSTABLE` because its
retained stdout bytes moved across repeats. Do not add a broad stdout allowance
or call the raw evidence stable from this result. A semantic stability view
would need a separately versioned projection that retains the raw evidence;
that is an API/schema question rather than a normalization of `steady v0.1`.
