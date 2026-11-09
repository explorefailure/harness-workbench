# Harness Workbench

**Your hardware. Your models. Find out what they can do.**

Explore local AI on your own terms. Give a model a real job, see what actually
happened, and keep the setup that worked.

Harness Workbench brings together your local model, coding agent, and
configuration to try useful work on your machine. It independently checks the
result, preserves the evidence, and saves a Recipe you can run again.

Built around the freedom to compute: choose your tools, inspect your results,
and build on what you learn.

> **Very experimental and still underdeveloped.** This is an early project
> built by one developer. Features are incomplete, setup can be rough, and
> behavior and interfaces may change. Expect bugs and limitations. The roadmap
> describes where I want to take it, not what is ready today.

[Try it](#try-it) · [Understand your result](#understand-your-result) ·
[Keep your Recipe](#keep-your-recipe) · [Where this is going](#where-this-is-going) ·
[Documentation](#documentation)

## Why this exists

The more useful work you can do with local models, the more autonomy you have.

A smaller model that works for your needs lets you put your own hardware to
use, choose your tools, and keep everyday compute close to home.
You can experiment, learn, and keep working on your own terms.

Harness Workbench exists to help you explore that possibility: find out what
works locally, understand its limits, and keep building on it. Freedom to
compute becomes more useful when you know what you can actually do with it.

## Local AI, on your terms

You have a machine, a model, and something you want to do. How do you find out
whether that combination works for you?

Workbench gives you a place to experiment with useful jobs and inspect what
happens. A successful-looking answer is only the beginning: did the agent
change the right file, run the tests, and leave the rest alone? When something
fails, the failed attempt stays available to learn from.

The direction is simple:

1. **Discover** what might work with your hardware and local AI setup.
2. **Try** a Reality Check: one recognizable job with clear success conditions.
3. **Prove** the result through independent checks and inspectable evidence.
4. **Keep** a Recipe you can rerun and build on.

That journey is being built in stages. **Today, the released CLI offers one
coding Reality Check using Pi and Ollama.** Automatic setup discovery and the
full visual experience are still in development.

## Start with a real job

The first Reality Check asks a local coding agent to fix a Python bug in a
disposable fixture. It gives the model a small job with a result you can check.

Workbench independently checks that the tests failed before the repair and
passed afterward, that only the permitted file changed, and that the retained
record is intact. It also checks the agent's native evidence for running the
required test before and after its edit. The agent saying “done” does not earn
a pass.

You get a verdict, the facts behind it, changed files, a saved run record, and
a Recipe to try again. A pass is evidence for this task and setup; repeated
runs are needed to explore consistency.

## Try it

The latest public GitHub release is
[**v0.2.0**](https://github.com/explorefailure/harness-workbench/releases/tag/v0.2.0),
with a signed tag, wheel, source archive, checksums, and release-conformance
record. The package is not published to PyPI.

### Install Workbench

Use Python 3.11–3.14 on Linux or macOS. Workbench has zero Python runtime
dependencies; the coding agent, Node, and Ollama are separate prerequisites.
Installing from source may download build dependencies.

```sh
git clone --branch v0.2.0 https://github.com/explorefailure/harness-workbench.git
cd harness-workbench
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
hwb --version
```

Release wheels and source archives are also available from the
[release page](https://github.com/explorefailure/harness-workbench/releases/tag/v0.2.0).

### Prepare the local coding setup

The first Reality Check is a demo of the feature, built around one specific
setup so you can try the complete run, verification, and Recipe experience.
These exact requirements apply to this demo. Support for additional local AI
runtimes and integrations with agent tracing software are coming soon, with
more models, coding agents, and configurations planned as the feature expands.
Workbench will complement the tracing tools you already use, bringing their
traces together with independent checks and controlled experiments to help you
understand failures, improve your setup, and get better results from local AI.

The demo requires:

- Ollama running locally at `http://127.0.0.1:11434`, with `gpt-oss:20b`
  installed and enough memory to run it.
- Pi **0.84.1** and Node **22.22.3** on `PATH`. The check validates Pi's
  executable hash as well as its version against the
  [shipped pins](src/harness_workbench/subjects/pin.json).

This is the subject adapter's baseline Pi setup. The separate
[Pi builder integration](docs/pi-builder.md) defaults to a newer Pi/Node pair;
that default does not satisfy this Reality Check's pins. Workbench does not
install these tools, download models, or reconfigure your model server for you.

**Known v0.2.0 setup issue:** the shipped local profile enforces a historical
Ollama model digest. If your installed model has different bytes, the check can
return `INVALID` before the agent starts. That is a setup failure, not a failed
repair. A correction is in development; it is not included in v0.2.0. The
intended behavior is to record your live model identity and flag changes for
another proof.

### Run the Reality Check

From a directory where you want to keep your Recipes and run records:

```sh
hwb try coding/python-bug-fix
```

No Workbench specification needs to be written. The check selects the
`local-ollama` profile, creates its disposable repair workspace, and retains
an ordinary sealed Workbench record under `runs/`.

Not ready to run a model? The [no-model example](#explore-without-a-model)
lets you inspect a deterministic failure-and-retry experiment first.

## Understand your result

| Verdict | What it means |
| --- | --- |
| **PASS** | Every required independent fact is established. |
| **PARTIAL** | Trustworthy evidence shows a green repair, but another required condition failed. |
| **FAIL** | Trustworthy evidence measured an unsuccessful repair. |
| **INVALID** | The setup or evidence cannot support a trustworthy task verdict. |

The terminal shows the verification facts, changed files, reasons the result
was not a pass, setup identity, record location, rerun command, and explicit
blind spots. Open the retained record to investigate what happened:

```sh
hwb ls
hwb show <run-id>
hwb verify <run-id>
```

A sealed record lets you check that the retained files have not changed. It
does not prove that every external effect was observed. This first check
establishes a bounded fixture result, not general coding ability or a
model-quality score.

## Keep your Recipe

The check saves a Recipe under `.hwb/recipes/coding-python-bug-fix`. It holds
the configuration, task, fixture, tests, adapter inputs, and a rerun command.
To use it again:

```sh
hwb try coding/python-bug-fix --recipe .hwb/recipes/coding-python-bug-fix
```

Only `model_selection.json` is editable configuration. The executable inputs,
specification, task, fixture, and tests are integrity-bound; changed or missing
bound files are rejected. Each sealed run retains the effective input digests.
The v0.2.0 digest issue described above also applies to model changes.

Rerunning a Recipe means trying the saved configuration again. It does not
guarantee identical behavior. The record gives you something concrete to
compare as you explore.

## Where this is going

The goal is to make local AI easier to explore on hardware you control, with
useful results you can inspect and reuse.

- **Discover your setup — under consideration.** Other tools already detect
  hardware, models, and runtimes. I’m exploring integrations and whether
  built-in discovery would add enough value to help you choose a Reality Check.
- **Explore in the Lab.** Run a check, inspect its proof, keep a Recipe, and
  revisit previous runs through a local web interface.
- **Built-in harness support.** All harness adapters will become part of
  Workbench. Choose your harness and configure it—no adapter code to manage.
- **Compare and stress-test.** Change one model or setting, or introduce a
  controlled failure, and investigate what changes.
- **Explore how agents use tools.** Inspect tool calls, test permissions and
  failure recovery, and see how context and memory affect the job.
- **Share and challenge discoveries.** Publish privacy-reviewed Proof Cards
  and try the same public task on another machine.

These are development directions, not v0.2.0 features. A basic Lab prototype
is in development and unreleased. Automatic recommendations, Proof Cards,
the challenge command, and a shared compatibility map are future work.
The immediate priority is making the first Reality Check useful to fresh
users and publishing concrete results.

## Go deeper

The experiment engine underneath the Reality Check is also available for your
own workloads and harness ideas. You can preserve every attempt, compare
runs, introduce faults, and test whether a control really changes behavior.

### Explore without a model

The library of deterministic workloads will expand, giving you more ways to
test a harness or setup with scripted, predictable behavior. These workloads
take the model out of the equation so you can investigate retries, tool
failures, controls, and evidence capture without model variability obscuring
what changed. They test the surrounding machinery; model-backed Reality
Checks then explore how the complete setup performs.

Try a [deterministic workload](examples/flaky/) to see how your harness handles
failure and recovery—without needing a model. The example walks through
retries, preserved failures, redaction, and change detection.

### What gets recorded

Each attempt keeps its exit status, raw stdout and stderr, timing, and causal
feature. The executed specification and selected feature source are preserved
beside it. A final success never erases earlier failures. Records are designed
to be inspected without Workbench; [the record guide](docs/the-record.md)
explains how.

### What it can test

Campaigns can ask whether a retry worked, a detector noticed a change, two
features interfered, or a checker rejected a known violation. Each verdict is
limited to the declared scenario and observed surfaces.

The core accepts ordinary commands. Six opt-in features ship with it:
`retry`, `sample`, `redact`, `freeze`, `receipt`, and `timing`.
Start with [measuring your own code](docs/measuring-your-own-code.md), then
[write a feature](docs/writing-a-feature.md) or choose a
[measurement campaign](docs/measuring.md).

For command reference:

- **take a spec** — `run` `sweep` `blast` `catch` `steady` `effects` `interrupt` `efficacy`
- **take an id** — `show` `verify` `diff` `fidelity` `sensitivity` `confine` `replay` `interfere` `order`
- **take neither** — `ls` and `subjects`; `try` takes a Reality Check name.

Use `hwb --help` or `hwb <command> --help` for options.

### Builder integrations

Separate experimental builders let you supply tasks, model/provider settings,
and independent checks to **Pi, OpenCode, and Hermes**. See the
[builder adapter guide](docs/builder-adapters.md) for setup and boundaries.
These workflows do not promise support for every native plugin, account,
model, or platform combination.

### Measuring another harness

The shipped experimental subject tree covers **Claude Code, Codex CLI,
DeepSeek Harness, Hermes Agent, and Pi**:

```console
$ hwb subjects --into ./subjects
```

Its own offline suite needs no subject installed:

```sh
cd subjects
python3 -m unittest test_experiment.py  # offline; no subject installed
```

Local models are the preferred starting point. You can also use a hosted API
through the adapter's supported routes. Choose your model and provider before
running live experiments; copied configurations may contain a preset route.
Hosted APIs may require credentials, network access, and paid usage.
See the [subject guide](src/harness_workbench/subjects/README.md) for setup
and supported options, including the `local-ollama` profile. The committed
default, `local-llamacpp`, runs the public `qwen3.8:27b` weights on a local
llama.cpp server, pinned by the GGUF file's SHA-256; select another profile,
or one of your own, with `HWB_MODEL_PROFILE`. The optional
`opencode-go` API route uses `HWB_OPENCODE_KEY` and requires outbound network
access; paid usage may apply. Choose the route that suits your setup.

The five-subject envelope remains experiment-local, not a stable vendor-neutral
API. See the [adapter promotion review](docs/adapter-envelope-promotion-review.md).

## Execution and privacy

Harness Workbench runs local code with your permissions. It is **not a security sandbox**:
workload commands execute directly, and selected feature modules are imported
as arbitrary Python code. Run only trusted commands, features, and Recipes.
Replay directories organize files; they do not provide OS isolation.

Records can contain stdout, stderr, selected environment values, configuration,
and source. Keep secrets out of declared inputs and review evidence before
sharing it. Local execution does not make every connected tool or provider
local; inspect your chosen route. See [SECURITY.md](SECURITY.md).

## Documentation

| I want to… | Read |
| --- | --- |
| Run my own task through a builder | [Builder adapters](docs/builder-adapters.md) |
| Attach my own command or harness behavior | [Measuring your own code](docs/measuring-your-own-code.md) |
| Explore without a model | [Flaky workload example](examples/flaky/) |
| Read the evidence | [The record](docs/the-record.md) |
| Compare runs and test controls | [Measurement campaigns](docs/measuring.md) |
| Define an experiment | [The specification](docs/the-spec.md) |
| Write a feature | [Feature interface](docs/writing-a-feature.md) |
| Inspect current release assurance | [v0.2.0 conformance record](docs/release-conformance-0.2.0.md) |
| Inspect historical release assurance | [v0.1.0 source conformance record](docs/release-conformance-0.1.0.md) |

## Compatibility

Harness Workbench requires CPython 3.11 or newer. Supported versions are
CPython 3.11, 3.12, 3.13, and 3.14 on Linux and macOS.
Windows is unsupported at this time. Windows support is being considered
once the main features are working.

<details>
<summary>Release verification and historical compatibility evidence</summary>

`harness-workbench` requires CPython 3.11 or newer. The v0.2 support target is
CPython 3.11, 3.12, 3.13, and 3.14 on Linux and macOS. A newer Python may be
able to install the package, but is not claimed as supported until it joins
that test set. Published candidate `0.1.0rc2`, exact commit
`66ce4ed853ac4a60f975bddf7e84cfccd4505cc6`, passed the full Linux/macOS matrix
(all eight CPython cells) and package job in [CI run
32895018013](https://github.com/explorefailure/harness-workbench/actions/runs/32895018013),
passed [CodeQL run
32895018016](https://github.com/explorefailure/harness-workbench/actions/runs/32895018016),
and passed Immutable-tag CI in [run
32895591807](https://github.com/explorefailure/harness-workbench/actions/runs/32895591807).
Final `0.1.0`, exact commit
`773d2b10fa4500ff24e8fdf9ae1a911185ad8f2a`, then passed its own [CI and
package run](https://github.com/explorefailure/harness-workbench/actions/runs/32898121252),
[CodeQL run](https://github.com/explorefailure/harness-workbench/actions/runs/32898121190),
and [post-publication immutable-tag
check](https://github.com/explorefailure/harness-workbench/actions/runs/32898769529).
Those results prove the tagged release commit and assets only; unreleased main
must pass its own gates.

Final `0.2.0`, exact commit
`ef37c76df8910c75d676897ef4a2a9e1b196ab50`, passed its full [CI and package
run](https://github.com/explorefailure/harness-workbench/actions/runs/42446370785),
[CodeQL run](https://github.com/explorefailure/harness-workbench/actions/runs/42444044014),
and the tagged [Pi](https://github.com/explorefailure/harness-workbench/actions/runs/42446370855),
[OpenCode](https://github.com/explorefailure/harness-workbench/actions/runs/42446370783),
and [Hermes](https://github.com/explorefailure/harness-workbench/actions/runs/42446370784)
compatibility matrices. Its published assets were downloaded and independently
reverified after publication.

The current engine depends on POSIX behavior: seam budgets use `SIGALRM`, interruption campaigns
terminate a direct child at published checkpoints, filesystem measurements
preserve and inspect executable modes, symlinks, and special nodes such as
FIFOs, and the shipped examples execute `/bin/sh` scripts. Workload commands
are passed directly to `subprocess` without a shell, so users choose their own
POSIX command or request `/bin/sh -c` explicitly.

</details>

## Project status and support

Harness Workbench is **actively developed, solo maintained**, under Apache-2.0.
Try it, investigate what happens, and bring back a clear reproduction when
something breaks. Focused fixes, documentation improvements, and tests are
welcome for best-effort review. Larger changes should start with an issue;
there is no response, merge, compatibility, or support SLA.

[SUPPORT.md](SUPPORT.md) · [CONTRIBUTING.md](CONTRIBUTING.md) ·
[Private security reporting](SECURITY.md) · [License](LICENSE) · [Notice](NOTICE)

For development, the root suite is network-free and needs no model installed:

```sh
python3 -m unittest discover -s tests
```

Maintainers should follow [RELEASING.md](RELEASING.md) for artifact checks and
publication. A raw backend-built sdist must not be
uploaded; use the complete normalization and release procedure.
