# Hermes builder integration candidate

`hwb-hermes` materializes owned adapter sources for Hermes Agent 0.22.5, checks
runtime identity, records native stream-json events and independently evaluates
a coding repair. This is an unreleased candidate; it does not promote the
experimental universal cross-harness envelope.

## Install an isolated runtime

From this reviewed Workbench candidate checkout, use Python 3.11+ with working
virtual-environment support:

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

## Use a local model

Edit provider in adapter_config.json:

```json
{
  "mode": "local",
  "base_url": "http://127.0.0.1:11434/v1",
  "model": "YOUR_INSTALLED_MODEL"
}
```

```sh
hwb-hermes demo . --timeout 600 --output local.result.json
```

Use an installed tool-capable model with sufficient SERVER context for Hermes'
instructions and tool definitions. This adapter does not download models,
restart inference services or reconfigure their context. Local mode supports
explicit unauthenticated OpenAI-compatible endpoints with a fixed dummy key.
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
- hwb-hermes run CONFIG: capture validity only; supply your own task oracle.

Execution options: --runtime SOURCE_DIR, --uv UV_LAUNCHER, --timeout POSITIVE_SECONDS,
--workspace-parent EXISTING_DIR and --output NEW_FILE. Exit 0 means the selected
check passed, 1 means a measured capture/outcome failed, 2 means setup/identity/
launch failed. Process timeout and byte-bound termination remain explicit.

A custom config must declare itself, pin, task, adapter.py, normalizer.py,
scripted_provider.py, runtime_identity.py, gateway_cleanup.py and all fixture files in inputs. Use
relative owned paths; fixture links/special files are refused. The scripted
provider and demo oracle are specific to the repair. Use local mode and your
own independent checks for other workloads.

For an outer sealed record run hwb run coding.json, then hwb verify RUN_ID. The
wrapper uses the owned default runtime. If reusing another exact runtime, add
--runtime SOURCE_DIR to the spec argv before the first freeze. Inspect the
inner step exit/outcome; outer recording completion is not task success. A new
setup needs a new owned baseline; do not reset freeze to conceal input drift.

## Validation scope

Linux and macOS are intended targets. The hosted matrix runs real scripted tools
with Workbench Python 3.11/3.14 and the separate pinned Hermes Python 3.14.4.
Require actual results on the exact candidate; a configured matrix alone is
not platform evidence. Resumed sessions, shared gateways, inherited plugins/MCP,
cloud authentication, hostile-code containment and arbitrary task scoring are
outside this supported builder workflow.

Official references: [Hermes release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5),
[CLI reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands/),
and [pinned native emitter](https://github.com/NousResearch/hermes-agent/blob/bf0203a794a07aac3ff764972ab9087baa4e4f4e/hermes_cli/stream_json.py).

The native CLI starts a separate gateway for the fresh HERMES_HOME. Each capture
records an additional bounded cleanup after the CLI exits or hits its deadline.
Only the recorded PID with matching profile and live start-time identity is
signaled; cleanup failure fails capture validity. No other profiles or services
are scanned/stopped. This is lifecycle cleanup, not hostile-code containment.
