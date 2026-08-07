# A flaky check, no model involved

Every other example here calls a local model, which makes the workbench look
like a model tool. It is not. A step is `argv` — the runner executes it and
records what came back, and it has no notion of a model, a provider, or a
prompt. This example is a shell script that fails twice and then succeeds.

`flaky.sh` fails its first two invocations and passes afterwards, counting in
`.flaky-state` beside itself. It is deterministic on purpose: a demo that
flips a coin cannot show that `retry` made the difference, because you could
never tell a rescued run from a lucky one.

Run everything below from this directory. `rm -f .flaky-state` resets it.

## The step fails

```console
$ rm -f .flaky-state
$ hwb run noretry.json
[timing] step check: 1 attempt(s)
20260807T174244Z-40481e-132e  discovery  1 step(s)  completed
```

**`completed` describes the harness, not the step.** The step exited 1. `hwb
run` exits 0 whenever the harness itself worked, because a non-zero exit from
your workload is data, not an error — the run recorded exactly what happened
and that is the run succeeding at its job.

## Attach `retry` and it passes

```console
$ rm -f .flaky-state
$ hwb run retry.json
[timing] step check: 3 attempt(s)
20260807T174244Z-947867-1117  discovery  1 step(s)  completed
```

The history is kept, not collapsed to the outcome:

```console
$ hwb show 20260807T174244Z-947867-1117
...
attempts
  step check n=0   exit=1     8ms  <- retry:0
  step check n=1   exit=1     8ms  <- retry:1
  step check n=2   exit=0     7ms  <- retry:2
```

Every attempt is retained and each one names what caused it. A harness that
reduced this to "passed" would have destroyed the only evidence that the
check is flaky at all.

## Ask what changed

```console
$ hwb diff <the noretry run> <the retry run>
harness: 5 difference(s)
  spec: DIFFERENT specs (sha256:40481e3d0592 vs sha256:9478678c3b2d)
  features: only in B -- retry
  step check: 1 attempt(s) vs 3
  step check: exits [1] vs [1, 1, 0]
  step check: attempt CAUSE differs -- - vs (retry) (retry) (retry)
output:  4 step output(s) DIFFER
...
masked (the noise floor of this comparison)
  run_id
  started_at
  ...
```

`diff` reports **what it ignored** alongside what it found. A comparison that
does not state its noise floor cannot be audited.

## Scrub the output before it reaches disk

`flaky.sh` prints a token-shaped string. `redact.json` attaches a pattern:

```console
$ rm -f .flaky-state && hwb run redact.json
$ cat <run dir>/steps/check/attempts/2/stdout.bin
checking api (invocation 3)
auth: using key [REDACTED]
ok: api healthy
```

## Campaigns need the stateless companion

`stable.json` runs `stable.sh`, which remembers nothing. Use it for anything
that runs the spec more than once:

```console
$ hwb catch stable.json
20260807T202556Z-80a43f

fault model (declared, because a catch rate without one is meaningless)
  ...

MUTATION           INPUT                    EXPECT    DETECTED BY
(none)             -                        ignored   -
append_byte        config.txt               caught    freeze
delete             config.txt               caught    freeze
touch_only         config.txt               ignored   -
trailing_newline   config.txt               caught    freeze
...
undeclared_file    (not in steps[].inputs)  ignored   -

caught 6/6   false alarms 0   correctly ignored 3
```

`stable.sh` is a declared input too, so it gets the same four mutations —
elided above, which is what the `...` means. `config.txt` and `stable.sh`
each contribute three caught and one correctly ignored; the third ignored is
`undeclared_file`. The `(none)` row is the control, and is counted in
neither.

**Why not `flaky.sh`?** Campaigns run the spec many times and compare the
records. `flaky.sh` counts its invocations, so run two starts where run one
stopped and every comparison measures the leftover state instead of the
thing being tested. This is a real confound and not a quirk of this example
— it is why `hwb efficacy` warms its baseline and then runs it twice before
believing any result.

Try `hwb catch retry.json` to see the honest version of the other failure:
`caught 0/3`, because that spec attaches no detector. A catch rate with
nothing watching is not a low score, it is a missing subject.

## Check the features behaved

```console
$ hwb confine <run id>
FEATURE      POWER      VERDICT      DETAIL
redact       wrap       clean        wrote only through its declared channel
retry        wrap       clean        wrote only through its declared channel

2 clean, 0 breached, 0 unmeasured
```

Each feature declares a power in its manifest, and `confine` checks it kept
to it. See [`docs/measuring.md`](../../docs/measuring.md) for the rest of the
families, and [`docs/writing-a-feature.md`](../../docs/writing-a-feature.md)
to write your own.
