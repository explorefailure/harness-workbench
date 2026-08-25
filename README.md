# Harness Workbench

**Harness Workbench is a test bench for your own software or AI harness—and for
the features you add to it.**

Bring a real workload and connect the harness behavior you want to test,
whether that's a retry policy, a redaction layer, a change detector, or
something entirely your own. Harness Workbench records each run so you can
inspect what happened and compare it with other runs.

Its measurement campaigns deliberately change conditions, introduce faults,
combine features, interrupt runs, and replay preserved inputs. They help reveal
whether a feature had an effect, stayed contained, interfered with another
feature, or failed without being noticed.

## Why this matters

A workload can finish successfully even when its harness did not work as
expected. A retry policy can fail when it is needed, a detector can miss a
change, or two features that work separately can interfere when used together.

These failures do not always produce an obvious error. Harness Workbench makes
them visible through controlled experiments and preserves the evidence you
need to reproduce and understand them.

## Who this is for

Harness Workbench is for people who build harnesses—or add new features to
one—and need to know whether those changes really work.

It is especially useful if you are:

- building your own software or AI harness;
- adding features such as retries, redaction, guardrails, tool controls,
  memory, or change detection;
- comparing harness, model, provider, or extension upgrades;
- investigating failures that leave behind a successful-looking run; or
- developing integrations for AI coding harnesses such as Pi, Claude Code,
  Codex, or Hermes Agent.

If your main question is whether an answer or generated program is good,
Harness Workbench is not a general-purpose benchmark. It focuses on whether
the harness around that work behaved as intended.

[`Try it`](#try-it) · [`What it can test`](#what-it-can-test) ·
[`Experiment ideas`](#experiment-ideas) ·
[`Measure your own code`](docs/measuring-your-own-code.md) ·
[`How the evidence works`](#what-gets-recorded)

It is built for two jobs:

- **Test your own harness ideas.** Write a feature, attach it to a step, then
  invert it and check that something noticed. See
  [`docs/measuring-your-own-code.md`](docs/measuring-your-own-code.md).
- **Test somebody else's harness.** `hwb subjects --into ./subjects` copies a
  ready-made tree that runs Claude Code, Codex CLI, DeepSeek Harness, Hermes
  Agent, and Pi as measured subjects, projecting five different native
  evidence surfaces into one envelope. See
  [Measuring another harness](#measuring-another-harness).

Latest published release: **[`v0.1.0-rc.1`](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.1)
public GitHub prerelease** (package version `0.1.0rc1`), published 2026-08-12
with wheel and source-distribution assets. It is not published to PyPI.

This tree is **ahead of that prerelease**: it prepares candidate `0.1.0rc2`,
which has no tag, no release, and no assets. The difference that matters to a
reader is a new public module, `harness_workbench.capture`, which recipients of
`v0.1.0-rc.1` did not receive. Zero runtime dependencies, Python 3.11+.

Maintenance status: **actively developed, solo maintained**. Focused bug fixes,
documentation, and tests are welcome for best-effort review; larger changes
should start with an issue. There is no response, merge, compatibility, or
support SLA. See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution
process, [SUPPORT.md](SUPPORT.md) for public help, and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.

---

## Try it

The prerelease is not published to PyPI. Install it from a clone in a virtual
environment:

```console
$ git clone https://github.com/explorefailure/harness-workbench.git
$ cd harness-workbench
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install .
$ hwb --version
hwb 0.1.0rc2
```

Create `hello.json` with one command and no features:

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

**`completed` describes the harness, not the command.** `hwb run` exits 0 when
the harness completes successfully. A non-zero workload exit is recorded data,
not a harness error.

### See it test something

The deterministic [`examples/flaky/`](examples/flaky/) workload fails twice
and then succeeds. From that directory, attach the shipped `retry` feature and
run it:

```console
$ rm -f .flaky-state
$ hwb run retry.json
[timing] step check: 3 attempt(s)
20260807T174244Z-947867-1117  discovery  1 step(s)  completed
```

The final attempt passed, but Harness Workbench did not erase the two failures
that came before it. The full example compares the run with and without retry,
shows what changed, and demonstrates redaction and change detection without a
model or network connection.

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

The record is meant to be readable **without this tool**—raw output as bytes,
the spec that ran, and every attached feature's source preserved beside it.
`hwb fidelity` checks that claim rather than asserting it, and
[`docs/the-record.md`](docs/the-record.md) is the map for reading a run with
nothing but `cat`.

## What it can test

Harness Workbench tests the features that observe, change, protect, and record
a run. It can help answer:

- **Did it work?** Did the feature change the downstream result it was supposed
  to change, or was it present but ineffective?
- **Did it notice what it should?** Does a detector catch deliberate changes to
  its declared inputs while ignoring changes outside its job?
- **Did it fail safely?** If a feature breaks, hangs, or writes somewhere it
  should not, is the damage contained and the unfinished run clearly marked?
- **Did anything interfere?** Do features behave differently together or when
  their order changes?
- **Can the evidence be trusted?** Is the record complete, intact, readable
  without Harness Workbench, and capable of being replayed?
- **Is the comparison meaningful?** Is the unchanged baseline stable, what
  changed between runs, and what noise was deliberately ignored?

Harness Workbench does not score whether the workload produced a good answer
or prove that a harness is correct in every environment. Each measurement
answers one specific, bounded question and preserves the evidence behind its
result. Start with
[`docs/measuring-your-own-code.md`](docs/measuring-your-own-code.md) to attach
your own command and feature, then choose the measurement that matches your
question.

## Experiment ideas

### Problems I'm building adapters to test

The most revealing experiments ask whether the harness kept a promise that an
ordinary successful run could hide:

- **Does stop actually mean stop?** Cancel a harness while a delayed tool action
  is about to arrive. Check whether any tool still runs or any file changes
  after cancellation.
- **Is the guardrail actually doing anything?** Run the same operation once
  blocked and once allowed. The durable outcome should reverse, proving that
  the guardrail—not something else—controlled the result.
- **What did context compaction forget?** Force compaction halfway through a
  task whose later steps depend on earlier decisions. Check whether the harness
  preserves the decisions, file state, tool results, and next required action.
- **Did a retry perform the action twice?** Let an action succeed but hide or
  delay its acknowledgement. Check whether retries recover safely or duplicate
  the effect.
- **What changed when you upgraded?** Replay the same scenarios before and
  after changing the harness, model, provider, or extension. Compare the tools,
  decisions, events, and durable outcomes—not just the final answer.

These experiments need structured access to the target harness. The candidate
subject tree provides that access for five harnesses, but no adapter is part of
the published prerelease. Until promotion evidence and owner review close, this
list describes candidate experiments—not capabilities claimed for the current
release.

### Experiments you can run now

Start with a small, deterministic workload whenever possible:

- Make a workload fail twice and succeed on the third attempt. Add a retry
  policy and see whether a successful final result still preserves the evidence
  of the earlier failures.
- Take two features that work correctly on their own, run them together, and
  reverse their order. Check whether combination or composition changes either
  feature's behavior.
- Give a feature a silent fault—such as a valid-looking but incorrect return—
  instead of making it crash. Check whether the harness notices and contains
  the damage.
- Interrupt the runner moments before and after it finishes closing a record.
  Check whether any unfinished evidence can masquerade as a successful run.
- Introduce a violation that a checker is supposed to reject. Confirm that the
  checker still detects it rather than treating silence as success.
- Print an unmistakably synthetic secret and verify that a redaction feature
  removes it before the output is stored.
- Allow one filesystem destination, deliberately write somewhere else, and
  verify that the boundary violation is detected.
- For an AI workload, repeat the same prompt or compare model, prompt, and
  harness configurations while focusing on observable differences rather than
  scoring answer quality.

## What I'm building next

Harness Workbench can already run anything exposed as a command, while your
own harness behavior can be added as Workbench features. Candidate `0.1.0rc2`
now includes an experimental subject tree for Pi, Claude Code, Codex CLI,
DeepSeek Harness, and Hermes Agent. They share one evidence envelope without
pretending their native event and interception surfaces are the same.

The next work is promotion evidence, not another adapter name:

- re-cut the current-source peer records needed for a five-subject comparison
  with Hermes `0.20.5`, and resolve whether its strict stdout movement has a
  narrow defensible normalization rather than adding a broad allowance;
- rerun the source, installed-artifact, and hosted Linux/macOS gates after the
  audit remediations land; and
- keep merge, tag, and release behind explicit owner review.

Until those gates close, the adapters are an experimental candidate surface,
not a supported integration promise.

Harness Workbench will remain independent of any particular model, provider,
or agent framework.

## How it works

Each step is a command. The runner executes it and records every attempt, exit
code, raw output, attached feature, and feature result. **Features** are
declared units of behavior that hook into a run at fixed seams.

The runner has **no notion of a model, a provider, or a prompt.** A step calling
`curl`, a test suite, a build script, a shell pipeline, or a Python script
hitting some API is identical to the workbench. Several examples call a local
model through ollama, but those are examples, not requirements: nothing in
`src/` couples to ollama and the test suite has no network references. Start
with [`examples/flaky/`](examples/flaky/), which uses a shell script and no
model.

Campaigns deliberately change or damage part of the setup, then compare what
happened. They can test whether a feature failure was contained, whether an
inverted decision changed downstream behavior, whether a detector observed an
input mutation, and whether verdict engines reject a known record violation.

## Project status and support

Maintenance status: **actively developed, solo maintained**. Focused bug fixes,
documentation, and tests are welcome for best-effort review; larger changes
should start with an issue. There is no response, merge, compatibility, or
support SLA. See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution
process, [SUPPORT.md](SUPPORT.md) for public help, and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Compatibility

`harness-workbench` requires CPython 3.11 or newer. The v0.1 support target is
CPython 3.11, 3.12, 3.13, and 3.14 on Linux and macOS. A newer Python may be
able to install the package, but is not claimed as supported until it joins
that test set. For candidate `0.1.0rc2`, exact public PR checkpoint
`f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3` passed the full Linux/macOS matrix
(all eight CPython cells) and the package job in [CI run
32604245910](https://github.com/explorefailure/harness-workbench/actions/runs/32604245910),
and passed [CodeQL run
32604245892](https://github.com/explorefailure/harness-workbench/actions/runs/32604245892).
That is preparation evidence for that commit, not release-final evidence, and
the post-audit candidate must rerun it. The published `v0.1.0-rc.1` separately
has Immutable-tag CI and a release-final conformance record attached to its
[GitHub prerelease](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.1).

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

Passing a bounded Workbench experiment is evidence about that declared
scenario and observed boundary—not a general security, sandboxing, or
prompt-injection certification.

Run records preserve stdout, stderr, selected environment values, specs, and
feature source. Do not put secrets in a spec's `env` list or commands unless
you intend those values to enter the run store. See [SECURITY.md](SECURITY.md)
for supported versions and private vulnerability reporting.

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

Write your own: [`docs/writing-a-feature.md`](docs/writing-a-feature.md).

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
client installed; live subject runs have additional requirements. The
committed active profile is the remote `opencode-go` gateway: DeepSeek,
Hermes, and Pi runs require a valid `HWB_OPENCODE_KEY`, outbound network
access, and may consume paid quota or incur spend. Hermes—the pinned Nous
client—makes a remote API call under that active profile. `local-ollama` is a
separate optional local profile, not the configuration those commands use by
default:

```sh
cd subjects
python3 -m unittest test_experiment.py            # offline; no subject installed
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
| [`docs/release-conformance-0.1.0rc2.md`](docs/release-conformance-0.1.0rc2.md) | the source-bundled pre-release conformance record, covering the **unreleased** `0.1.0rc2` candidate this tree prepares; the [release-final record](https://github.com/explorefailure/harness-workbench/releases/download/v0.1.0-rc.1/harness-workbench-v0.1.0-rc.1-release-conformance.md) for the published `v0.1.0-rc.1` is attached to that GitHub prerelease |

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

Release tooling is pinned in the `release` extra — the build backend included,
because `[build-system].requires` is only a floor and an isolated build
resolves it freshly — so local and CI artifact checks use the same versions:

```sh
python3 -m pip install '.[release]'
python3 -m build --no-isolation --wheel
python3 -m twine check --strict dist/*.whl
```

That is the development convenience. The release gate installs the same pinned
versions without installing the project, because installing the project leaves
behind the `build/` directory the gate refuses to start with.

Maintainers should use the complete, fail-closed candidate and final-release
procedure in [`RELEASING.md`](RELEASING.md), including clean artifact installs,
commit-derived timestamps, ownership-neutral source-distribution repacking,
tag/version agreement, and checksums. A raw backend-built sdist must not be
uploaded; building an archive is not by itself a release.

## Licence

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
