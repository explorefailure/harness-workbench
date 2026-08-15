# Pi Coding Agent adapter and integration proof

Experiment results and code consequences are recorded in
[`LEARNINGS.md`](LEARNINGS.md). The repository-wide write-up standard is
[`docs/experiment-writeups.md`](../../docs/experiment-writeups.md).

Status: implemented and locally testable, but not shipped. This experiment remains
outside the Python package and release examples while Harness Workbench is at
`0.1.0rc1`.

The reusable layer is `adapter.py`. It accepts a `pi-hwb-adapter-config/v0.1`
configuration, verifies the pinned Pi installation, creates a disposable workspace,
launches Pi in JSON mode, preserves raw evidence, normalizes Pi events, and emits a
`pi-hwb-adapter-run/v0.1` envelope. It has no experiment verdict, guard variant, or
knowledge of the files below.

`text_adapter_config.json` is a second, independent consumer of that same adapter.
It uses a different fixture, task, scripted provider, model, system prompt, and tool
policy. Pi returns one deterministic text response with all tools disabled, creates
no sidecar evidence, and leaves the workspace unchanged. This is the reusable-layer
proof: the adapter is not merely a renamed part of the guard experiment.

`read_edit_adapter_config.json` is a third consumer. Its provider drives separate
`read` and `edit` tool cycles against `nested dir/naïve file.txt`, then the tests
verify the exact edited bytes, the unchanged surrounding file, and the correlated
Pi tool evidence. This covers a nested path containing spaces and non-ASCII text
without granting `bash` or `write`.

`coding_adapter_config.json` is a fourth, more realistic consumer. Pi reads a buggy
Python slug utility and its tests, runs the suite to reproduce the failure, edits the
implementation, and reruns the suite successfully. The fixture is synthetic and
offline, but the `read`, `edit`, and `bash` operations are Pi's real tools rather
than adapter simulations. `coding_runner.py` applies a separate coding-specific
oracle: the release-facing command passes only for the exact red-test → intended
edit → green-test lifecycle, with no invariant or unexpected workspace changes.

`plan_adapter_config.json` drives a fifth workload through paired plan/action
arms. The same provider performs two read-only positive controls, a direct
`write`, and a shell-mediated write. Plan mode disables the direct tool and
blocks the unsafe shell command; action mode permits both exact effects.

The extension-composition pair reverses a target-mutating hook and a guarding
hook. It demonstrates that policy order changes the durable result and that
Pi's execution-start arguments are pre-hook proposals, not effective arguments.

The throwing-handler pair reverses an auditing hook and a hook that raises during
`tool_call`. Pi fails the treatment closed and continues to a healthy positive
control, but stops invoking later handlers after the exception. The experiment's
oracle distinguishes execution safety from complete audit visibility.

The conflicting-policy pair reverses an explicit `{block: false}` handler and a
blocking handler. Both orders deny the treatment: allow continues the chain, while
block is a terminal veto that prevents later handlers from running.

The first consumer is a separate controlled experiment. `control_runner.py` adds the
guard extension and delegates its verdict to `control_oracle.py`. An offline scripted
provider asks Pi to make two writes:

- `permitted.txt` is the positive control and must succeed in both variants.
- `forbidden.txt` is the treatment target. The same guard extension blocks it in
  the `block` variant and permits it in the `allow` variant.

The control pair passes only when the declared treatment explains the guard decision,
Pi's tool result, and the durable `forbidden.txt` effect while the positive control
and all stable evidence agree.

No live model, API key, user repository, or saved Pi session is used by the supplied
configuration. Pi is not a sandbox. `adapter_config.json` limits Pi to the `write`
tool and disables ambient resources; the adapter supplies an empty private config
and minimal environment, uses a disposable workspace, and bounds its owned POSIX
process group on handled exit paths. The Workbench timeout is a wider emergency
cutoff, not a containment boundary. A child that creates a new session—or an
unhandleable kill of the adapter—can escape cleanup; use a container or VM when
containment of hostile workloads is required.

## Pin and install

The exact tested identity is recorded in `pin.json`:

```sh
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.1
pi --version
node --version
```

The adapter requires Pi `0.84.1`, Node `22.22.3`, and Python 3.11 or newer. It
verifies Pi's package name and version plus the pinned hashes of `package.json`,
`npm-shrinkwrap.json`, the launcher, and the Pi-owned installed file tree. The
shrinkwrap hash binds the dependency graph represented by that installation.

## Run the adapter directly

`adapter_config.json` is a complete reusable adapter request. From this directory:

```sh
python3.11 adapter.py adapter_config.json
```

That command runs Pi and emits transport, runtime, lifecycle, raw-byte, and workspace
evidence. It does not load the guard extension or judge the block/allow relation.
Replace the declared task, fixture, extensions, and `pi_arguments` to define another
Pi workload. Every consumed task, fixture file, extension, and pin must appear in
`inputs`; missing provenance fails before Pi launches. Do not place credentials in
the config or arguments because both become recorded evidence. Caller-supplied
environment values are passed to Pi but only their names are recorded.

Run the independent text-only workload with:

```sh
python3.11 adapter.py text_adapter_config.json
```

Run the independent read/edit workload with:

```sh
python3.11 adapter.py read_edit_adapter_config.json
```

Run the contained coding repair with:

```sh
python3.11 coding_runner.py
```

Use `python3.11 adapter.py coding_adapter_config.json` only when inspecting the
generic adapter capture without applying the coding outcome oracle.

Run that repair as a sealed Workbench workload with frozen inputs and receipts:

```sh
PYTHONPATH=../../src python3.11 -m harness_workbench run coding.json
PYTHONPATH=../../src python3.11 -m harness_workbench verify <coding-run-id>
```

Run the sealed plan/action pair with:

```sh
PYTHONPATH=../../src python3.11 -m harness_workbench run plan_mode.json
PYTHONPATH=../../src python3.11 -m harness_workbench run action_mode.json
PYTHONPATH=../../src python3.11 verify_plan_pair.py runs/<plan-run-id> runs/<act-run-id>
```

Run the sealed extension-order and throwing-handler pairs with:

```sh
PYTHONPATH=../../src python3.11 -m harness_workbench run mutate_first.json
PYTHONPATH=../../src python3.11 -m harness_workbench run guard_first.json
PYTHONPATH=../../src python3.11 -m harness_workbench run throw_first.json
PYTHONPATH=../../src python3.11 -m harness_workbench run audit_first.json
PYTHONPATH=../../src python3.11 -m harness_workbench run block_first.json
PYTHONPATH=../../src python3.11 -m harness_workbench run allow_first.json
```

Those source-bound commands deliberately use the Workbench from the current
checkout. If you use the shorter installed `hwb` command instead, run `hwb
--version` first and confirm that it is the intended Harness Workbench release;
an older unrelated installation can otherwise be selected from `PATH`.

Every adapter configuration declares positive `capture_limits` for stdout, stderr,
and named evidence. The adapter watches stdout and stderr while Pi runs and terminates
its owned process group when either limit is observed. Captures at or below the limit
retain their complete base64 bytes. Oversized captures remain in the retained root;
the envelope records their exact byte count and streaming SHA-256, reports a failed
verdict, and does not load or base64-encode them in memory. A named evidence file may
request a smaller `max_bytes`, but never a value above the configuration's evidence
ceiling.

## Run and verify the control pair

From this directory, using the repository's editable installation or an installed
`hwb` command:

```sh
hwb run block.json
hwb run allow.json
hwb verify <block-run-id>
hwb verify <allow-run-id>
python3.11 verify_pair.py runs/<block-run-id> runs/<allow-run-id>
```

The specs enable Workbench's built-in `freeze` and `receipt` features. On the first
run they create ignored freeze baselines beside the specs; subsequent runs fail on
input drift. The pair verifier requires the freeze and receipt maps to agree across
both variants and to match the adapter's independent digest map.

Each step emits one `pi-hwb-control-run/v0.1` envelope. Its nested
`pi-hwb-adapter-run/v0.1` capture contains:

- exact captured stdout, stderr, and guard-sidecar bytes as base64 plus SHA-256;
- strict UTF-8 decoded views where decoding succeeds;
- an ordered, correlated lifecycle and tool-event projection;
- before/after file manifests and the retained workspace path;
- runtime, installed-package, isolation, and process-cleanup evidence.

`hwb diff <block-run-id> <allow-run-id>` remains useful for inspecting the complete
records. `verify_pair.py` is the causal-isolation gate: it requires both Workbench
records to be sealed, conforming, and untampered; masks only the declared variant
outcomes; and rejects other differences in the bound inputs, runtime, Pi arguments,
lifecycle, tool calls, positive control, or durable filesystem state.

The outer control envelope adds only the variant, control verdict, and stable
comparison projection. This is a paired external control, not a use of `hwb
efficacy`. The Pi extension is part of the inner workload, so Workbench's built-in
feature inversion cannot mutate that extension directly.

Run the experiment tests with:

```sh
python3.11 -m unittest test_experiment.py -v
```

Run the heavier reproducibility proof separately from the ordinary suite:

```sh
python3.11 stress_adapter.py --runs 100 --concurrency 8
```

The soak compares the input/config/runtime/isolation/workspace/normalized-summary
projection across all successful runs while deliberately excluding volatile raw
session IDs, paths, and timestamps. It also requires a unique retained root and no
surviving owned process group for every run.

The ordinary tests cover malformed and out-of-order streams, pinned session
protocol drift, duplicate terminals, retry and compaction cycles, lifecycle deletion
and duplication matrices, tool correlation, closed config validation, path traversal
and symlink rejection, exact pin and installed-tree mutation rejection, input binding,
raw evidence boundaries and malformed encodings, process nonzero exit, timeout,
detachment, SIGTERM, unwritable workspace, and output pressure, credential/host-state
filtering, ambient project-resource suppression and explicit loading, adapter/oracle
separation, independent text-only and read/edit workloads, a coding-repair
false-success matrix, the real red→repair→green Pi run, eight concurrent isolated Pi
runs, deterministic provider and extension failure captures, both real Pi controls,
paired plan/action policy enforcement and its negative mutations, an
undeclared-difference mutation matrix, extension mutation/guard ordering,
throwing-handler fail-closed and audit-order behavior, terminal block precedence
over explicit allow, and sealed-record tamper rejection.

Hostile escaped-session/network/process-pressure cases remain container-only. A live
provider run remains optional, cost-bearing discovery rather than confirmation. RPC
and Linux are separate gates because they add a new transport or support claim.
