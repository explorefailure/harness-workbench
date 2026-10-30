# Pi builder workspace

This candidate turns the existing Pi adapter into an owned builder workflow.
It is not yet a published release. The default profile is Pi 1.4.0 with Node
26.13.0 and a locked undici 8.12.3 override. Linux and macOS are the target
platforms; verify the exact candidate's hosted checks before release.

## Install and run the first repair

Put Node 26.13.0 on PATH. From this reviewed Workbench candidate checkout:

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

Copy coding_adapter_config.json to your-config.json. Change its task, fixture,
extensions, provider, tools and system prompt. Update inputs to include the new
config itself, pin, task, every fixture file and every extension. Paths must be
relative files within the owned workspace; escaping paths fail before execution.

```sh
hwb-pi run your-config.json --pi ./runtime/node_modules/.bin/pi --output custom.result.json
```

Do not reuse the demo's coding oracle for another task. Add independent checks
for your declared outcomes. The private Pi configuration does not copy saved
credentials; live-provider authentication and outcome evaluation require a
separate integration design.

For a sealed outer Workbench record, expose the owned Pi launcher on PATH:

```sh
export PATH="$PWD/runtime/node_modules/.bin:$PATH"
hwb run coding.json
hwb verify RUN_ID
```

Also inspect the inner step exit and verdict. hwb run exit 0 only means the outer
recorder completed. Do not reset a freeze baseline to conceal changed inputs;
start a new owned workspace for a new setup.

## Compatibility and dependency contract

The Pi/Node pair, bundled launcher, Pi-owned tree, metadata and installation lock
are pinned. Pi 1.4.0 uses dist/bundle/cli.js and pin schema pi-hwb-pin/v0.2. npm ci
checks package integrity; the adapter hashes the lock and Pi-owned files, not
every installed dependency byte. This is not continuous dependency-tamper
monitoring. Do not use npm update to bypass identity failures.

The root override upgrades external undici to 8.12.3 without modifying Pi 1.4.0.
A fresh install and npm audit on 2026-10-30 reported zero vulnerabilities. Hosted
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
