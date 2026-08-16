# Cross-harness adapter experiment

This experiment runs an exact file-write task and a red → edit → green repair
task through Claude Code, Codex CLI, and Hermes Agent, then projects their
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
use their existing authenticated clients. Hermes uses the pinned local Ollama
model and a temporary `HERMES_HOME`; only its file toolset is enabled. The
Hermes source checkout defaults to `~/.hermes/hermes-agent`; set
`HERMES_AGENT_ROOT` if it is installed elsewhere.

```sh
python3.11 -m unittest -v test_experiment.py
python3.11 runner.py --subject claude
python3.11 runner.py --subject codex
python3.11 runner.py --subject hermes
python3.11 runner.py --subject claude --workload repair
python3.11 runner.py --subject codex --workload repair
python3.11 runner.py --subject hermes --workload repair
python3.11 fault_runner.py
```

Run each as a frozen, receipted Workbench discovery record:

```sh
PYTHONPATH=../../src python3.11 -m harness_workbench run claude.json
PYTHONPATH=../../src python3.11 -m harness_workbench run codex.json
PYTHONPATH=../../src python3.11 -m harness_workbench run hermes.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_claude.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_codex.json
PYTHONPATH=../../src python3.11 -m harness_workbench run repair_hermes.json
PYTHONPATH=../../src python3.11 -m harness_workbench run faults.json
```

Compare either three saved outer-envelope JSON files or three Workbench run
directories:

```sh
python3.11 compare.py runs/<claude-id> runs/<codex-id> runs/<hermes-id>
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

Stdout, stderr, and hook evidence have positive byte limits. Exceeding stdout or
stderr terminates the owned process group; the Hermes hook refuses a sidecar
append that would exceed its limit. Credential-looking environment values are
scrubbed before captured bytes are serialized, and credential variables are not
passed to the local Hermes process. Stored captures report source byte counts,
stored digests, redaction counts, overflow state, and termination reason.
An escaped child can outlive the owned group, but it cannot hold the adapter's
capture loop open indefinitely.

Do not put secrets in prompts, arguments, or fixtures. Hosted model behavior is
not deterministic, and a model label does not content-pin the remote service.
These are discovery runs, not confirmation runs.
