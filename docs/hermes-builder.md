# Hermes builder integration candidate

[All builder adapters](builder-adapters.md) · [Workbench documentation](../README.md#documentation)

`hwb-hermes` materializes owned adapter sources for Hermes Agent 0.22.5, checks
runtime identity, records native stream-json events and independently evaluates
a coding repair. This is an unreleased candidate; it does not promote the
experimental universal cross-harness envelope.

## Choose your model: local working draft

Keep `mode: "local"` for unauthenticated compatible endpoints. For an API-key
endpoint, set `provider` to mode `compatible`, with base_url, model and
api_key_env naming your selected environment variable. No key value belongs in
the config or argv. Hermes controls its own model/context handling; this adapter
does not accept context_window/max_tokens settings.

Native mode accepts name `openrouter`, `openai-api` or `anthropic`, plus an
explicit base_url, model and api_key_env. Hermes receives the paired provider
credential/endpoint variables. This does not import OAuth or saved accounts.
Compatible Chat Completions, OpenRouter and the other advertised native API
protocols have local fixture coverage requiring actual terminal execution.
Actual hosted accounts remain unvalidated.

Optional top-level checks run independent argv commands in the fixture after
capture and gateway cleanup. Declare task_checks.py and provider_auth.py with
the other sources; new defaults include both. `task_checks.passed` is separate
from capture `verdict`. Either failure gives a nonzero adapter exit. The selected
key is redacted from launch/output evidence. Check commands and gateway cleanup
do not receive provider keys. See [configuration and check examples](builder-adapters.md).
These changes are local/uncommitted; previous hosted CI does not cover them.


## Install an isolated runtime

From this reviewed Workbench candidate checkout, use Python 3.11+ with working
virtual-environment support:

The Workbench install requires Python virtual-environment support (including
`ensurepip` on distributions that package it separately) and access to the
declared build backend, `setuptools>=77`. The default source-install command
may fetch that build dependency; it is not an offline-install claim. A local
Workbench wheel installs without runtime dependency downloads. Native harness
installation below is a separate step with its own downloads.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m pip install uv==0.12.21
hwb-hermes init ./my-hermes-workspace
cd my-hermes-workspace
python3 install_runtime.py
hwb-hermes doctor .
hwb-hermes demo . --output repair.result.json
```

Workbench keeps zero Python runtime dependencies. uv, Hermes and its Python
runtime are optional external software. Initialization downloads nothing.
The explicit installer creates NEW runtime/, clones official release v0.22.5,
verifies its annotated tag object, source commit, actual tracked source hashes
and uv.lock, then runs a frozen editable install with Python 3.14.4. That Python
is downloaded into persistent runtime/python rather than temporary HOME state.

Hermes intentionally rejects ordinary wheel/sdist distribution builds. This
integration respects its supported source/editable workflow and never bypasses
that guard. The upstream project metadata uses version 0.0.0; the pinned release
identity is established through the tag, source and update-disabled version
probe, which reports Hermes Agent 0.22.5.

The source pin is commit bf0203a794a07aac3ff764972ab9087baa4e4f4e. Existing Hermes
installations and accounts are not updated. --runtime SOURCE_DIR can reuse an
exactly matching checkout/environment; otherwise the default is runtime/source.
Doctor checks tracked source bytes before invoking runtime code, checks Python
and uv versions, and requires uv sync --frozen --check --offline to report an
unchanged environment. This validates the selected dependency graph, not every
installed dependency or bytecode byte. Runtime pinning is not a vulnerability
audit or hostile-code containment guarantee.

## Start with a deterministic control

The demo's provider runs on an ephemeral loopback port. It produces four planned
model-shaped calls while Hermes executes its real read_file, terminal, patch and
terminal tools. The first native tests fail, the implementation changes and the
next tests pass. No model inference or API account is used in this control.

Hermes also probes local endpoints for Ollama metadata. The scripted fixture
records the recognized /api/show request and returns 404 because it is an
OpenAI-compatible fixture, not an Ollama server. Unknown endpoints, invalid probe
bodies, request size/count limits and malformed requests still fail visibly.

The independent oracle runs the original tests before and after the agent run,
requires only slugger.py to change and verifies that test bytes/mode remain
unchanged. It requires native test-command exit observations [1, 0], a complete
successful native stream, an actual clean process exit and unchanged declared
inputs. The scripted control additionally requires the exact four native calls;
a local model may inspect/edit through terminal or other enabled file tools.

Inspect outcome.passed and outcome.errors in the JSON. A valid capture or a final
assistant answer alone does not establish a successful repair.

## Run a local model and your own task

From the initialized workspace above, select an already-running endpoint and
its exact model ID. Set these two values for your server:

```sh
export HWB_MODEL_URL=http://127.0.0.1:11434/v1
export HWB_MODEL_ID=YOUR_INSTALLED_MODEL
python3 - <<'PYMODEL'
import json, os
from pathlib import Path
p = Path("adapter_config.json")
c = json.loads(p.read_text())
c["provider"] = {"mode": "local", "base_url": os.environ["HWB_MODEL_URL"],
                 "model": os.environ["HWB_MODEL_ID"]}
p.write_text(json.dumps(c, indent=2) + "\n")
PYMODEL
hwb-hermes demo . --timeout 600 --output local.result.json
```

This runs the shipped repair against your model and applies the strict reference
oracle. The local Qwen 27B run passed that oracle; the placeholder model and
endpoint above must be replaced and are not a claim about another server.

The llama.cpp path was exercised separately on 2026-11-11 with Hermes Agent
0.22.5 and `qwen3.8:27b` through a loopback OpenAI-compatible endpoint. The
server was llama.cpp build 12110 (`cd959bae9`, reported as `0.9.0-dev`) with a
65,536-token context. The capture and coding-repair outcome passed, native test
command exits were `[1, 0]`, only `slugger.py` changed, final independent tests
passed and the owned gateway stopped cleanly. The endpoint's `/api/show` probe
returned 404, as expected for raw llama.cpp, without blocking the run. This is
evidence for that model, task and server configuration only; it does not make
llama.cpp an Ollama metadata endpoint or establish arbitrary task success.

For an authenticated compatible endpoint, change `mode` to `compatible` and add
`"api_key_env": "MY_MODEL_KEY"` inside `provider`. Set that environment variable
before running; see the [credential example](builder-adapters.md#choose-a-model-in-opencode-or-hermes).
For a native route, use this provider fragment with your explicit endpoint:

```json
{"mode":"native","name":"openai-api","base_url":"https://your-provider.example/v1","model":"YOUR_MODEL_ID","api_key_env":"MY_MODEL_KEY"}
```

This native example is schema-verified; actual hosted account access is
unvalidated. Select the native name and endpoint protocol together, as listed
above. `api_key_env` is the variable name; it never contains the key value.

Next follow [the shared custom-task walkthrough](builder-adapters.md#a-small-task-you-can-adapt)
with `HWB_TASK_CONFIG=adapter_config.json`. It supplies new files, declares
independent checks and runs `own_task.json` without editing adapter source.
The generic `run` command evaluates those checks; `demo` always grades the
shipped slugger repair and cannot grade your other tasks.

Use an installed tool-capable model with sufficient SERVER context for Hermes'
instructions and tool definitions. This adapter does not download models,
restart inference services or reconfigure their context. Legacy local mode uses an explicit unauthenticated OpenAI-compatible endpoint
with a fixed dummy key; compatible mode supports an explicitly selected API key.
Saved credentials and ambient account/configuration variables are not passed.
Credentials embedded in endpoint URLs/query strings are rejected.

Every run gets fresh HOME, HERMES_HOME, XDG state and temporary directories.
Hermes --safe-mode disables inherited user config, rules/memory, plugins and MCP.
The invocation fixes file/terminal toolsets, a bounded turn count and a local
nonpersistent terminal backend. Lazy dependency installation is disabled and uv
is offline during checks/runs. The declared repair uses automatic tool approval.
These are reproducibility settings, not restrictions preventing arbitrary
absolute-file reads or outbound tool requests. Use trusted tasks/fixtures.

## Native evidence and its limits

Hermes 0.22.5 emits system/init, text, tool_use, tool_result and a terminal result.
This is more direct evidence than the historical adapter's shell-hook/process
boundary, which remains unchanged for its earlier research fixtures.

The normalizer requires one init, paired native tool IDs/names, explicit boolean
is_error observations and a successful terminal result carrying a session ID.
It refuses missing, duplicate, mismatched, malformed or post-terminal events.
Initial session_id may be empty upstream; the adapter does not invent a value.
It retains native is_error separately from the exit_code decoded from terminal
result JSON. Intermediate command failure can be part of a successful repair.

Upstream CLI tool-result strings are capped at 5000 characters, potentially
leaving truncated JSON. The raw emitted bytes remain retained. Undecodable
outputs get no invented exit status; they cannot pass the reference test-exit
oracle. Scripted provider requests additionally preserve what the provider saw;
a live local run does not claim a full uncapped API trace. There is no claim of
byte-identical replay across native timestamps, session IDs or model samples.

Roots retain stdout.jsonl, stderr.txt, launch.json, result.json, fixture manifests
and provider.json for scripted requests/probes. They persist until you remove
them and may contain local paths/task data. Output files are NEW owner-only
files; evidence is never overwritten.

## Commands and custom tasks

- hwb-hermes init DIR: NEW owned workspace.
- hwb-hermes doctor DIR: pinned source/runtime and config check.
- hwb-hermes demo DIR: reference coding-repair oracle.
- hwb-hermes run CONFIG: capture validity and optional declared independent checks.

Execution options: --runtime SOURCE_DIR, --uv UV_LAUNCHER, --timeout POSITIVE_SECONDS,
--workspace-parent EXISTING_DIR and --output NEW_FILE. Exit 0 means the selected
check passed, 1 means a measured capture/outcome failed, 2 means setup/identity/
launch failed. Process timeout and byte-bound termination remain explicit.

A custom config must declare itself, pin, task, adapter.py, normalizer.py,
scripted_provider.py, runtime_identity.py, gateway_cleanup.py, provider_auth.py, task_checks.py and all fixture files in inputs. Use
relative owned paths; fixture links/special files are refused. The scripted
provider and demo oracle are specific to the repair. Use a local, compatible or native model provider and your
own independent checks for other workloads.

For an outer sealed record run hwb run coding.json, then hwb verify RUN_ID. The
wrapper uses the owned default runtime. If reusing another exact runtime, add
--runtime SOURCE_DIR to the spec argv before the first freeze. Inspect the
inner step exit/outcome; outer recording completion is not task success. A new
setup needs a new owned baseline; do not reset freeze to conceal input drift.

## Validation scope

Linux and macOS are intended targets. Fresh ARM64 macOS 26.5 validation used
Hermes 0.22.5 with its separate managed Python 3.14.4, uv 0.12.21 and the
frozen Anthropic extra / SDK 0.87.0. Workbench Python versions below are
separate from the owned Hermes interpreter.

Fresh owned-workspace validation on ARM64 macOS 26.5 on 2026-11-03 passed
runtime identity checks. The complete source suite ran with Workbench Python
3.11.15 and 3.14.7: each ran 464 tests, with 463 passing and one skip because
the historical core-only Hermes runtime was not installed. A clean installed
wheel built with pinned setuptools 83.0.0 passed all nine authenticated local
native protocol routes and all three scripted native-tool repairs.

These measurements establish local ARM64 Mac execution for this candidate
snapshot. These scripted measurements alone do not measure model task outcomes,
hosted
account access, current-candidate hosted CI or macOS x64. The earlier Qwen
repair results are separate Linux model evidence. The historical Pi baseline
profile was not installed for this Mac validation. Exact-candidate hosted CI
remains a release gate.

A separate installed ARM64 Mac execution of the documented `total(values)` task
used the existing Linux Qwen 27B service through an owned loopback-only SSH tunnel.
One fresh attempt with a 1200-second bound completed in 1102.12 seconds; native
unit-test exits were `[1, 0]`. The external baseline exited 1, final independent
tests/checks passed, only `total.py` changed, and original input/test bytes stayed
unchanged. A separate deterministic 100-integer-list probe passed. Recorded
process-group cleanup passed and the owned Hermes gateway was independently
confirmed stopped. These are Mac client/native-tool observations with
Linux inference, not Mac inference benchmarks, hosted accounts or universal
model/task compatibility. OpenCode now supervises observed owned descendants, with Linux source/installed
and Mac source native evidence; Mac clean wheel and source-archive installed native regressions also passed.
These functional results do not establish release-artifact conformance. Earlier timeout failures remain retained, and normal model-task
success does not establish cancellation behavior. Evidence is
retained in the local `further-validation/` bundle. No outer sealed model-task
record was produced.

The configured hosted matrix runs real scripted tools with Workbench Python
3.11/3.14 and the separate pinned Hermes Python 3.14.4. A configured matrix
alone is not platform evidence. Resumed sessions, shared gateways, inherited plugins/MCP,
OAuth/saved-account authentication, hostile-code containment and arbitrary task scoring are
outside this supported builder workflow.

Official references: [Hermes release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5),
[CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/),
and [pinned native emitter](https://github.com/NousResearch/hermes-agent/blob/bf0203a794a07aac3ff764972ab9087baa4e4f4e/hermes_cli/stream_json.py).

The native CLI starts a separate gateway for the fresh HERMES_HOME. Each capture
records an additional bounded cleanup after the CLI exits or hits its deadline.
Only the recorded PID with matching profile and live start-time identity is
signaled; cleanup failure fails capture validity. No other profiles or services
are scanned/stopped. This is lifecycle cleanup, not hostile-code containment.

## Native provider dependency profile

The default runtime pin now includes the upstream `anthropic` extra, with
Anthropic SDK 0.87.0 from the frozen uv.lock. The explicit installer installs
that extra alongside core dependencies; runtime checks require its graph and
SDK version before a workload starts. Lazy installation remains disabled.
An older core-only Hermes environment will fail the updated runtime check.
Initialize a new owned workspace and run its installer to use this profile;
do not update a global Hermes install or bypass the identity check.

Hermes sessions are explicitly pinned to the retained fixture with `--in DIR`.
Setting only TERMINAL_CWD was insufficient inside a parent Git workspace. The
scripted control now uses relative read/patch paths, and protocol regressions
require native `pwd` to report the exact retained fixture. This checks execution
location, not filesystem containment.

The task query also names the retained fixture explicitly and asks the model to
read, edit and run checks there. This avoids ambiguous relative filenames when
a parent Git project is visible. These instructions are guidance, not an access
restriction: review input hashes and independent checks as well as model claims.

## Review fixes and compatibility

Legacy scripted/local configs without checks that omit both new helpers are
migrated in memory: `provider_auth.py` and `task_checks.py` are added to the
effective hashed input set, and `input_migration` records those additions. The
config file is not rewritten. Both helper files must exist in the upgraded owned
workspace. Partial omissions and omissions in provider/check configurations are
rejected. Sealed specs must also declare the helpers in their own step inputs.

Independent reference tests use a separate environment without provider keys;
their captured output is also redacted, including fail-closed handling of
truncation. Input hashes are checked again after independent tests and declared
checks. Changed, deleted or symlink-replaced original inputs fail the capture,
even when a check exits successfully. The final input hashes are recorded as `inputs_after`.


## Local custom-task evidence bridge

The unreleased candidate also provides `hwb-adapter spec HARNESS CONFIG
--launcher OWNED_RUNTIME --output NEW_SPEC.json`. Use `hermes` as `HARNESS`;
for Hermes the launcher argument is the owned source directory, while Pi and
OpenCode take their pinned executable. It stages declared task inputs afresh
for each outer retry/sample and retains selected results/fixture files inside
the sealed record. Configure independent checks and inspect their verdicts;
outer completion and sealed verification do not establish task success.

This bridge revision accepts scripted or unauthenticated HTTP loopback routes
only. Native-client bridge qualification currently covers Pi/OpenCode on Linux;
Hermes now also has Linux native bridge observations. The earlier Pi slice
passed source/wheel/source-archive native bridge checks on ARM64 macOS; that
artifact preceded the new OpenCode/Hermes controls. New controls need their own
platform qualification. Earlier direct
adapter/model/platform evidence remains separate. `hwb-adapter capabilities
hermes` reports the implemented typed profiles documented below. Unsupported values fail clearly; defaults remain compatible.

See the [shared builder guide](builder-adapters.md)'s “Seal a local custom task with the
evidence bridge” section for generation, budgets, retained paths and limitations.
Declared code is trusted; the bridge is not an OS sandbox.


### Hermes: selected toolsets and owned patch hook

For an unauthenticated HTTP loopback `provider.mode="local"`, the optional
`controls` envelope accepts:

```json
{"schema":"hwb-native-controls/v1","harness":"hermes","values":{
  "tool_selection":{"toolsets":["file","terminal"],"expect_terminal":true},
  "patch_hook":{"block":true,"expect":{"path":"target.txt","effect":"unchanged"},"timeout":1}
}}
```

Assign the object to `controls` and declare the existing fixture target in
`inputs`. Toolsets are a nonempty unique subset of `file` and `terminal`;
`expect_terminal` currently must be `true` and requires an actually observed
terminal call. A false expectation is rejected: Hermes omits filtered terminal
calls from its CLI stream, so this oracle cannot establish unavailability.
Selecting only `file` while expecting a terminal call is a known-red inversion. Hook `block` is boolean, `effect` is `changed`/`unchanged`, and `timeout` is
an integer 1..5 seconds. These are fixed owned profiles, not arbitrary shell,
Python plugin or user-config imports. Declare the shipped `control_runtime.py` helper in `inputs`; its execution
bytes are bound and retained with the task inputs. No account stores or package
dependencies are imported.

Tool selection alone preserves safe mode. A patch hook requires a new explicit
owned customization profile: generated config and hook, accepted deliberately
with `--accept-hooks`, fresh HERMES_HOME, empty bundled-plugin directory,
`plugins.enabled=[]`, project plugins disabled, empty MCP config and ignored
project rules. Existing configs retain `--safe-mode`. The frozen Anthropic
runtime profile and identity checks, explicit `--in` directory and workspace
query remain mandatory; no pinned source or global runtime changes are needed.

Native pre/post hooks retain session/call IDs, tool/path, decision and terminal
status. Hermes' CLI stream omits blocked patch calls. The denial oracle therefore
requires correlated native pre/post hooks with `blocked`/`plugin_block`, an
unchanged target and stable independent-check files; missing execution events
alone do not prove denial. Allowed edits also require native streamed completion
and the exact resolved retained path. The hook process's cwd can be the gateway's
runtime source directory; it is not proof of task cwd. A native `pwd` and file
inspection check the task location separately. This is not filesystem containment. Other file or terminal tools can bypass
a patch-specific block.

Baseline allows a changed target; enabled blocks it and expects unchanged;
invert the block or toolset while keeping the expectation to prove the sensor
rejects the wrong behavior. Hook error, timeout, missing file and wrong matcher
must fail qualification. Native `fail_closed` covers the blocking hook's command
failure/deadline; adapter profile hashes and activation/effect checks reject
missing/skipped/mutated hooks. This is not a claim that all upstream exceptions
fail closed. Bounded owned-gateway cleanup is checked on every completed attempt.
Inspect the generated hook/config, native trace, launch and cleanup JSON in the
sealed record, alongside the independent task verdict and original input hashes.
