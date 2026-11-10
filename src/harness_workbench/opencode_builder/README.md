# OpenCode builder integration candidate

This README is copied into owned workspaces. The shared builder guide lives in
`docs/builder-adapters.md` in the Workbench checkout. Repository guide:
[builder adapters](https://github.com/explorefailure/harness-workbench/blob/main/docs/builder-adapters.md).
These unreleased changes may not yet be present on the remote main branch; use
the working checkout documentation as the matching reference.

`hwb-opencode` creates an owned adapter workspace, checks a pinned OpenCode
binary, captures CLI JSON events, and independently evaluates a reference repair.
This is an unreleased integration candidate. The cross-harness envelope remains
experimental and is not extended or promoted by this integration.

## Choose your model: local working draft

Keep `mode: "local"` for unauthenticated compatible endpoints. For an API-key
endpoint, set `provider` to mode `compatible`, with base_url, model and
api_key_env naming your selected environment variable. No key value belongs in
the config or argv. Optional context_window/max_tokens default to 32768/4096;
these client limits do not reconfigure the server.

Native mode accepts name `openrouter`, `openai`, `anthropic` or `google`, plus an
explicit base_url, model and api_key_env. It selects the corresponding bundled
OpenCode SDK; the endpoint must speak that protocol. It does not import OAuth or
saved accounts. Compatible Chat Completions, the OpenRouter native route and the other advertised
native API protocols have local fixture coverage. Protocol tests require actual
native terminal execution. Actual hosted accounts remain unvalidated.

Optional top-level checks run independent argv commands in the fixture after
capture. Declare task_checks.py and provider_auth.py with the other sources;
new default configs already include both. `task_checks.passed` is separate from
capture `verdict`. A failed capture or declared check causes a nonzero exit.
See [configuration and check examples](https://github.com/explorefailure/harness-workbench/blob/main/docs/builder-adapters.md) before your own task.
These changes are local/uncommitted; previous hosted CI does not cover them.


## First run

Install this Workbench candidate in a Python 3.11+ virtual environment:

Run this installation block from the Workbench checkout, not from an
initialized adapter workspace. If reading this README after `init`,
Workbench is already installed: keep that environment active and continue
with the runtime installation commands after `cd`.

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
hwb-opencode init ./my-opencode-workspace
cd my-opencode-workspace
python3 install_runtime.py
hwb-opencode doctor . --opencode ./runtime/opencode
hwb-opencode demo . --opencode ./runtime/opencode --output repair.result.json
```

Initialization performs no downloads and refuses existing destinations, including
symlinks. The explicit installer downloads OpenCode 1.19.5 from official GitHub
release assets, checks archive and binary SHA-256, and writes only NEW runtime/.
It extracts only the expected executable. If the release-download route answers
a 5xx, it retries through the GitHub API; set `GITHUB_TOKEN` to avoid the
anonymous API rate limit (the token is sent to api.github.com only). doctor checks binary identity BEFORE
executing a bounded version probe. Your global OpenCode install is untouched.

The default demo starts a bounded scripted OpenAI-compatible server on an
ephemeral loopback port. It drives actual read, bash, edit, bash calls. No API
account or model inference is involved. Independent Python tests fail before the
repair and pass afterward; fixture manifests must show only slugger.py changed.
The original test file must retain its bytes and mode. Native tool status is
kept separately from bash metadata.exit: OpenCode can mark a tool completed even
when the shell command exits nonzero.

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
hwb-opencode demo . --opencode ./runtime/opencode --timeout 600 --output local.result.json
```

This runs the shipped repair against your model and applies the strict reference
oracle. The local Qwen 27B run passed that oracle; the placeholder model and
endpoint above must be replaced and are not a claim about another server.

For an authenticated compatible endpoint, change `mode` to `compatible` and add
`"api_key_env": "MY_MODEL_KEY"` inside `provider`. Set that environment variable
before running; see the [credential example](https://github.com/explorefailure/harness-workbench/blob/main/docs/builder-adapters.md#choose-a-model-in-opencode-or-hermes).
For a native route, use this provider fragment with your explicit endpoint:

```json
{"mode":"native","name":"openai","base_url":"https://your-provider.example/v1","model":"YOUR_MODEL_ID","api_key_env":"MY_MODEL_KEY"}
```

This native example is schema-verified; actual hosted account access is
unvalidated. Select the native name and endpoint protocol together, as listed
above. `api_key_env` is the variable name; it never contains the key value.

Next follow [the shared custom-task walkthrough](https://github.com/explorefailure/harness-workbench/blob/main/docs/builder-adapters.md#a-small-task-you-can-adapt)
with `HWB_TASK_CONFIG=adapter_config.json`. It supplies new files, declares
independent checks and runs `own_task.json` without editing adapter source.
The generic `run` command evaluates those checks; `demo` always grades the
shipped slugger repair and cannot grade your other tasks.

Use an installed model with tool support and sufficient SERVER context for
OpenCode's instructions and tool definitions. The adapter advertises a conservative
32K context/4K output in its generated config; that does not reconfigure the
inference server. Ensure the server's actual context is at least 32K. With Ollama,
use an existing model profile with an appropriate num_ctx. No model download,
server restart, or global model reconfiguration is performed by the adapter.

Legacy local mode supports unauthenticated OpenAI-compatible endpoints;
compatible mode additionally supports an explicitly named API key. In legacy local mode a fixed dummy
Authorization value is used; saved provider accounts and ambient credential
variables are not passed. base_url rejects embedded credentials and query strings.
You explicitly choose and trust the endpoint. The adapter does not enforce network
isolation or prevent a tool reading arbitrary absolute paths. This is not a
hostile-code sandbox. Do not place secrets in fixtures or tasks.

The reference outcome check also applies to live-model demos: unchanged tests,
only implementation changed, native test failure followed by pass, independent
before/after tests, complete stream and clean bounded process termination. Local
models may use bash to inspect/edit; the fixed native read/edit sequence is
required only for the scripted control. A passing repair is one observation,
not a general claim about model quality or deterministic live-model replay.

## Commands, evidence and custom workloads

- `hwb-opencode init DIR`: NEW owned workspace with editable sources and pin.
- `hwb-opencode doctor DIR`: runtime identity and config/provenance check.
- `hwb-opencode demo DIR`: reference coding-repair outcome check.
- `hwb-opencode run CONFIG`: capture validity and optional declared independent checks.

All execution commands accept --opencode LAUNCHER, --timeout POSITIVE_SECONDS,
--workspace-parent EXISTING_DIR and --output NEW_FILE. The launcher can be on PATH
or an explicit path. Output files are created owner-only and never overwritten.
Exit 0 means the selected check passed; 1 means a measured capture/outcome failed;
2 means configuration/identity/launch failed. Missing runtime and malformed
configuration produce structured errors. Timeouts and byte bounds are evidence,
not fabricated successful completion.

Each run uses fresh HOME, XDG config/data/cache/state and TMPDIR. Project config
and parent instruction discovery, default/external plugins, model catalog fetch,
autoupdate, sharing, snapshots, LSP, formatters and file watching are disabled.
Read/edit/bash are allowed and other tools denied. This is a deliberately narrow
integration; inherited MCP tools, resumed sessions, interactive permission prompts,
custom plugins and OAuth/saved-account providers are outside its supported scope.
Managed host OpenCode configuration/preferences are refused because HOME/XDG
isolation cannot isolate them. Upstream detached plugin dependency installation
is set to npm offline mode with empty user/global npm configuration and install
scripts disabled; an unused background dependency warning may appear.
The upstream CLI emits completed tool observations, not full start/end execution
pairs; this adapter does not claim otherwise.

Retained roots contain raw stdout.jsonl, stderr.txt, generated runtime.json,
result.json and fixture snapshots/manifests. Scripted runs retain provider.json
with the actual provider-visible conversation. Event summaries retain native tool
IDs, inputs, outputs, status and exit metadata. Raw output digests are never
replaced by a normalized comparison hash. There is no claim of byte-identical
replay across timestamps, session IDs or model samples. Run roots persist until
you remove them; review paths and task data before sharing.

Copy adapter_config.json for a custom workload and declare the new file itself,
pin, task, adapter.py, normalizer.py, scripted_provider.py, provider_auth.py, task_checks.py and every fixture file
in inputs. Paths must be relative within the owned workspace; links and special
fixture files are refused. The scripted provider is specific to the reference
repair; use a configured model for custom workloads and declare independent checks.

For an outer sealed record put runtime/ on PATH, run `hwb run coding.json`, then
`hwb verify RUN_ID`. Inspect the inner step's exit and outcome as well: outer
recorder success is not task success. freeze/receipt bind the declared source
bytes, including the runtime pin. Start a new workspace when inputs change;
do not reset a baseline to hide drift.

## Runtime and validation scope

Earlier ARM64 Mac probes found an ordinary OpenCode native test surviving a
20-second adapter timeout in three attempts; a Linux source probe reproduced it.
OpenCode detached the tool into a separate session/group, so the native-client
group stopped while the recorded test PID survived. Those failures remain in
`further-validation/`; CLI failure/capture rejection alone did not prove cleanup.

The current adapter opts into observed-descendant supervision. It records owned
parent lineage and kernel birth identity while the native client runs, then
rechecks each recorded identity before cleanup, including observed children
that detached or reparented. Linux uses pidfd-bound signaling when available;
Darwin rechecks microsecond birth identity, but its final check/signal is not
atomic. Unknown identity, observation-budget exhaustion, signal errors or
survivors fail cleanup and capture. If Darwin cannot identify an initial
unreaped zombie, even an actual exit 0 fails setup rather than claiming a clean
capture.

Linux source and clean installed-wheel native regressions passed both a blocked
tool timeout and normal completion with lingering detached tools. Mac source
native regressions passed those two cases. Independent kernel audits found no
same-identity recorded child alive, kept an unrelated sentinel alive and
confirmed unchanged original inputs. Mac clean wheel and source-archive installed native regressions also passed;
these are the measured cases, not universal cleanup or containment guarantees.
Polling can miss fork/reparent activity between samples, and the adapter provides
no OS sandbox. Inspect `process.descendant_cleanup` separately from the native
client group fields. Fix evidence is retained in the local
`outputs/opencode-cleanup-fix/` bundle, alongside the earlier failures.

The pin binds official OpenCode 1.19.5 source tag to commit
810c0eba1a7348036f8bdf61e6632d0c36c6876c and contains archive/binary digests for
Linux x64/ARM64 and macOS x64/ARM64. Runtime byte identity is checked at each
invocation. This is not an upstream vulnerability audit or a proof of containment.
Linux x64 and ARM64 macOS 26.5 have local evidence. The Mac used a fresh
official OpenCode 1.19.5 ARM64 binary. Linux ARM64 and macOS x64 remain
unmeasured for this candidate. The configured hosted workflow includes the
actual scripted repair and negative outcome checks; its configuration is not
a current-candidate passing result.

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
One fresh attempt with a 1200-second bound completed in 752.71 seconds; native
unit-test exits were `[1, 0]`. The external baseline exited 1, final independent
tests/checks passed, only `total.py` changed, and original input/test bytes stayed
unchanged. A separate deterministic 100-integer-list probe passed. Recorded
process-group cleanup passed. These are Mac client/native-tool observations with
Linux inference, not Mac inference benchmarks, hosted accounts or universal
model/task compatibility. OpenCode now supervises observed owned descendants, with Linux source/installed
and Mac source native evidence; Mac clean wheel and source-archive installed native regressions also passed.
These functional results do not establish release-artifact conformance. Earlier timeout failures remain retained, and normal model-task
success does not establish cancellation behavior. Evidence is
retained in the local `further-validation/` bundle. No outer sealed model-task
record was produced.

Official interface references: [CLI](https://opencode.ai/docs/cli/),
[configuration](https://opencode.ai/docs/config/),
[providers](https://opencode.ai/docs/providers/), and
[pinned source](https://github.com/anomalyco/opencode/tree/v1.19.5).

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
--launcher OWNED_RUNTIME --output NEW_SPEC.json`. Use `opencode` as `HARNESS`;
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
opencode` reports the implemented typed profiles documented below. Unsupported values fail clearly; defaults remain compatible.

See the repository's `docs/builder-adapters.md` shared guide's “Seal a local custom task with the
evidence bridge” section for generation, budgets, retained paths and limitations.
Declared code is trusted; the bridge is not an OS sandbox.


### OpenCode: edit permission and owned native counter

For an unauthenticated HTTP loopback `provider.mode="local"`, the optional
`controls` envelope accepts these typed values (both may be combined):

```json
{"schema":"hwb-native-controls/v1","harness":"opencode","values":{
  "edit_permission":{"permission":"deny","expect":{"path":"target.txt","effect":"unchanged"}},
  "tool_counter":{"tool":"read","enabled":true,"minimum":1}
}}
```

Assign this object to the config's `controls` field and declare the existing
fixture target in `inputs`. `permission` is `allow` or `deny`; interactive `ask`
is rejected. `effect` is `changed` or `unchanged`. The counter accepts `read`,
`edit` or `bash`, boolean `enabled`, and integer `minimum` 1..100. It selects an
adapter-owned dependency-free native plugin; arbitrary plugins, package profiles
and unrestricted config merges are unsupported. Declare `control_runtime.py` in `inputs`; a new `init` ships this helper.
Its bytes are bound by the generated spec and retained with declared inputs.

A denied edit is removed from OpenCode's advertised tool list. The oracle
requires an actual attempted target edit and native unavailable/permission error,
unchanged target and checks that preserve it. Merely rendering a deny rule or
never attempting the edit cannot pass. The allow case additionally needs native
completion, the expected resolved file path and an independently changed file.
The counter correlates native `tool.execute.before` call IDs/tool names with
completed/error native tool records; loading a plugin alone is insufficient.

Counter mode selects the explicit owned-plugin profile instead of pure mode
(which suppresses all plugins). It retains fresh homes, disabled project config,
disabled default/Claude/external-skill discovery, disabled LSP/formatters/MCP,
offline package operations and tracked descendant cleanup. Only the generated
file URL is configured. No global runtime or package installation is performed.
Without a counter, existing pure-mode behavior remains. These are trusted local
controls, not an OS sandbox: allowed bash can bypass an edit-specific denial.

For baseline use allow/changed with the counter enabled. For enabled use
deny/unchanged. Known-red inversions use allow/unchanged or disable the counter
while still expecting a call. Inspect separate capture, task and control verdicts,
`native/runtime.json`, `launch.json`, `control-plugin.mjs` and
`control.trace.jsonl` inside the sealed bundle. A failed task may be the expected
result of a passing denial experiment; CLI failure is still nonzero.
