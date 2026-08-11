# Contributing to Harness Workbench

Harness Workbench is actively developed and maintained by one person. Focused
bug fixes, documentation improvements, and tests are welcome for best-effort
review. Larger changes should start with a GitHub issue so scope and fit can be
discussed before substantial work begins.

Review is best effort. The project does not promise a response time, merge,
release date, compatibility exception, or support service. A technically sound
change may still be declined when it expands the project beyond its current
scope or maintenance capacity.

## Before opening a pull request

- Use [GitHub Issues](https://github.com/explorefailure/harness-workbench/issues)
  for non-sensitive bugs, usage questions, and proposals. Search existing
  issues first.
- For a focused fix, add or update tests that would fail without the change.
- For changed behavior or interfaces, update the relevant README, reference
  page, example, and machine-checked transcript where applicable.
- Keep generated run and campaign stores out of commits.
- Do not disclose a vulnerability in an issue or pull request. Follow
  [SECURITY.md](SECURITY.md) instead.

## Local checks

Harness Workbench supports CPython 3.11–3.14 on Linux and macOS. From the
repository root, run:

```console
$ python3 -m unittest discover -s tests
```

Release-affecting changes should also pass the artifact checks documented in
[RELEASING.md](RELEASING.md). Those checks build both distribution forms,
inspect their contents, and exercise each artifact in a clean environment.

## Pull requests

Keep a pull request to one coherent change. Explain the problem, the chosen
approach, user-visible effects, and the checks you ran. Link the prior issue
for a larger change. Maintainer review may ask for a smaller scope or a
different compatibility tradeoff; opening an issue first reduces that risk.
