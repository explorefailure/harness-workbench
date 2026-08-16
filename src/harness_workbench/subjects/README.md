# Cross-harness adapter experiment

This experiment runs an exact file-write task and a red → edit → green repair
task through Claude Code, Codex CLI, DeepSeek Harness, and Hermes Agent, then
projects their
different evidence surfaces into the
candidate contract in
[`SHARED_ADAPTER_CONTRACT.md`](SHARED_ADAPTER_CONTRACT.md). Pi remains the
reference integration; these are the independent harnesses used to find what is
actually shared.

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
version and digest of the primitive that produced it, under `apparatus`,
because `freeze` binds the files beside the spec and cannot bind that one.

```sh
python3.11 -m unittest -v test_experiment.py     # 38 tests, offline
python3.11 runner.py --subject claude
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

Pi is the reference integration and was deliberately excluded while this
contract was derived, so that the shared envelope could not simply inherit Pi's
shape. It is included here as a fifth subject to *test* the envelope rather than
to inform it: what Pi cannot express through the shared contract is a finding
about the contract. Pi's own richer schema stays in `experiments/pi_coding_agent`.

Run each as a frozen, receipted Workbench discovery record:

```sh
PYTHONPATH=../../src python3.11 -m harness_workbench run claude.json
PYTHONPATH=../../src python3.11 -m harness_workbench run codex.json
PYTHONPATH=../../src python3.11 -m harness_workbench run deepseek.json
PYTHONPATH=../../src python3.11 -m harness_workbench run hermes.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_claude.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_codex.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_deepseek.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_hermes.json
PYTHONPATH=../../src python3.11 -m harness_workbench run faults.json
```

Compare either four saved outer-envelope JSON files or four Workbench run
directories:

```sh
python3.11 compare.py \
  runs/<claude-id> runs/<codex-id> runs/<deepseek-id> runs/<hermes-id>
```

`contract_passed` means the records satisfy the shared evidence contract. It
does not require every harness to finish the task. Inspect each subject's
`adapter_passed`, `outcome_passed`, and `timed_out` values separately.

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
both final four-subject comparisons pass, all eight records verify, and
DeepSeek passes the exact-write workload. Its repair adapter conforms, but two
final-source samples did not complete the repair, so the pinned local model is
not a stable repair subject. Promotion of the shared adapter contract into
Workbench core is still blocked on successful repeatable DeepSeek and Hermes
repair runs, steadiness evidence for the expanded matrix, and an explicit core
API/schema review. Before a public GitHub release, rerun the repository's
existing release procedure and CI from the final commit, review the
developer-preview DeepSeek version pin, and update the release conformance
record; do not push or tag from this experiment alone.
