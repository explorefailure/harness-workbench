# Pi builder integration — release candidate

This README is copied into owned workspaces. The shared builder guide lives in
`docs/builder-adapters.md` in the Workbench checkout. Repository guide:
[builder adapters](https://github.com/explorefailure/harness-workbench/blob/main/docs/builder-adapters.md).
These unreleased changes may not yet be present on the remote main branch; use
the working checkout documentation as the matching reference.

This candidate turns the existing Pi adapter into an owned builder workflow.
It is not yet a published release. The default profile is Pi 1.4.0 with Node
26.13.0 with locked minimatch 11.0.4 and undici 8.12.3 overrides. Linux and macOS are the target
platforms; verify the exact candidate's hosted checks before release.

## Choose your model after installation

Pi 1.4.0 can now use its native `models.json` and catalog providers without a
custom provider extension. After completing the installation below, run these commands from the owned
workspace. Replace the endpoint/model placeholders with your running server:

```sh
hwb-pi configure . --base-url http://127.0.0.1:11434/v1 --model YOUR_MODEL_ID --context-window 65536 --check '["python3", "-B", "-m", "unittest", "-v"]'
hwb-pi run model_adapter_config.json --pi ./runtime/node_modules/.bin/pi --timeout 600 --output model.result.json
```

For an authenticated endpoint add `--api-key-env MY_MODEL_KEY`. Set its value
outside the config through your shell/secret manager. For a Pi catalog provider
use `--provider PROVIDER_ID --model MODEL_ID --api-key-env MY_MODEL_KEY`; an
explicit proxy `--base-url` is optional. Native providers use catalog API/context
metadata. Compatible APIs default to `openai-completions`; other accepted APIs
are `openai-responses`, `anthropic-messages` and `google-generative-ai`. All four
have authenticated local native-tool protocol-fixture coverage. Only Pi 1.4.0 supports this new config path.

`configure` writes a NEW config, leaving the scripted repair unchanged. Use
`--config-name NEW_NAME.json` to keep another setup. It does not contact a model
or change the server. `--offline` disables automatic catalog/network activity;
explicit inference requests still run. Compatible context/output metadata does
not reconfigure server context. Use `--max-tokens` to change its output limit.

The config's optional `provider` field selects mode `compatible` (base_url,
model, optional api_key_env/api/context_window/max_tokens) or `native` (name,
model, api_key_env, optional base_url). With this field, legacy `extensions` and competing
provider/model/key flags in pi_arguments are rejected. The declared
`controls.values.tool_call_extension` profile below is the supported local hook path. Declare adapter.py,
normalizer.py and provider_config.py along with the config/task/fixture inputs.

Repeatable `--check JSON_ARGV` adds independently bounded task checks and declares
task_checks.py. Inspect `task_checks.passed` separately from `verdict`; either
failure gives a nonzero adapter exit. Check commands run without provider keys.
See the [shared guide](https://github.com/explorefailure/harness-workbench/blob/main/docs/builder-adapters.md#run-your-task-and-checks) for the full
check schema. The default `demo` remains the optional scripted reference control.

Local fixtures validate compatible authentication, a catalog OpenRouter route,
credential redaction, auth failure and independent checks. Pi passed the strict
Qwen 27B repair: failing baseline, native exits [1, 0], only slugger.py changed,
independent tests passed and cleanup succeeded. Actual hosted accounts remain
unvalidated. Fresh ARM64 Mac checks are described under local platform
validation below. OAuth/saved-account import is outside this API-key path.


## Install and run the first repair

Put Node 26.13.0 on PATH. From this reviewed Workbench candidate checkout:

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
hwb-pi init ./my-pi-workspace
cd my-pi-workspace
npm ci --prefix runtime --ignore-scripts
hwb-pi doctor . --pi ./runtime/node_modules/.bin/pi
hwb-pi demo . --pi ./runtime/node_modules/.bin/pi --output repair.result.json
```

Pi and Node are optional external executables. Workbench keeps zero Python
runtime dependencies. Initialization does not download anything or change global
software; npm ci is an explicit separate step using the supplied lock. Keep the
virtual environment active so commands use the installed candidate.

The demo uses Pi's actual read/edit/bash tools with an offline scripted provider.
It reproduces failing tests, edits the implementation, reruns the tests and
checks the exact outcome independently. It does not measure live-model reasoning.

After installation, use [model selection](#choose-your-model-after-installation)
and then [your own workload](#bring-your-own-workload). The scripted demo above
is an optional integration diagnostic.

## Commands and verdicts

- `hwb-pi init DIR`: create a NEW owned workspace. Existing directories and
  symlinks are rejected; there is no merge or force mode.
- `hwb-pi doctor DIR --pi LAUNCHER`: check exact Pi/Node and installed identity
  without running the task. This does not certify a workload configuration.
- `hwb-pi demo DIR --pi LAUNCHER`: run the reference repair and its outcome oracle.
- `hwb-pi run CONFIG --pi LAUNCHER`: validate provenance and capture a custom task.
  Successful capture alone does not establish correct task output.

Exit 0 means the requested check passed; 1 means a completed capture/outcome check
failed; 2 means configuration, runtime identity or launch failed. With --output,
JSON goes into a NEW owner-only file. Existing evidence is never overwritten.
Diagnostics may also appear on stderr. --timeout must be finite and positive;
--workspace-parent selects an existing directory for retained run roots.

Inspect verdict, adapter.verdict, comparison and
adapter.workspace.retained_root in the demo JSON. The comparison names its
elapsed-time mask. Raw output and result hashes remain retained. Run roots are
not auto-deleted: preserve the evidence you need before cleaning up.

## Bring your own workload

Follow [the shared runnable task walkthrough](https://github.com/explorefailure/harness-workbench/blob/main/docs/builder-adapters.md#a-small-task-you-can-adapt)
with `HWB_TASK_CONFIG=model_adapter_config.json` after running `configure`.
It supplies new files, declares checks and runs `own_task.json`.

For another model-backed workload, copy `model_adapter_config.json` to
`your-config.json` and change its task and fixture. Keep `extensions` empty;
provider selection belongs in `provider`, not `pi_arguments`. Update inputs to include the new
config itself, pin, task, every fixture file and every extension. Paths must be
relative files within the owned workspace; escaping paths fail before execution.

```sh
hwb-pi run your-config.json --pi ./runtime/node_modules/.bin/pi --output custom.result.json
```

Do not reuse the demo's coding oracle for another task. Add independent checks
for your declared outcomes. The private Pi configuration does not copy saved
credentials; saved-account/OAuth authentication remains outside this API-key path.
Use explicit provider selection and declared checks for model-backed tasks.

For a sealed outer Workbench record, expose the owned Pi launcher on PATH:

```sh
export PATH="$PWD/runtime/node_modules/.bin:$PATH"
hwb run coding.json
hwb verify RUN_ID
```

Also inspect the inner step exit and verdict. hwb run exit 0 only means the outer
recorder completed. Do not reset a freeze baseline to conceal changed inputs;
start a new owned workspace for a new setup.

## Local platform validation

The Mac validation used fresh Pi 1.4.0 with Node 26.13.0 and the locked
undici 8.12.3 override.

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
One fresh attempt with a 1200-second bound completed in 463.60 seconds; native
unit-test exits were `[1, 0, 0]`. The external baseline exited 1, final independent
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
The extra passing native test is retained; the earlier exact `[1, 0]` sequence
flag stays false, separately from the independently verified repair outcome.

## Compatibility and dependency contract

The Pi/Node pair, bundled launcher, Pi-owned tree, metadata and installation lock
are pinned. Pi 1.4.0 uses dist/bundle/cli.js and pin schema pi-hwb-pin/v0.2. npm ci
checks package integrity; the adapter hashes the lock and Pi-owned files, not
every installed dependency byte. This is not continuous dependency-tamper
monitoring. Do not use npm update to bypass identity failures.

The root overrides upgrade external minimatch to 11.0.4 and undici to 8.12.3
without modifying Pi 1.4.0. A fresh install and npm audit on 2026-11-04 reported
zero vulnerabilities. Hosted
newer-profile jobs repeat that audit. This is a dated check, not a promise that
future advisories cannot appear. A loopback gzip-response test exercises Pi's
actual HTTP dispatcher against the patched dependency.

Pi 0.84.1 / Node 22.22.3 remains available with `--profile baseline` solely as a
historical comparison. Its unchanged dependency graph has known advisories and
is not the recommended builder runtime. Do not treat passing baseline scenarios
as a dependency-health claim or install that profile for new work.

The source-owned repair/rewrite/branch oracles preserve both final reported
status and native retained results. Pi 1.4.0 can retain an embedded isError:true
while a hook changes the final reported status to false. The comparison binds
raw results to their summary and masks only valid nonnegative bash elapsed time.
It still rejects changed exit codes, fabricated output, missing positive controls
and inconsistent durable effects. Nine targeted scenarios have been exercised
on Node 22.22.3, 22.23.4, 24.22.2 and 26.13.0; only the named default pair is the
new builder profile. The larger matrix is bounded experimental evidence.

The CLI is the supported entry point, not a public Python adapter API or a
universal cross-harness schema. Config/run schemas remain v0.1 for this candidate;
breaking changes require a schema revision. Upgrade into a fresh owned directory.
Report the candidate commit, profile, exact runtimes and redacted reproducer.

## Execution boundary

Run only trusted configurations, commands, fixtures and extensions. Pi and the
adapter are not a sandbox. Disposable directories do not provide OS isolation.
Handled termination cleans the owned POSIX process group; escaped sessions or
unhandleable kills can survive. RPC, resumed sessions, arbitrary live-model
quality and hostile-code/network containment are outside this milestone.

The demo normalizes shipped modes to 0644 (shell launchers 0755), uses umask 022
and disables Python bytecode for its test command. It still rejects unexpected
files and permission changes. Custom workloads retain their own semantics.


## Local custom-task evidence bridge

The unreleased candidate also provides `hwb-adapter spec HARNESS CONFIG
--launcher OWNED_RUNTIME --output NEW_SPEC.json`. Use `pi` as `HARNESS`;
for Hermes the launcher argument is the owned source directory, while Pi and
OpenCode take their pinned executable. It stages declared task inputs afresh
for each outer retry/sample and retains selected results/fixture files inside
the sealed record. Configure independent checks and inspect their verdicts;
outer completion and sealed verification do not establish task success.

This bridge revision accepts scripted or unauthenticated HTTP loopback routes
only. Native-client bridge qualification currently covers Pi/OpenCode on Linux;
Hermes native bridge execution is now measured on Linux. The earlier Pi
artifact passed Mac ARM64 source/wheel/source-archive bridge/control gates;
newer control changes need separate platform qualification. Earlier direct
adapter/model/platform evidence remains separate. `hwb-adapter capabilities
pi` reports the new control contract: the local `tool_call_extension` profile is
implemented; other native controls remain unsupported. Unsupported values fail clearly; defaults remain compatible.

See the repository's `docs/builder-adapters.md` shared guide's “Seal a local custom task with the
evidence bridge” section for generation, budgets, retained paths and limitations.
Declared code is trusted; the bridge is not an OS sandbox.


## Declared local tool-call extension

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
