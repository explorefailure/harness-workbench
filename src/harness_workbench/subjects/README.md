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
Its generic `llm-pi-ai` plugin routes the content-pinned local
`qwen3.5:9b` model through Ollama's loopback OpenAI-compatible endpoint, so no
external API key is required. The experiment supplies only the placeholder
key-shaped value required by that provider profile and treats it as a redacted
credential. `dsh_patch.yml` retains workspace confinement while making the run
noninteractive. Hermes uses the same pinned local Ollama model and a temporary
`HERMES_HOME`; only its workload toolsets are enabled. The Hermes source
checkout defaults to `~/.hermes/hermes-agent`; set `HERMES_AGENT_ROOT` if it is
installed elsewhere.

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
python3.11 -m unittest -v test_experiment.py     # 103 tests, offline
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
export HWB_OPENCODE_KEY=$(cat ~/.config/hwb/opencode.key)
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
context snapshot are disabled; the local provider declares reasoning `off` and
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
scrubbed before captured bytes are serialized, and credential variables are not
passed to the local Hermes process. Stored captures report source byte counts,
stored digests, redaction counts, overflow state, and termination reason.
An escaped child can outlive the owned group, but it cannot hold the adapter's
capture loop open indefinitely.

Do not put secrets in prompts, arguments, or fixtures. Agent behavior remains
nondeterministic even when local model bytes are pinned, and a hosted model
label does not content-pin the remote service. These are discovery runs, not
confirmation runs.

## GitHub release readiness

The DeepSeek adapter is ready to include as experiment-local discovery work:
both final five-subject comparisons pass, all eight records verify, and
DeepSeek passes the exact-write workload. Its repair adapter conforms, but two
final-source samples did not complete the repair, so the pinned local model is
not a stable repair subject. Promotion of the shared adapter contract into
Workbench core is still blocked on successful repeatable DeepSeek and Hermes
repair runs, steadiness evidence for the expanded matrix, and an explicit core
API/schema review. Before a public GitHub release, rerun the repository's
existing release procedure and CI from the final commit, review the
developer-preview DeepSeek version pin, and update the release conformance
record; do not push or tag from this experiment alone.
