# Harness Workbench

**Run things. Record what happened. Then ask whether the thing that recorded
it can be trusted.**

`hwb` executes a list of commands from a JSON spec and writes a record of
what occurred — every attempt, every exit code, raw output, which code was
attached and what it did. Behaviour is added through **features**: small
declared units that hook into the run at fixed seams.

The part that is unusual is the second half. Once you have features, you have
a harness whose own defects are invisible to it — so `hwb` ships a set of
campaigns that measure the harness itself: break a feature and check the
damage was contained, invert a feature's decision and check anything
downstream noticed, perturb the inputs and check a detector fires, mutate a
record and check the checkers still reject it.

Status: **v0.1.0.** Zero runtime dependencies, Python 3.9+, 224 tests.

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
```

**If `hwb` is not found afterwards**, the script went somewhere not on your
PATH — commonly `~/Library/Python/3.9/bin` on macOS, `~/.local/bin` on Linux.
Either add it, or skip the problem entirely:

```console
$ python3 -m hwb --help   # equivalent, always works
```

A virtualenv avoids it as well, and is the recommended way to try this out.

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

That works with no configuration, nothing installed, and no features. The
run store is `./runs` by default.

**`completed` describes the harness, not your command.** `hwb run` exits 0
whenever the harness itself worked. A non-zero exit from your workload is
data — the run recorded exactly what happened, which is the run succeeding.

## Adding features

Six features ship inside the package. They are **opt-in**: a spec asks for
them by name and never gets them by accident.

```json
{
  "schema": "hwbspec/v0.1",
  "features_root": "hwb:builtin",
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

Omitting `features_root` looks in `<spec dir>/features`, which is where your
own features go. **Shipped features are never a fallback** — a mistyped root
fails loudly rather than quietly succeeding with code you did not choose.

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
$ hwb sensitivity <run id>    # does every verdict engine reject a known violation
$ hwb sweep <spec> && hwb interfere <sweep id>   # do features disturb each other
$ hwb diff <run a> <run b>    # what changed, and what was masked
$ hwb fidelity <run id>       # what can be answered from the record alone
$ hwb replay <run id> --in .  # re-execute from the preserved spec
```

**There is no scorer.** Judging whether your workload produced a good answer
needs an oracle this project does not have. What is checkable without one is
a *relation* — between two runs, or between a run and a deliberate violation
of it — which is why none of this needs labels.

Full guide: [`docs/measuring.md`](docs/measuring.md), including a section on
what the instrument **cannot** see.

## Commands

Two kinds, and mixing them up is the most common first mistake:

- **take a spec** — `run` `sweep` `blast` `catch` `steady` `effects` `interrupt` `efficacy`
- **take an id** — `show` `verify` `diff` `fidelity` `sensitivity` `confine`
  `replay` `interfere` `order`

`hwb --help` and `hwb <command> --help` are accurate and worth reading.

## Documentation

| | |
|---|---|
| [`docs/measuring-your-own-code.md`](docs/measuring-your-own-code.md) | **start here to use it** — attach your workload and your own features, then measure them |
| [`examples/flaky/`](examples/flaky/) | a worked example, no model required |
| [`docs/the-spec.md`](docs/the-spec.md) | every spec field, the bounds, and the digest rule |
| [`docs/the-record.md`](docs/the-record.md) | what a run directory contains and how to read it without `hwb` |
| [`docs/writing-a-feature.md`](docs/writing-a-feature.md) | the manifest contract, seams, powers, capabilities |
| [`docs/measuring.md`](docs/measuring.md) | every campaign, what its verdict means, and its limits |

**These pages have machine-checked surfaces, not a blanket guarantee.** The
test suite asserts that every spec field, every record key, the seam table,
the powers table and the command split match the code. It re-runs each
registered `console` transcript and holds the remaining unregistered count to
a ceiling, with any elision in checked output marked `...`. What no test can
check is whether the *prose* is still a good explanation. See
[`docs/measuring.md`](docs/measuring.md#are-the-transcripts-in-these-docs-real).

## Development

```console
$ python3 -m pytest tests/ -q
```

The test suite is network-free and needs no model installed.

## Licence

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
