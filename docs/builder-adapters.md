# Bring your harness, model and task

Use Harness Workbench with Pi, OpenCode or Hermes to run your own tasks with a
model you choose, retain native tool evidence and check results independently.
Choose a harness, configure a model/provider, supply working files and checks,
then inspect the run. Scripted repairs are optional integration diagnostics.

Start with the harness whose native tools and runtime you want to evaluate.
Pi offers a provider configuration command; OpenCode uses a pinned standalone
binary; Hermes uses a pinned source checkout and its own Python environment.
All three copy your fixture into a retained run directory and can execute checks
you define. You do not need to edit adapter source to select a provider or task.

A **compatible model** needs all four of the following:

- An endpoint speaking a connection protocol supported by the selected adapter
  and harness. A Chat Completions URL is not a Responses or Anthropic endpoint.
- Tool calling that the harness can decode and use with its native tools. Text
  chat alone is insufficient for these repair workflows.
- Enough actual server context for instructions, tool schemas, task files and
  accumulated results, plus room for output. Client metadata cannot enlarge it.
- Valid access: a reachable endpoint, an available model ID and, where required,
  a credential with model/API permissions and quota.

Compatibility lets the task run; performance is whether the model solves it.
A compatible model can still choose the wrong files, make a bad edit or fail
checks. Neither a fixture pass nor one repair establishes universal compatibility
or model quality. Workbench records execution and evaluates declared checks.

## Current working draft

These integrations are unreleased. The provider/check changes are local and
uncommitted; an older published package or the existing PR head does not include
them. Install the working checkout to try them.

| Harness | Pinned runtime | Model selection | Detailed guide |
| --- | --- | --- | --- |
| Pi (`hwb-pi`) | Pi 1.4.0 / Node 26.13.0 | `configure` selects a compatible endpoint or Pi catalog provider | [Pi](pi-builder.md) |
| OpenCode (`hwb-opencode`) | OpenCode 1.19.5 | Provider config selects a compatible endpoint or bundled native SDK | [OpenCode](opencode-builder.md) |
| Hermes (`hwb-hermes`) | Hermes 0.22.5 / Python 3.14.4 / uv 0.12.21 | Provider config selects a compatible endpoint or named native provider | [Hermes](hermes-builder.md) |

Workbench requires Python 3.11+. Hermes uses its separate owned Python. Pi needs
its pinned Node on PATH. Runtime installation instructions are in each guide.
Current-candidate local validation covers Linux and macOS 26.5 arm64 as detailed
below. Previous hosted adapter CI covers the base commit only.

## Install and initialize

From the working Workbench checkout, use Python with working `venv`/`ensurepip`
support (some Linux distributions need their venv package). Building this
checkout needs `setuptools>=77`; pip build isolation may need network access or
a populated package cache even though Workbench has no Python runtime dependencies.
The current host Python lacked ensurepip; a retained managed Python successfully
created clean environments for the offline wheel/source installation checks.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Keep the environment active. Run the chosen adapter's `init NEW_DIRECTORY`, then
its guide's explicit pinned runtime installation and `doctor`. Initialization
copies editable sources, task, fixture, pin and spec; it downloads nothing.
Existing workspaces and output files are refused to preserve earlier evidence.

## Choose a model in Pi

From a newly initialized Pi workspace with the pinned runtime installed:

```sh
hwb-pi configure . --base-url http://127.0.0.1:11434/v1 --model YOUR_MODEL_ID --context-window 65536 --check '["python3", "-B", "-m", "unittest", "-v"]'
hwb-pi run model_adapter_config.json --pi ./runtime/node_modules/.bin/pi --timeout 600 --output task.result.json
```

Replace the endpoint/model with your running server's values. Add
`--api-key-env MY_MODEL_KEY` for authentication; set that variable through your
shell or secret manager. No key value goes in the config or argv.

For a Pi catalog provider use `--provider PROVIDER_ID --model MODEL_ID
--api-key-env MY_MODEL_KEY` instead of the compatible endpoint options. A proxy
`--base-url` is optional. Native providers use Pi's catalog API/context metadata.
Compatible endpoints default to `openai-completions`; `--api` also accepts
`openai-responses`, `anthropic-messages` and `google-generative-ai`. All four compatible APIs now have local protocol-fixture coverage requiring an
actual native terminal-tool completion. The catalog OpenRouter route is also
covered separately. Actual hosted accounts remain unvalidated.

`configure` creates NEW `model_adapter_config.json`, leaving the scripted control
unchanged. Use `--config-name NEW_NAME.json` for another setup. It does not contact
the endpoint. No provider extension is needed for these paths.

## Choose a model in OpenCode or Hermes

For an unauthenticated endpoint, edit `provider` in `adapter_config.json`:

```json
{"mode":"local","base_url":"http://127.0.0.1:11434/v1","model":"YOUR_MODEL_ID"}
```

For an authenticated compatible Chat Completions endpoint:

```json
{
  "mode": "compatible",
  "base_url": "https://your-provider.example/v1",
  "model": "YOUR_MODEL_ID",
  "api_key_env": "MY_MODEL_KEY"
}
```

Set `MY_MODEL_KEY` through your shell/secret manager. Only the explicitly named
credential is passed; saved accounts and unrelated provider variables are not
copied. Missing/empty credentials fail setup. The reference accepts single-line
keys of at least eight characters. API keys are never passed in process argv.

Supply a key without putting its value in shell history (Bash example):

```sh
read -r -s -p 'Provider API key: ' MY_MODEL_KEY
printf '\n'
export MY_MODEL_KEY
# Run your selected adapter here; afterward:
# unset MY_MODEL_KEY
```

Use HTTPS for remote authenticated endpoints. Plain HTTP loopback examples
assume a local service you trust. Endpoint URLs must not contain credentials,
query strings or fragments. `YOUR_MODEL_ID` and example URLs are placeholders,
not preinstalled models or validated hosted services.

Connection choices are harness-specific:

| Adapter | Compatible endpoint protocol | Native provider selection |
| --- | --- | --- |
| Pi | `openai-completions` (Chat Completions, default), `openai-responses`, `anthropic-messages`, `google-generative-ai` | `--provider` selects a provider/model present in the pinned Pi catalog; OpenRouter has local fixture evidence |
| OpenCode | Chat Completions via `local` or `compatible` | `openrouter`, `openai`, `anthropic`, `google` using bundled SDKs |
| Hermes | Chat Completions via `local` or `compatible` | `openrouter`, `openai-api`, `anthropic` using Hermes native routes |

Pi catalog mode requires an API-key environment reference and a catalog model ID;
the adapter's provider-name syntax check alone does not prove catalog membership
or access. Other Pi catalog combinations require their own validation. For
example, `hwb-pi configure . --provider openrouter --model YOUR_CATALOG_MODEL_ID
--api-key-env MY_MODEL_KEY --config-name router.json` selects a native route;
replace the placeholder with an ID from the pinned catalog. This is a selection
example, not a hosted-account test.

For a native API-key route, use `mode: "native"`, `name`, explicit `base_url`,
`model` and `api_key_env`. OpenCode names: `openrouter`, `openai`, `anthropic`,
`google`; Hermes names: `openrouter`, `openai-api`, `anthropic`. The endpoint must
speak the selected native protocol. OpenCode uses its bundled provider SDK;
Hermes uses its native provider and paired endpoint/credential environment.
These options do not import subscription/OAuth sessions. The advertised native routes now have local protocol-fixture coverage requiring
actual terminal-tool execution; OpenRouter is covered separately as well. Actual
hosted services remain unvalidated.

OpenCode compatible/native modes optionally accept `context_window` and
`max_tokens` (defaults 32768 and 4096); legacy local mode does not accept these
fields. Pi accepts corresponding configure flags. These describe client
limits, not a server reconfiguration. Hermes retains its own model/context
handling and does not accept these adapter fields. The adapters never download
models, restart inference services or change global server settings.

## Run your task and checks

Copy the chosen adapter config. Change `task` and `fixture` to your workload,
and declare the new config itself, task, pin, adapter/helper sources and all
fixture files in `inputs`. Keep declared paths inside the workspace. Schemas and
runtime flags remain adapter-specific.

Add an optional top-level `checks` field to the complete config (merge this
fragment; it is not a standalone config):

```json
{
  "checks": [
    {"id":"tests","argv":["python3","-B","-m","unittest","-v"],"timeout":60,"expected_exit":0}
  ]
}
```

Include `task_checks.py` in declared inputs. OpenCode/Hermes defaults include it;
Pi's repeatable `configure --check JSON_ARGV` does this for you. Check scripts
belong in the declared fixture/input set. Commands run as argv, without an
implicit shell, in the task workspace after capture. They have individual
timeouts and output limits and a separate environment without provider keys.
Check effects are recorded in `workspace.after_checks`.

Run the command for your owned workspace:

```sh
hwb-pi run your-config.json --pi ./runtime/node_modules/.bin/pi --timeout 600 --output task.result.json
```

```sh
hwb-opencode run your-config.json --opencode ./runtime/opencode --timeout 600 --output task.result.json
```

```sh
hwb-hermes run your-config.json --timeout 600 --output task.result.json
```

Choose NEW output paths for every attempt. The adapter exits nonzero if capture
or declared checks fail. Passing capture without checks makes no task-correctness
claim. The shipped `slugger.py` repair oracle cannot grade another workload.

### A small task you can adapt

First complete your adapter guide's installation and model selection. From that
owned workspace, set the config to copy (Pi's `configure` must already have run):

```sh
# Pi:
export HWB_TASK_CONFIG=model_adapter_config.json
# For OpenCode or Hermes instead:
# export HWB_TASK_CONFIG=adapter_config.json
```

Run this once in a fresh workspace. It creates task files and a new config;
it preserves the shipped repair. The exact example was subsequently run with
the local Qwen model: Pi and OpenCode passed; Hermes first timed out at 600
seconds, then passed a new attempt in 348.53 seconds with a 1200-second allowance.
Both outcomes remain retained. Those samples ran on Linux; the additional Mac
client observations below use the same task with a 1200-second bound. These are separate measurements from the earlier slugger repair, not a
guarantee for another model or task.

```sh
python3 - <<'PYTASK'
import json, os
from pathlib import Path
source = Path(os.environ["HWB_TASK_CONFIG"])
c = json.loads(source.read_text())
assert c["provider"]["mode"] != "scripted", "Select your model first"
fixture = Path("task_fixture")
fixture.mkdir()  # refuses reuse
(fixture / "total.py").write_text("def total(values):\n    return 0\n")
(fixture / "test_total.py").write_text(
    "import unittest\nfrom total import total\n\n"
    "class TotalTests(unittest.TestCase):\n"
    "    def test_empty(self): self.assertEqual(total([]), 0)\n"
    "    def test_signed(self): self.assertEqual(total([3, -1, 5]), 7)\n"
    "    def test_single(self): self.assertEqual(total([4]), 4)\n")
with Path("own_task.md").open("x") as f:
    f.write("Fix total.py so total(values) returns the sum of integer values. "
            "Work only in the supplied task workspace. Do not change tests. "
            "Run python3 -B -m unittest -v before and after the repair.\n")
old_fixture, old_task = c["fixture"], c["task"]
c["fixture"], c["task"] = str(fixture), "own_task.md"
c["inputs"] = [n for n in c["inputs"] if n not in (source.name, old_task)
               and not n.startswith(old_fixture + "/")]
c["inputs"] += ["own_task.json", "own_task.md", "task_fixture/total.py",
                "task_fixture/test_total.py"]
if "task_checks.py" not in c["inputs"]:
    c["inputs"].append("task_checks.py")
c["checks"] = [{"id": "unit-tests", "argv": ["python3", "-B", "-m", "unittest", "-v"],
                "timeout": 60, "expected_exit": 0}]
with Path("own_task.json").open("x") as f:
    f.write(json.dumps(c, indent=2) + "\n")
PYTASK
(cd task_fixture && python3 -B -m unittest -v)
```

The last command should fail two tests with exit 1. This establishes the
baseline; the generic `run` command does not enforce a failing baseline itself.
Use one command for your adapter. These custom-task examples now use a
1200-second bound to match the new Mac samples: OpenCode and Hermes took more
than 600 seconds. Choose a bounded allowance for your own model/task; one pass
does not establish reliable completion within either allowance. Earlier failed
attempts and their original bounds remain retained.

```sh
hwb-pi run own_task.json --pi ./runtime/node_modules/.bin/pi --timeout 1200 --output own-task.result.json
# Or:
hwb-opencode run own_task.json --opencode ./runtime/opencode --timeout 1200 --output own-task.result.json
# Or:
hwb-hermes run own_task.json --timeout 1200 --output own-task.result.json
```

Inspect the result without relying on the assistant's final answer:

```sh
python3 - <<'PYRESULT'
import json
from pathlib import Path
r = json.loads(Path("own-task.result.json").read_text())
for field in ("verdict", "task_checks", "process", "gateway_cleanup"):
    if field in r:
        print(field, json.dumps(r[field], indent=2))
subdir = "workspace" if "pi" in r else "fixture"
print("retained task files:", Path(r["workspace"]["retained_root"]) / subdir)
print("before:", r["workspace"]["before"])
print("after:", r["workspace"]["after"])
PYRESULT
```

Review the retained `workspace/total.py` (Pi) or `fixture/total.py` (OpenCode/
Hermes), unchanged tests, native command exits
and check output. The declared tests cover only three examples; add cases and
explicit file-change checks when your task requires stronger guarantees. Generic
checks are not the strict reference oracle and do not automatically enforce
“only implementation changed.” Checks run on the model-edited fixture, so a model
could alter a test there; compare manifests/bytes or use a trusted external
oracle when this matters. The original declared inputs must remain unchanged.

Replace the example with your own task by setting `task` to its instruction
file, `fixture` to its working directory and `inputs` to every file used by the
run, including adapter helpers and check scripts. Only fixture files are copied
to the working directory. Task instructions are supplied to the harness; other
declared inputs are hashed, not automatically copied into the fixture. Avoid
absolute paths to the input copy in task text: the retained fixture is where
edits and checks belong.

## Commands and evidence

| Command | Input | Meaning |
| --- | --- | --- |
| `init` | New directory | Materialize owned sources and fixtures |
| `doctor` | Workspace directory | Check runtime/config identity; no model request |
| `demo` | Workspace directory | Check the reference repair; Pi's default is scripted |
| `run` | Config JSON file | Capture your workload and execute optional declared checks |

Pi additionally has `configure DIR` to create its model-backed config. Runtime
selection flags are `--pi`, `--opencode` and Hermes' `--runtime`. A selected check
passes with exit `0`; a measured capture/outcome/check failure gives `1`; setup,
identity or launch failure gives `2`. Inspect JSON and stderr too.

| Result | Pi | OpenCode / Hermes |
| --- | --- | --- |
| Custom capture | `verdict`, `pi.summary`, `configuration.provider` | `verdict`, `summary`, `network.provider` |
| Independent custom checks | `task_checks.passed`, `task_checks.results` | Same |
| Reference demo outcome | `verdict`, `comparison`; capture under `adapter` | `outcome`; capture under `verdict` |
| Retained root | `workspace.retained_root`; demo uses `adapter.workspace.retained_root` | `workspace.retained_root` |

Read these four layers independently:

| Layer | Question answered | What a pass does not establish |
| --- | --- | --- |
| Capture validity (`verdict`) | Did the bounded invocation yield the required native stream, unchanged inputs and required process-group/observed-descendant/gateway cleanup? | Correct task output |
| Native tool outcomes | What did the harness report, and what exit did its shell tool retain? | That a completed tool exited 0, or that final prose is true |
| Independent checks (`task_checks`; reference demo `outcome`/`comparison`) | Did the declared commands or reference oracle accept the retained result? | Coverage beyond those checks |
| Sealed verification (`hwb verify`) | Does the outer record conform to its sealed evidence contract? | Model correctness or a passing inner task |

A failing native test followed by a passing one can be the intended repair
sequence. OpenCode's completed status is separate from `metadata.exit`; Hermes'
`is_error` is separate from decoded terminal exit; Pi retains native results
separately from reported status. Read both the native outcomes and independent
checks. The outer recorder's exit 0 alone is not an inner task pass.

Check results include actual exits, stdout/stderr, timeouts and cleanup outcomes.
The selected provider/model is recorded, not verified model weights. Native
error flags and shell exits remain separate. Hermes also records per-run gateway
cleanup. Raw emitted output is retained subject to bounds and credential
redaction; digests describe stored bytes. Truncated credential-bearing streams
fail closed rather than retaining a possibly exposed prefix.

Selected-key redaction protects captured diagnostic/output evidence. It does not
stop a trusted tool writing secrets to arbitrary files or sending them elsewhere.
Retained harness state and task data need review before sharing. Disposable homes
and process cleanup provide no OS/network sandbox.

## Seal a local custom task with the evidence bridge

The unreleased candidate adds `hwb-adapter spec`. Unlike the reference
`coding.json`, it generates an outer spec for the config/task you select.
This first bridge revision accepts scripted providers or unauthenticated HTTP
**loopback** endpoints. It rejects credential references and native hosted
providers; direct adapter commands retain their existing provider support.
A model on another local machine needs an explicitly arranged loopback tunnel
for this bridge. The generator does not create one or modify model services.

From an initialized workspace, after declaring your task, fixture and checks:

```sh
hwb-adapter spec opencode custom.json --launcher ./runtime/opencode --timeout 120 --features '[{"name":"freeze"},{"name":"receipt"}]' --output custom-experiment.json
hwb --root ./custom-runs run custom-experiment.json
hwb --root ./custom-runs show RUN_ID
hwb --root ./custom-runs verify RUN_ID
```

Replace `RUN_ID` with the run printed by `run`. For Pi, select `pi` and its
explicit pinned launcher; for Hermes, select `hermes` and pass the owned
`runtime/source` directory as `--launcher` (and `--uv` if needed). These names
are adapter selectors, not a promise that native controls are equivalent.
The generator checks the bridge envelope and declared paths. The adapter still
performs its own full config/runtime validation during execution. Current
bridge native-client qualification covers Pi and OpenCode on Linux; Hermes
native bridge runs are now measured on Linux. The earlier Pi artifact also
passed native bridge/control qualification on ARM64 macOS from source, wheel
and source archive; newer OpenCode/Hermes controls need separate qualification.

Generation writes three **new** sibling files: the outer spec, `.bridge.json`
request, and `.worker.py` snapshot. It refuses existing destinations. Keep all
three together. Requests bind declared input hashes and modes; after an
intentional input edit generate a new experiment name. Do not reset an existing
freeze baseline. An optional `--features` JSON list selects ordinary outer
features; the default is no outer features. Native retry is a separate control.

Each retry/sample receives a fresh `HWB_ATTEMPT_DIR` through the opt-in
`steps[].attempt_context: "hwb-attempt/v1"` contract. Other specs keep their
existing subprocess behavior. Context steps require a finite outer timeout;
their output is bounded at 8MiB stdout/1MiB stderr and observed descendant
cleanup is recorded. The worker stages only declared files, preserving modes,
and runs the adapter with a disposable home and a credential-free environment.
It compares staged/original input hashes and modes after execution. Checks run
through the adapter's existing separate check environment.

Inside `steps/STEP/attempts/N/adapter-evidence/`, inspect:

- `bridge-result.json`: separate capture/check/control observations and the
  attachment inventory; `task_success: null` means checks did not establish it.
- `inner-result.json` and `adapter-process.json`: native result and actual
  process exit, bounds, cleanup and bounded output. Missing/incomplete results
  fail capture; a failed check remains visible even if capture is valid.
- `inputs/`, `workspace/` and selected `native/` sidecars: copied declarations,
  independently manifested final fixture, streams and rendered runtime config.
  The final fixture must match the adapter's final after-check manifest.

These files live inside the outer integrity inventory, so later byte changes,
missing files and extra files are detected by `verify`. Verification establishes
sealed-record conformance, not task correctness. `hwb run` can complete with a
nonzero attempt; inspect the bridge/check verdicts. The worker exits nonzero
unless capture is valid **and** declared checks pass. Model-reported success is
not normalized by this bridge and remains a separate native observation.

Attachment selection is bounded to 64MiB (including a reserved 4MiB summary),
with 4,096 copied-file/entry bounds; declarations alone are limited to 32MiB.
Runtime homes, caches, accounts and databases are excluded. Unsafe links,
special files, missing/changed inputs, mismatched final manifests and oversized
attachments fail capture. Failed/partial attempts stay recorded; selected
partial fixtures/streams are collected when orderly worker teardown permits.
A forced kill can leave only an incomplete bundle; inspect outer process bounds
and cleanup, never infer success from missing evidence. Historical absolute
scratch paths in native JSON are provenance; use bundle-relative attachments
after scratch cleanup. Replay needs the declared original input files and
pinned native runtime, not just the retained result bundle.

Declared adapters/extensions/checks remain trusted executable code. Declared
file contents are retained verbatim: do not put secrets in task/input files.
Credential-free environments, process cleanup and native permissions are not
an OS sandbox or a guarantee against code reading an absolute host path.

### Versioned native-control request

`hwb-adapter capabilities pi` (or `opencode`/`hermes`) reports the pinned native
version and **implemented** control values. Pi exposes the narrow
`tool_call_extension` profile below; OpenCode exposes typed edit permission and
an owned native counter, and Hermes exposes selected toolsets and an owned patch
hook. An optional config field can reserve this
validated shape, with the matching harness:

```json
{"controls":{"schema":"hwb-native-controls/v1","harness":"pi","values":{}}}
```

Omitting it keeps legacy behavior. Null, unknown schema/harness, extra keys and
unsupported `values` fail before native execution. The Pi profile below supports
a declared hook alongside a specific local provider; it does not expose the full
Pi SDK or arbitrary OpenCode/Hermes plugins and custom configuration. Existing legacy
controls retain their existing limits.
Requested settings, rendered overrides, activation and effect are separate
fields. Empty overrides label legacy defaults; activation remains
`not_measured` and effect `not_evaluated`. Discovering a native setting or
emitting a configuration is not evidence that a control activated or worked.
The Pi profile below now measures one native hook through this bridge; sessions,
memory and broad campaign qualification remain later work.



### Pi: a declared local tool-call extension

The first supported control is `controls.values.tool_call_extension` in the
Pi1.4.0/Node26.13.0 profile. It supports unauthenticated HTTP loopback Chat
Completions only. Keep the exact `pi_arguments` generated by `hwb-pi configure`,
with an empty legacy `extensions` list. Provider/auth/model flags, evidence
flags and alternate tool/session/settings arguments cannot be added to this
profile. Other existing direct configurations retain their behavior.

A hook is a declared ES module with this **narrow** interface:

```javascript
export default async function (event, settings) {
  if (event.toolName === "edit") {
    return {block: settings.block, reason: "Declared edit experiment"};
  }
}
```

It receives a deeply frozen copy of the native tool event and settings, not Pi's
provider/session/tool-registration API. Return undefined to allow, or an object
containing boolean `block` and optional string `reason`. Invalid returns,
exceptions and asynchronous deadlines fail closed and invalidate capture.
`timeout_ms` is1..5000; synchronous CPU loops cannot be preempted by a JavaScript
timer, so the outer native-process deadline remains necessary. A timer does not
cancel an already running asynchronous operation; failed capture and owned
process cleanup remain required. No API allows
the hook to replace Workbench's recording mode or selected provider by accident.
Trusted executable code still has Node builtins and filesystem access; this is
not an OS sandbox or protection against deliberately malicious code.

Merge the following into your complete config, using actual file hashes and an
existing regular fixture file as `expect.path`:

```json
{
  "controls": {
    "schema": "hwb-native-controls/v1",
    "harness": "pi",
    "values": {
      "tool_call_extension": {
        "entry": "guard.mjs",
        "files": {"guard.mjs": "REPLACE_WITH_LOWERCASE_SHA256"},
        "settings": {"block": true},
        "expect": {"tool": "edit", "path": "slugger.py", "effect": "unchanged"},
        "timeout_ms": 1000
      }
    }
  }
}
```

The placeholder is deliberately invalid; compute it from the exact file bytes.
Declare `guard.mjs`, every dependency, `control_runtime.py` and
`tool_call_control.mjs` in top-level `inputs`. `files` binds the entry and up to64
`.mjs`/`.json` files with lowercase SHA256 digests. Bare-package imports,
undeclared local module imports, links, package.json/node_modules profiles and
lazy installation are unsupported. Normal module imports from the owned tree
resolve only declared files or `node:` builtins. This is not a general check on
filesystem reads made by trusted code. There is no npm dependency provisioning
or full TypeScript/native Pi extension API in this first profile.

Inputs are verified before native execution, then copied to an owned control
source tree. The wrapper and rendered configuration are hash-bound and checked
again afterward, as is the selected provider configuration. Assistant events
must report the selected provider/model. A load marker alone establishes neither
a tool attempt nor a useful effect: the effect oracle matches hook decisions
with native call IDs, tool names, paths and native error results, and compares
independently collected target hashes. Checks must preserve the target afterward.

For baseline set `settings.block` false and `expect.effect` changed; for enabled
set block true and effect unchanged. Keep task/model/fixture/checks identical.
An inversion keeps the enabled expectation but breaks the declared hook or its
dependency, updating its digest deliberately. Generate a NEW outer spec for
each case with `hwb-adapter spec pi ...`; never reset an older freeze baseline.
Native read/bash tools remain available. Blocking edit does not prevent a model
from using another tool to change the file: that makes the unchanged-file
oracle fail and is evidence of the limitation, not a containment success.

Inspect inner `controls.requested`, `rendered`, `activation` and `effect`
separately. The bridge copies these observations and the effect verdict to
`experiment`; `task_success` still comes only from independent task checks.
A successful denial may have valid capture, passing experiment and failing task.
An inverted hook can have valid capture and passing task but failing experiment.
Both produce nonzero adapter/worker exits; outer recorder completion and sealed
verification remain separate. Missing attempts or missing evidence never count
as a control pass. `native/control.trace.jsonl`, wrapper/config/source copies,
inner results and retained fixture bytes are sealed in the outer record.

Local native-client qualification covers baseline, enabled, inverted dependency,
load error, undeclared import, asynchronous timeout, thrown error, invalid result,
corrupt stdout and synchronous-hang cleanup. The experiment changes only target.txt in the tiny
retained task; original inputs are checked independently. This protocol fixture
is not a hosted account test or model-quality measurement. Local Qwen confirmation
is reported separately in the implementation plan and retained assurance bundle.
The earlier Pi artifact passed Mac ARM64 source/wheel/source-archive gates.
Hermes now has Linux native bridge observations. Broader native-feature/controller
campaign qualification remains separate from these direct control oracles.


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

## Optional scripted control and sealed records

The scripted provider chooses planned calls while the real harness executes its
tools. Use the default `demo` to diagnose integration problems without model
variation or account access. OpenCode/Hermes can also apply the reference repair
oracle to a selected model. A scripted pass is not a model-quality measurement.

The supplied `coding.json` seals the reference repair. Follow the guide's runtime
PATH setup, run `hwb run coding.json`, then `hwb verify RUN_ID`. For your own task,
create a spec invoking your config and declaring its inputs, using that spec as
an example. Check outer recording completion, sealed conformance and inner
capture/task outcomes separately. Set runtime overrides before the first freeze;
use a new owned setup when the baseline changes. The shipped `coding.json`
still invokes the reference demo; it does not automatically select
`own_task.json`. For a custom spec, replace the step argv with your adapter's
`run own_task.json ...` command, declare the config's full input list in that
step, and give `step_timeout_ms` enough time for the adapter deadline plus checks
and cleanup. Keep `freeze` and `receipt` features from the shipped example.
`RUN_ID` is the ID printed by `hwb run`, not a literal value to type.

## Troubleshooting and validation status

| Symptom | Next step |
| --- | --- |
| Command missing | Activate the environment and install this working checkout |
| Identity mismatch | Use the exact pinned runtime/lock in a new workspace; preserve the failed result |
| Credential missing | Set the explicitly named variable in the invoking process |
| HTTP 401 / auth failure | Check selected endpoint/provider/key; no unrelated-account fallback is intended |
| Unsupported protocol / tools never execute | Match the selected API and endpoint path; confirm tool calls work with the actual harness. A text-only reply is insufficient |
| Context exceeded / truncated results | Check server context, client output budget and accumulated tool output; reduce task size or select sufficient capacity |
| Model edits wrong workspace | Inspect retained manifests and input hashes; use relative task paths. Hermes `--in` and workspace instructions guide execution but cannot contain it |
| Cleanup failure | Inspect process/group, OpenCode `process.descendant_cleanup` and Hermes `gateway_cleanup` evidence before retrying. Do not stop unrelated services; retain the failed record |
| Timeout | Preserve partial effects and cleanup evidence; retry with a justified bound and NEW result |
| Capture passes, checks fail | Read each independent check's actual exit/output and working files |

### Measured local evidence · 2026-10-31

All three adapters passed the local Qwen 27B reference repair at an advertised
65,536-token context. Strict success required a failing independent baseline,
native test exits `[1, 0]`, only `slugger.py` changed in the retained fixture,
passing independent final tests and successful process/gateway cleanup.

The complete suite passed **458 tests with no skips**. Nine native API routes
passed authenticated local protocol fixtures using actual harness clients and
native terminal tools: Pi's four compatible APIs, OpenCode's OpenAI/Anthropic/
Google SDK routes and Hermes' OpenAI/Anthropic routes. OpenRouter and compatible
connections have separate coverage. Packaging, clean wheel/source installs,
sealed-record checks and source/expanded-artifact secret scans passed. These
are local working-tree snapshots, not released packages.

Earlier failed Hermes attempts remain retained. One edited the input copy
outside the retained fixture and reported passing native tests; unchanged
retained files, input hashes and failed independent checks correctly rejected
it. The final corrected run passed. This is why native test reports alone are
insufficient. Explicit `--in` and retained-workspace query guidance improve
workspace targeting without imposing filesystem isolation.

The full suite preceded the final Hermes query clarification; that changed
path then passed source and final installed-wheel regressions. Actual hosted
API-key accounts and current-candidate hosted CI remain untested. Subsequent
macOS 26.5 arm64 checks are recorded below.
OAuth/subscription/saved-profile authentication is intentionally excluded by the
current adapter contract; upstream account support does not make it an adapter
option. Protocol fixtures cannot prove hosted account access, model
quality or universal compatibility. The [implementation plan](model-provider-plan.md)
records the evidence and remaining validation; the assurance report and model
results are in the supplied local `adapter-assurance/` evidence bundle.

For an issue include the Workbench commit, adapter/profile, runtime versions,
command, redacted config and relevant result/errors. Review files for credentials
and private task data before sharing. See [SUPPORT.md](../SUPPORT.md).

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
even when a check exits successfully. Pi records final hashes as
`configuration.input_digests_after`; OpenCode/Hermes use `inputs_after`.

The retained 458-test/package/model assurance bundle predates these review fixes.
It remains historical evidence and does not establish byte identity or native
runtime validation for the revised adapters. Review-fix regressions exercise
credential isolation/redaction, legacy migration and post-check input integrity
with native launches mocked; no new model experiment is implied.

## Follow-up validation and access boundaries

Current-source and freshly installed-wheel tests again passed all nine native
API routes using authenticated local fixtures and actual terminal tools. The
current default suite ran 464 tests (451 passed, 13 optional tests skipped);
that is separate from the historical 458/no-skip assurance result. A forced
Hermes deadline passed owned-gateway cleanup with the frozen Anthropic profile.
The older core-only runtime was correctly rejected before native execution;
initial socket-denied and runtime-mismatch attempts remain retained.

Fresh Linux wheel and source-distribution installs passed offline import,
metadata, CLI, offline-subject and sealed hello-record verification with the
retained pinned build toolchain. Final packaged adapter assets matched source.
Those initial checks reused native runtimes. Subsequent local work below adds
fresh Linux installations and uncached backend resolution. Subsequent macOS
checks are recorded below; the formal release gate remains separate.

The exact documented `total(values)` task now has model-backed observations:

| Adapter | Native test observations | Independent retained result |
| --- | --- | --- |
| Pi | `[1, 0]` | Passed; only `total.py` changed, tests and original inputs unchanged, cleanup passed |
| OpenCode | `[1, null, 0]` | Passed after a denied command with a mistyped working directory; only `total.py` changed, tests/inputs unchanged, cleanup passed |
| Hermes, initial full-profile attempt | No native tests | Failed at 600 seconds after search/read calls; no edits, final tests failed, inputs unchanged, owned gateway stopped |
| Hermes, new attempt / 1200-second allowance | `[1, 0]` | Passed in 348.53 seconds; only `total.py` changed, tests/inputs unchanged, independent tests and process/gateway cleanup passed |

Every baseline independently exited 1 with two failures. OpenCode's `null` is a
denied call with no observed shell exit; its executed native tests exited 1 then
0. Hermes was waiting for the third local model response when terminated; this
is a failed bounded task attempt, not a successful custom-task integration.
The separately retained new attempt passed below the old deadline. Faster model
responses and different tool choices mean the larger allowance cannot be credited
as its cause, or treated as proof that 600 seconds reliably suffices. No adapter
or model-service change was made. The earlier strict slugger success also remains
valid historical evidence. These direct task runs did not produce outer sealed
records, establish universal compatibility or prove an adapter defect.

Fresh Linux installations now passed for all three native runtimes, followed by
their actual scripted reference repairs: Pi used a copied package-only offline
npm cache, while unchanged OpenCode/Hermes installers fetched their pinned
official assets/source into new owned workspaces. Doctors, retained-file/input
checks and cleanup passed. Node was already available, not freshly downloaded.
Uncached source builds resolved setuptools 84.2.1, and that clean-installed wheel
passed all three help/init/doctor/scripted-repair workflows with source-identical
initialized adapter bytes. The newer backend's subject executable modes still
fail release expectations. The earlier release-backend pin was setuptools
83.0.0; the newer clean Mac failure below shows that pin alone is insufficient.

Local Python 3.11–3.13 source cells each passed 464 tests with 13 optional skips
and 251 offline-subject tests without skips. After a synthetic secret fixture
was generated at runtime, six focused regressions passed on Python 3.11–3.14;
draft source/artifact secret scans and a 140-commit history scan passed without
allowlist changes. Initial scan/mode failures remain evidence. These preflight
results concern development snapshots, not pristine committed release bytes.

No selected hosted provider API-key reference or spending authorization was
available. To test a hosted route, explicitly select its provider/model/endpoint,
supply a private named key reference and authorize a bounded potentially paid
run. OAuth/subscription/saved-profile support would need a separate auth contract
and native refresh/write-back/isolation tests; it cannot be enabled by copying
an account file or treating an OAuth token as an API key.

Read-only hosted results confirm earlier successful Linux/macOS jobs at base
`c83e25d`, not the current working tree. Exact-candidate hosted CI remains a
separate gate; no CI dispatch occurred.

Authorized macOS validation used a new owned temporary workspace on macOS 26.5
arm64 and a hashed 355-file candidate snapshot. Fresh pinned native installations
passed: Pi 1.4.0 with a newly downloaded checksum-verified Node 26.13.0, OpenCode
1.19.5, and Hermes 0.22.5 with its frozen Anthropic 0.87.0 profile. Source suites
on Python 3.11.15 and 3.14.7 each ran 464 tests: 463 passed and one historical
core-only Hermes runtime test was skipped because that old runtime was absent.
A clean installed wheel built with setuptools 83.0.0 passed all nine authenticated
local native protocol routes and all three scripted reference repairs. These
fixtures exercise actual native tools without hosted accounts or model inference.
Each Python cell also passed 251 offline-subject tests without skips. Installed
negative checks preserved capture validity while recording actual check exit 7
and CLI exit 1 for all three adapters. A real 45-second installed Hermes timeout
returned CLI exit 1, invalid capture and successful owned-process/gateway cleanup.
These are distinct outcomes; the fixture runs do not establish sealed-record
conformance for arbitrary tasks. Those fixture tests alone do not establish model task outcomes, x86_64 compatibility or
current hosted-CI success. Commands, snapshot hashes, logs and setup failures
are retained in the local `macos-validation/` evidence bundle. Workspace
instructions and owned-process cleanup do not provide an OS sandbox.

Further installed ARM64 Mac runs used the same documented task and the existing
Linux Qwen 27B service through a temporary loopback-only SSH tunnel, one fresh
1200-second-bounded attempt per adapter:

| Adapter | Actual native unit-test exits | Elapsed seconds | Independently inspected result |
| --- | --- | --- | --- |
| Pi | `[1, 0, 0]` | 463.60 | Passed; the extra passing test remains recorded |
| OpenCode | `[1, 0]` | 752.71 | Passed; actual exits extracted from native `metadata.exit` |
| Hermes | `[1, 0]` | 1102.12 | Passed; recorded owned gateway also independently confirmed stopped |

Each external baseline failed with exit 1, each final independent check passed,
only `total.py` changed, and original inputs/tests stayed unchanged. Each retained
implementation additionally passed 100 deterministic integer-list cases. Pi's
exact `[1, 0]` sequence flag remains false; its independently verified repair
outcome is separate. The initial OpenCode result reader expected the wrong exit
field and failed after the successful run; corrected independent post-processing
is retained alongside that failure, with no model rerun. These are finite task
samples using Mac clients and Linux inference, not hosted access, Mac inference
benchmarks, universal compatibility or sealed model-task conformance. Recorded
normal process-group cleanup passed; cancellation supervision is evaluated separately below.
The owned tunnel was closed, frozen source hashes remained intact and global
Mac runtime versions matched the initial inventory.

Further ARM64 Mac installation tests passed with the normalized source archive.
A clean pinned-backend install used explicitly provisioned setuptools 83.0.0,
then built/installed offline with no build isolation; all three help/init/doctor
and scripted repair workflows passed, and 51 adapter assets matched source,
archive, wheel, installed and initialized copies. Raw and normalized source
archives and that pinned installation preserved both subject executables as
0755. A separate ordinary isolated pip install from the source archive selected
setuptools 84.2.1 and passed the same adapter workflows. Its generated wheel and
installed subject files had mode 0644 for `hook.py` and `runner.py`; the release
artifact verifier correctly failed. This confirms the release-backend limitation
on Mac too. Direct default-isolation installation from a checkout was not run in
this Mac phase. The initial missing-cache prerequisite failure remains retained.


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

Earlier cancellation probes found ordinary OpenCode native tests surviving a
20-second adapter deadline in separate sessions/groups on Mac and Linux. The
native-client group had stopped; audits retained each failure before stopping
only the independently matched owned survivor. Those failures remain evidence.

The current OpenCode adapter opts into bounded observation and cleanup of owned
descendants using parent lineage and kernel birth identity. Linux uses pidfds
when available; Darwin's birth recheck and signal are not atomic. Observation
errors, unknown identities and surviving recorded children invalidate capture.
Darwin may hide an initial unreaped zombie; an unavailable initial identity
fails setup even if the native process actually exited 0. Polling can miss
fork/reparent activity between samples, so this is not an OS sandbox or a claim
to discover every descendant. Read `process.descendant_cleanup` as well as
process-group evidence.

Linux source and clean installed-wheel native regressions passed the blocked
tool timeout and normal-exit/lingering-detached-tool cases. Mac source native
regressions passed both; Mac clean wheel and source-archive installed native regressions also passed.
These functional results do not establish release-artifact conformance.
Independent kernel audits confirmed recorded child identities gone, an unrelated
sentinel alive and unchanged original inputs. Pi/Hermes earlier native-timeout
probes, all three adapters' independent-check timeouts and Hermes' native SIGINT
case retain their original scope. Successful normal model tasks are separate
from this cancellation evidence. Commands, failures and audits are retained
under the local `outputs/opencode-cleanup-fix/` evidence bundle.

The [existing v0.1.0 release](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0)
predates these adapters. Local development artifacts still report 0.1.0; do not
use that public version as installation instructions for this work. An unused
version and fresh exact-candidate release/platform/artifact gates are required.
The [implementation plan](model-provider-plan.md#remaining-validation-and-documentation-gaps)
records the concrete blocked actions and remaining installation limits. Commands,
results, failures and source hashes are retained in the local `validation-gaps/`
evidence bundle; these observations do not promote it to release evidence.


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
