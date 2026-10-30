# Pi builder integration — release candidate

The builder CLI ships an owned adapter and deterministic repair fixture. This
change is a release candidate, not an already published support promise.

Install from the candidate checkout with `python3 -m pip install .`, then:

```
hwb-pi init ./my-pi-workspace
cd my-pi-workspace
```

Pi and Node are optional external executables; ordinary Workbench installation
still has zero runtime Python dependencies. Install the exact runtime declared by
pin.json separately. No automatic downloads, global upgrades or credential copies
are performed by hwb-pi.

The supported command contract is init/doctor/demo/run and the exit statuses:
0 = successful requested check; 1 = completed capture/oracle with a failed verdict;
2 = invalid configuration, missing runtime, identity mismatch or launch failure.
With --output, JSON is written to a new owner-only file; diagnostics may appear on
stderr. Never interpret doctor success as an outcome verdict.

Scope: POSIX JSON mode, Python 3.11+, exact pinned Pi/Node artifacts. Newer-version
matrix findings are separate candidate evidence until their configuration and full
tests are reviewed. No RPC, interactive TUI or resumed-session support is implied.
No public Python adapter API or universal cross-harness schema is promoted.
Keep pi-hwb-adapter-config/v0.1 and pi-hwb-adapter-run/v0.1 fields stable for this
candidate; breaking changes require a schema revision. Pi-specific event mappings
are tied to the named runtime. Reinitialize into a new directory to upgrade;
existing owned workspaces are never overwritten. Bug reports should include the
integration version, runtime identities and a redacted reproducer, not secrets.

With Pi 0.84.1 and Node 22.22.3 on PATH:

```
hwb-pi doctor .
hwb-pi demo . --output repair.result.json
```

The demo uses Pi's actual read/edit/bash tools with a scripted offline provider.
It does not measure a live model's ability. Exit 0 requires both capture and the
independent exact repair oracle. Inspect `verdict`, `adapter.verdict`,
`comparison`, and `adapter.workspace.retained_root` in the JSON.

Use a NEW output filename for every run. Retained roots are not auto-deleted.
`doctor` only checks runtime identity; it does not execute task/extensions or
certify a config. `run CONFIG` performs the full provenance checks and capture.

To attach your own workload, copy coding_adapter_config.json to your-config.json,
change task/fixture/extensions/provider/tools/system prompt, and update inputs:
include your config itself, pin, task, every fixture file and every extension.
Inputs are relative files within this directory; absolute/escaping paths fail.
Do not use the demo's coding oracle for a different task. `hwb-pi run` exit 0 means
valid capture, not correct output. Add independent checks for your own outcomes.
The adapter's private Pi configuration does not import saved credentials. Live
provider authentication/environment integration is outside this first workflow.

For a sealed Workbench run, `hwb run coding.json`, followed by `hwb verify ID`.
Inspect the step exit status as well: hwb run exit 0 means the outer recorder
completed, even if the inner workload failed. Never edit an existing freeze
baseline to conceal changed inputs; use a new owned workspace for a new setup.

Only execute trusted workspace scripts/configs/extensions. Pi and this adapter
are not a sandbox. A disposable directory is not OS isolation. Handled timeouts
clean up the owned POSIX process group; escaped sessions and unhandleable kills
can survive. No network/hostile-code containment or session-resume guarantee.

The demo normalizes shipped file modes (0644, shell launchers 0755), uses umask
022 and disables Python bytecode in its test command. It still rejects unexpected
files or permission changes. Custom workloads retain their own semantics.


## Newer runtime candidate: Pi 1.4.0 / Node 26.13.0

Put Node 26.13.0 on PATH, then create a separate owned workspace:

```
hwb-pi init ./pi-latest --profile pi-1.4.0-node-26.13.0
cd pi-latest
npm ci --prefix runtime --ignore-scripts
hwb-pi doctor . --pi ./runtime/node_modules/.bin/pi
hwb-pi demo . --pi ./runtime/node_modules/.bin/pi --output repair.result.json
```

The committed runtime/package-lock.json binds the optional npm dependency graph.
The bundled launcher, Pi-owned tree, package metadata and exact Node version are
also pinned. Do not use npm update or regenerate the lock as a workaround for an
identity mismatch. This profile uses pin schema pi-hwb-pin/v0.2 because Pi 1.4.0
uses dist/bundle/cli.js and no longer includes the old package shrinkwrap.
The runtime check hashes this local installation lock, not all transitive bytes.
Package integrity is checked by npm ci; this is not continuous dependency tamper
monitoring. The upstream source tag is recorded separately from npm integrity.

The event reader explicitly accepts Pi 1.4.0 system-message pairs. The repair
oracle checks the new structured read/bash result shapes, keeps raw result
hashes, and masks only finite nonnegative bash wall_time_seconds during the
outcome comparison. It still rejects changed exit codes, truncation, fabricated
results, missing effects and unexpected files. Historical advanced rewrite
oracles have not been ported to 1.4.0; their exact-payload failures must not be
presented as successful compatibility tests.

An npm audit of the locked candidate on 2026-10-30 reported an undici advisory
chain, including high-severity findings. This is an unresolved dependency review
item, not proof of exploitation in these offline fixtures. No automatic downgrade
or force fix was applied, since that would change the tested artifact. This
candidate is not a declaration that live network workloads are production ready.
