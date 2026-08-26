# Changelog

This project follows [Semantic Versioning](https://semver.org/) for public
releases. Python package versions use PEP 440; release-candidate Git tags use
the equivalent hyphenated spelling (for example, package `0.1.0rc1` is tagged
`v0.1.0-rc.1`).

## Unreleased

- Add a retained, plan-only-by-default `route_canary.py` for the three adapters
  sharing the configured gateway. It renders each real repair request against
  loopback with a fake key, replays the exact tool-bearing JSON body only after
  a fresh usage gate, stops after the first valid stream event, and fails closed
  on route refusals before the repair matrix can begin. The full certification
  workflow runs this three-call canary first and retains its usage, bounded
  cleanup, gitleaks, and exact credential-absence evidence.
- Add a plan-only-by-default `certify.py` workflow for the exact five-subject,
  three-draw repair recut. One explicit `--live` and a fresh record directory
  authorize at most 33 calls (18 nominal): three non-retrying provider-route
  canaries plus at most 30 subject attempts (15 nominal). The workflow runs
  offline readiness and usage gates first, produces five sealed Workbench
  stores without changing retry/sample semantics, verifies every store, runs
  the exact-five comparator, retains bounded process and usage evidence, scans
  every retained file for configured credential values and gitleaks findings,
  and emits a digest-bound review candidate without editing
  `adapter_certification.json`.
- Add a plan-only-by-default `smoke.py` command that composes owner-only local
  prerequisite loading, hard gateway-usage gates, bounded retained execution,
  post-run usage deltas, independent receipt validation, exact credential-value
  absence checks, and offline postflight into one routine live acceptance path.
- Give Hermes repair runs a workload-specific 180-second bound after a retained
  all-five smoke reached the required red test but exhausted the former
  120-second bound while awaiting the next gateway turn. Hermes write remains
  bounded at 120 seconds. Re-certify the changed 13-input repair apparatus with
  a passing three-draw matrix across all five adapters.
- Add a routine offline adapter preflight that reads a bounded owner-only
  gateway credential without following symlinks, activates the configured
  pinned Hermes environment, and runs the all-five doctor without submitting
  prompts or exposing credential contents.
- Add an experiment-local adapter doctor that submits no prompts and reports
  `ready`, `pin_drift`, `schema_drift`, `auth_missing`, or
  `live_verification_required` from installed identity, local authentication,
  frozen native-lifecycle replay, and reviewed live-certification checks.
- Add a plan-only-by-default recertification runner. `--live` is required to
  authorize bounded subject runs; the default is one draw, the maximum is
  three, and every result is retained with a digest and summary report.
- Ship five minimized native replay fixtures plus a certification manifest
  binding the current repair inputs and capture/canon apparatus to the reviewed
  five-subject 3/3 comparison.
- Repin Claude Code to `2.1.246` after a retained one-draw repair
  recertification matched the prior 3/3 baseline's normalized evidence and
  exact outcome. Keep that smoke result as a separately bound bridge rather
  than presenting it as a replacement three-draw matrix.
- Update the experimental Claude Code subject pin to `2.1.245` and accept an
  omitted optional `is_error` only when the enclosing native result qualifies
  it as a structured success or an error string. A wholly status-free result
  still fails closed.
- Require repair test commands to run standalone so a later chained command
  cannot mask native green-test evidence. A fresh same-apparatus comparison
  passes the adapter and exact repair outcome 3/3 for Claude Code, Codex CLI,
  DeepSeek Harness, Hermes Agent, and Pi, with no timeouts.
- Keep the cross-harness envelope, normalizers, pins, model profiles, and
  workload oracles experiment-local; this stabilization does not promote a
  new supported API.

## 0.1.0 — 2026-08-25

This final promotion carries the verified `0.1.0rc2` contents forward without
accepted candidate fixes. It is a new commit and build; rc2 verification does
not transfer to the final tag or assets, so the complete release gate is run
again.

- Update the experimental Hermes subject from `0.16.0` to official stable
  `0.20.5` / `v2026.8.19`. The pin now binds the annotated release tag object,
  peeled source commit, `uv.lock` digest, and launcher digest. Fresh write,
  guard, repair, and steadiness records verify as complete and conforming;
  exact outcomes succeed, the block guard is bypassed through `terminal`, and
  strict no-allowance steadiness is `UNSTABLE` only on retained stdout bytes.
  Live records from the old pin remain historical.
- Normalize retained subject workload argv to the logical launcher while still
  executing and digest-verifying the resolved pinned file. This fixes npm's
  `dsh` symlink exposing `bin.js` and keeps comparison strict: `bin.js` remains
  a rejected invocation. A post-fix DeepSeek repair recut passes adapter and
  exact repair outcome 3/3 with correlated native tool and terminal evidence.
- Complete a same-apparatus current-source five-subject repair comparison. The
  shared contract passes with no errors while preserving measured negatives:
  Claude fails closed on a changed native tool-result shape, Codex outcome is
  2/3, Hermes is 3/4 with one recovered timeout, and DeepSeek and Pi are 3/3.
- Retain Hermes's strict no-allowance steadiness verdict. All nine exact task
  outcomes are stable, but capture/lifecycle values are all distinct and tool
  routing varies between one and two calls; allowing whole `stdout.bin` axes
  would hide the complete evidence envelope rather than normalize metadata.
- Complete the adapter API/schema promotion review and defer the whole
  `cross-harness-adapter-run/v0.1` envelope for `0.1.0rc2`. Its strict
  exact-five-subject validator remains experiment-local; the supported public
  boundary remains the vendor-neutral `capture` and `canon` primitives.
- **New public module `harness_workbench.capture`.** Bounded subprocess
  execution with per-stream limits, a deadline, process-group termination and
  escalation, and a post-cleanup group-liveness observation; capture envelopes
  carrying stored bytes, source count, digest, overflow and redaction count;
  file-backed capture with `exists`/`required` semantics; tree manifests;
  credential discovery and byte redaction; minimal-environment construction;
  path containment against a declared root; and JSONL decoding. A bound that
  fires is returned as a measurement, never raised and never encoded as a
  synthesized exit code.
- Route the public library surface in the conformance record, and add a
  mechanical check that fails until every module declaring `__all__` is routed
  there. `capture` was added after the published `0.1.0rc1` tag and was
  initially unrouted in the unreleased development tree because no check
  looked at modules; the published rc1 artefacts do not contain it. The check
  reads the shipped subject tree's imports as syntax, resolving
  package-relative spellings (`from .. import runner`) to the same module the
  absolute spelling names, and reads `__all__`
  as an assignment anywhere in a module rather than at the start of a line, so
  neither a declared surface nor a dependency on internal code can move by
  being written a different way. Dynamic `importlib` calls are outside it and
  the record says so. A companion check declares which top-level packages under
  `src/` ship, because `packages.find` ships every one of them and only
  `harness_workbench` was being classified at all.
- **`harness_workbench.canon` now declares `__all__`**: `canon_bytes`,
  `digest_file`, `digest_obj`, `digest_tree`, `file_digests`, `short`. The
  module was already public by use — the shipped subject tree imports it and
  every adapter record digests it — but with no declared surface its functions
  could be added, removed or renamed with nothing failing. Declaring it also
  stops `from harness_workbench.canon import *` re-exporting `hashlib`, `json`,
  `os` and the typing names.
- **Source distributions are no longer a function of the builder's umask.**
  `normalize_sdist.py` already neutralized ownership and timestamps but copied
  each member's mode straight from the checkout, so building one commit under
  `umask 077` produced different bytes than under `umask 022`. Modes are now
  reduced to `0644`, `0755` for directories and executables, and `0777` for
  links — the executable bit is the only one Git tracks and the only one kept.
  **This changes the bytes of any sdist built from this tree.** The artifact
  verifier enforces the neutral modes, and rejects a wheel carrying umask-
  derived modes with a message naming the cause, because nothing normalizes a
  wheel the same way and the release procedure compares the two builds for byte
  identity.
- **The build backend is pinned for the release path.** `[build-system]`
  declares `setuptools>=77`, a floor, and `python -m build` resolves a floor
  freshly in an isolated environment — so the same commit, on one machine, at
  one umask, produced different wheel bytes under setuptools 79.0.1 and 84.0.0,
  including different member modes. The `release` extra now pins
  `setuptools==83.0.0` beside `build` and `twine`, and the gate builds with
  `--no-isolation` so the pin is the backend that actually runs. The floor still
  governs anyone building from source. `RELEASING.md` writes the three versions
  literally and a test reconstructs that install command from the extra, so the
  two spellings cannot drift.
- **Archive modes are checked against the source tree, not against themselves.**
  The verifier asked each member's own executable bit what its mode should have
  been, which every archive satisfies: an sdist with every regular member
  rewritten to `0755` verified, and so did a wheel with the executable bit
  stripped off the shipped `run_subject.sh`. Permission bits are now compared
  against a constant set, and the set of executable members must equal the set
  the checkout marks executable — the one permission bit Git records — with
  both directions of disagreement named in the failure.
- **`RELEASING.md` step 1 no longer installs the project.** It ended with
  `pip install '.[release]'`, which runs an in-tree build and leaves a `build/`
  directory; step 2's first precondition is that no `build/` exists. Step 2
  could not start, and the fresh clone step 2's prose recommended landed in the
  same state. Only the pinned tools are installed now, and step 2's version
  check reads the source tree rather than an installed copy — which is what it
  should have been checking anyway.
- Identify a subject call by its request and its id rather than by its id
  alone, which is not unique.
- Correct the live-subject prerequisites: the committed `opencode-go` profile
  requires a valid API key, outbound network access, and a potentially
  spend-bearing remote call. Hermes uses that remote profile; `local-ollama`
  is a separate optional local profile.
- Record the successful hosted CI and CodeQL preparation checks for exact PR
  checkpoint `f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3` without treating them
  as tag, asset, or release-final evidence.
- Keep the source boundary explicit: audit remediation beginning at commit
  `05054d60c816309ea6346f2d34be1c654e7f5697` and its follow-up fixes come after
  hosted checkpoint `f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3`. Those later
  commits require exact-head hosted checks after publication; the checkpoint's
  results do not transfer forward.

## 0.1.0rc1 — 2026-08-12

This source was published as the public GitHub prerelease
[`v0.1.0-rc.1`](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.1).
The release carries the signed tag, wheel, source distribution, checksums, and
release-final conformance record. It is not published to PyPI.

- Run JSON specs as ordinary subprocess workloads with append-only attempt
  evidence and zero runtime dependencies.
- Load opt-in, declared features at fixed seams and preserve their source with
  each run.
- Measure confinement, effects, blast radius, interruption recovery,
  steadiness, efficacy, sensitivity, feature interference, fidelity, and
  replay behavior.
- Fail closed on incomplete closure metadata, unsafe store overlap, unsafe
  replicate identifiers, unsupported artifact types, and malformed feature
  annotations.
- Ship six builtin example features plus documentation and network-free
  examples.
- Support CPython 3.11–3.14 on Linux and macOS; Windows is unsupported.
- State the trusted-code execution boundary, publish a private vulnerability
  reporting policy, and add pinned CodeQL and Dependabot configuration.
- Normalize release source distributions to remove local ownership metadata,
  use the release commit timestamp, and reject unsafe archive paths or links.
- Use unmistakably synthetic redaction sentinels in current source, preserving
  the redaction rejection test without credential-shaped fixture data. Narrow
  the history scanner's sole exception to the exact removed fixture, path, and
  introducing commit.

## Published releases

- [`0.1.0` — 2026-08-25](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0)
  — first final public GitHub release; not published to PyPI.
- [`0.1.0rc2` — 2026-08-25](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.2)
  — second public GitHub prerelease; not published to PyPI.
- [`0.1.0rc1` — 2026-08-12](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.1)
  — first public GitHub prerelease; not published to PyPI.

[Semantic Versioning]: https://semver.org/
