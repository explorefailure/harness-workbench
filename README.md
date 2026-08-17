# Harness Workbench

`hwb` executes commands from a JSON spec and records every attempt, exit code,
raw output, attached feature, and feature result. **Features** are declared
units of behavior that hook into a run at fixed seams.

The same harness cannot observe all of its own defects. `hwb` therefore ships
campaigns that evaluate specific relations: whether a feature failure was
contained, whether an inverted decision changed downstream behavior, whether
a detector observed an input mutation, and whether verdict engines reject a
known record violation.

It is built for two jobs:

- **Test your own harness ideas.** Write a feature, attach it to a step, then
  invert it and check that something noticed. See
  [`docs/measuring-your-own-code.md`](docs/measuring-your-own-code.md).
- **Test somebody else's harness.** `hwb subjects --into ./subjects` copies a
  ready-made tree that runs Claude Code, Codex CLI, DeepSeek Harness, Hermes
  Agent, and Pi as measured subjects, projecting five different native
  evidence surfaces into one envelope. See
  [Measuring another harness](#measuring-another-harness).

Status: **[`v0.1.0-rc.1`](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.1)
public GitHub prerelease** (package version `0.1.0rc1`), published 2026-08-12
with wheel and source-distribution assets. It is not published to PyPI. Zero
runtime dependencies, Python 3.11+, 377 tests.

Maintenance status: **actively developed, solo maintained**. Focused bug fixes,
documentation, and tests are welcome for best-effort review; larger changes
should start with an issue. There is no response, merge, compatibility, or
support SLA. See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution
process, [SUPPORT.md](SUPPORT.md) for public help, and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.

---

## A step is any command

The runner executes `argv` with `subprocess.run` and records what came back.
It has **no notion of a model, a provider, or a prompt.** A step calling
`curl`, a test suite, a build script, a shell pipeline, a Python script
hitting some API — all identical to the workbench.

Several examples in `examples/` call a local model through ollama. Those are
*examples*, not requirements: nothing in `src/` couples to ollama and the
test suite has no network references at all. Start with
[`examples/flaky/`](examples/flaky/), which uses a shell script and no model.

## Install

```console
$ pip install .           # from a clone
$ hwb --version
hwb 0.1.0rc1
```

**If `hwb` is not found afterwards**, the script went somewhere not on your
PATH — commonly `~/Library/Python/3.11/bin` on macOS, `~/.local/bin` on Linux.
Either add it, or skip the problem entirely:

```console
$ python3 -m harness_workbench --help   # equivalent, always works
```

A virtualenv avoids it as well, and is the recommended way to try this out.

## Compatibility

`harness-workbench` requires CPython 3.11 or newer. The v0.1 support target is
CPython 3.11, 3.12, 3.13, and 3.14 on Linux and macOS. A newer Python may be
able to install the package, but is not claimed as supported until it joins
that test set. Local release checks exercised macOS. Immutable-tag CI exercised
the full Linux/macOS matrix; the release-final conformance record is attached
to the [GitHub prerelease](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.1).

Windows is unsupported. The workbench is deliberately POSIX-oriented rather
than merely untested there: seam budgets use `SIGALRM`, interruption campaigns
terminate a direct child at published checkpoints, filesystem measurements
preserve and inspect executable modes, symlinks, and special nodes such as
FIFOs, and the shipped examples execute `/bin/sh` scripts. Workload commands
are passed directly to `subprocess` without a shell, so users choose their own
POSIX command or request `/bin/sh -c` explicitly.

## Security boundary

Harness Workbench is an execution tool, **not a security sandbox**. Reading and
validating a JSON spec and its feature manifests is inert, but running that
spec crosses a trust boundary: every `steps[].argv` command executes with the
current user's permissions, and every selected `feature.py` module is imported
as arbitrary Python code. Only run specs, commands, feature roots, and
preserved feature source that you trust.

Campaigns do not change that boundary. In particular, `hwb replay` copies
declared workload files into a separate directory to avoid overwriting the
original workload. That directory is ordinary filesystem organization, not
OS-level isolation: replayed commands and feature modules retain the user's
filesystem, process, environment, and network access.

Run records preserve stdout, stderr, selected environment values, specs, and
feature source. Do not put secrets in a spec's `env` list or commands unless
you intend those values to enter the run store. See [SECURITY.md](SECURITY.md)
for supported versions and private vulnerability reporting.

## First run, no features and no model

Write a spec:

```json
{
  "schema": "hwbspec/v0.1",
  "features": [],
  "steps": [{"id": "01", "argv": ["/bin/echo", "hello"]}]
}
```

```console
$ hwb run hello.json
20260807T170423Z-6beee9-4472  discovery  1 step(s)  completed

$ hwb ls
RUN                            CLASS         STATE       STEPS  FEATURES
20260807T170423Z-6beee9-4472   discovery     complete    1      -

$ hwb show 20260807T170423Z-6beee9-4472
```

This run needs no configuration or features. The run store is `./runs` by
default.

Campaign manifests use their own stores (`./sweeps`, `./steadies`, and so
on). A campaign store must be disjoint from the run store: equality or
nesting in either direction is rejected after resolving symlinks, before
either kind of evidence is created. The separate defaults already satisfy
this contract.

**`completed` describes the harness, not the command.** `hwb run` exits 0 when
the harness completes successfully. A non-zero workload exit is recorded data,
not a harness error.

## Adding features

Six features ship inside the package. They are **opt-in**: a spec must select
them by name.

```json
{
  "schema": "hwbspec/v0.1",
  "features_root": "harness_workbench:builtin",
  "features": [
    {"name": "retry", "config": {"max": 3}},
    {"name": "timing"}
  ],
  "steps": [{"id": "01", "argv": ["./flaky.sh"], "inputs": ["flaky.sh"]}]
}
```

| feature | power | does |
|---|---|---|
| `retry` | wrap | re-runs a failing step, keeping every attempt |
| `sample` | wrap | runs each step N times |
| `redact` | wrap | scrubs patterns out of captured output before it lands |
| `freeze` | annotate | digests declared inputs, reports drift against a baseline |
| `receipt` | annotate | binds the run to the identity of what it ran on |
| `timing` | observe | reports seam dispatch cost (an instrument, not a capability) |

Omitting `features_root` looks in `<spec dir>/features`, which is where local
features go. **Shipped features are never a fallback**: an invalid root or
feature name fails during feature loading instead of selecting a builtin.

Write your own: [`docs/writing-a-feature.md`](docs/writing-a-feature.md).

## What gets recorded

Every attempt is kept, flat and append-only, and never collapsed to an
outcome:

```console
$ hwb show <run id>
attempts
  step check n=0   exit=1     8ms  <- retry:0
  step check n=1   exit=1     8ms  <- retry:1
  step check n=2   exit=0     7ms  <- retry:2
```

Each attempt names what caused it. A harness that reduced this to "passed"
would destroy the only evidence that the step is flaky at all. Reduction
happens at read time, never at capture.

The record is meant to be readable **without this tool** — raw output as
bytes, the spec that ran and every attached feature's source preserved beside
it. `hwb fidelity` checks that claim rather than asserting it, and
[`docs/the-record.md`](docs/the-record.md) is the map for reading a run with
nothing but `cat`.

## Measuring the harness

```console
$ hwb confine <run id>        # did each feature use only its declared record channel
$ hwb effects <spec> --watch state --allow state/output.txt  # did files stay in a bounded envelope
$ hwb blast <spec>            # break a feature; was the damage contained
$ hwb interrupt <spec>        # kill the runner at each durable lifecycle boundary
$ hwb steady <spec>           # is the unchanged baseline stable enough to compare
$ hwb efficacy <spec>         # invert a feature's decision; did anything notice
$ hwb catch <spec>            # perturb declared inputs; did a detector fire
$ hwb sensitivity <run id>    # does every registered verdict engine reject a known violation
$ hwb sweep <spec> && hwb interfere <sweep id>   # do features disturb each other
$ hwb diff <run a> <run b>    # what changed, and what was masked
$ hwb fidelity <run id>       # what can be answered from the record alone
$ hwb replay <run id> --in .  # re-execute from the preserved spec
```

`hwb` does not score workload quality; that judgment needs an oracle the
project does not have. The campaigns instead check a *relation* between two
runs, or between a run and a deliberate violation. These verdicts do not need
quality labels.

Full guide: [`docs/measuring.md`](docs/measuring.md), including a section on
what the instrument **cannot** see.

## Measuring another harness

A *feature* is a control you own and can invert. A *subject* is a harness you
run from the outside and measure. The shipped subject tree covers five:

```console
$ hwb subjects --into ./subjects
```

`hwb subjects` with no destination lists what ships instead of copying it.
Once the tree is yours, its own suite runs with no network and no third-party
client installed; the subject runs need that subject present:

```sh
cd subjects
python3 -m unittest test_experiment.py            # 61 tests, offline
python3 runner.py --subject claude --workload repair
```

The tree is copied out rather than run in place, and that is the point: each
spec declares its adapter sources in `inputs`, so `freeze` and `receipt`
digest the exact bytes that ran. An adapter imported from the installed
package would instead make "which adapter ran" a property of whichever
version happened to be installed.

**It is not a stable API.** Every subject is pinned to an exact third-party
release, and one of them is a developer preview that documents breaking
changes as expected. The tree ships so the workbench can demonstrate itself
against real harnesses; `src/` imports nothing from it, and the pins are
yours to update.

Each subject exposes a different interception surface — some can deny a tool
call, fewer can rewrite its input, fewer still can rewrite its result. Those
differences are the interesting part, and
[`SHARED_ADAPTER_CONTRACT.md`](src/harness_workbench/subjects/SHARED_ADAPTER_CONTRACT.md)
records them per harness.

### The capture primitive

Writing adapters twice surfaced one thing that is not about any harness:
bounding a hostile child process and coming back with evidence you can defend.
That much is core, in `harness_workbench.capture`:

```python
from harness_workbench import capture

env = capture.minimal_environment(root, {"MY_HARNESS_OFFLINE": "1"})
result = capture.run_bounded(argv, cwd=workspace, env=env, timeout=120)
evidence = capture.capture_bytes(
    result.stdout, redactions=capture.credential_values(os.environ)
)
```

A bound firing is a measurement, not an error: timeouts, byte limits and
nonzero exits come back in `result`, and `run_bounded` raises only when it
cannot measure at all. The child leads its own process group and every
termination signals the group, because a subject holding a shell outlives a
signal sent to the shell alone — and `group_alive_after_cleanup` reports
whether anything survived rather than assuming nothing did.

The envelope shape, what it refuses to promote, and why is in
[`docs/adapter-primitive-extraction.md`](docs/adapter-primitive-extraction.md).
The adapters themselves stay out of core, for the reason above.

## Commands

Two kinds, and mixing them up is the most common first mistake:

- **take a spec** — `run` `sweep` `blast` `catch` `steady` `effects` `interrupt` `efficacy`
- **take an id** — `show` `verify` `diff` `fidelity` `sensitivity` `confine`
  `replay` `interfere` `order`
- **take neither** — `ls`, and `subjects` (copies the shipped subject tree)

Use `hwb --help` or `hwb <command> --help` for the registered command and
option reference.

## Documentation

| | |
|---|---|
| [`docs/measuring-your-own-code.md`](docs/measuring-your-own-code.md) | **start here to use it** — attach your workload and your own features, then measure them |
| [`examples/flaky/`](examples/flaky/) | a worked example, no model required |
| [`docs/the-spec.md`](docs/the-spec.md) | every spec field, the bounds, and the digest rule |
| [`docs/the-record.md`](docs/the-record.md) | what a run directory contains and how to read it without `hwb` |
| [`docs/campaign-manifests.md`](docs/campaign-manifests.md) | exact stores, schemas, fields, verdicts, and limits for campaign evidence |
| [`docs/writing-a-feature.md`](docs/writing-a-feature.md) | the manifest contract, seams, powers, capabilities |
| [`docs/measuring.md`](docs/measuring.md) | every campaign, what its verdict means, and its limits |
| [`docs/experiment-writeups.md`](docs/experiment-writeups.md) | required learning record and code-consequence template for every experiment |
| [`docs/adapter-primitive-extraction.md`](docs/adapter-primitive-extraction.md) | what two independent adapters converged on, and which half of it became core |
| [`docs/release-conformance-0.1.0rc1.md`](docs/release-conformance-0.1.0rc1.md) | the source-bundled pre-release conformance record; the [release-final record](https://github.com/explorefailure/harness-workbench/releases/download/v0.1.0-rc.1/harness-workbench-v0.1.0-rc.1-release-conformance.md) is attached to the GitHub prerelease |

**These pages have machine-checked surfaces, not a blanket guarantee.** The
test suite asserts that every spec field, every record key, the seam table,
the powers table and the command split match the code. It re-runs each
registered `console` transcript and holds the remaining unregistered count to
a ceiling, with any elision in checked output marked `...`. What no test can
check is whether the *prose* is still a good explanation. See
[`docs/measuring.md`](docs/measuring.md#are-the-transcripts-in-these-docs-real).

## Development

```console
$ python3 -m unittest discover -s tests
```

The test suite is network-free and needs no model installed.

Contributions are reviewed on a best-effort basis. Use GitHub Issues for usage
questions and non-sensitive bugs, and discuss larger changes there before
substantial implementation. The complete posture and local checks are in
[CONTRIBUTING.md](CONTRIBUTING.md) and [SUPPORT.md](SUPPORT.md).

Release tooling is pinned in the `release` extra so local and CI artifact
checks use the same versions:

```sh
python3 -m pip install '.[release]'
python3 -m build --wheel
python3 -m twine check --strict dist/*.whl
```

Maintainers should use the complete, fail-closed candidate and final-release
procedure in [`RELEASING.md`](RELEASING.md), including clean artifact installs,
commit-derived timestamps, ownership-neutral source-distribution repacking,
tag/version agreement, and checksums. A raw backend-built sdist must not be
uploaded; building an archive is not by itself a release.

It includes a deterministic, stdlib-only generated corpus for the JSON,
spec, feature-manifest, conformance, and partial-close boundaries. The fixed
seed and case counts live in `tests/test_properties.py`; a generated failure
should be reduced to an ordinary regression case before its fix lands.

## Licence

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
