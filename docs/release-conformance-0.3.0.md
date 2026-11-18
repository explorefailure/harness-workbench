# Explore Failure - Harness Workbench 0.3.0 release conformance record

> **Prepared candidate record — NOT RELEASED.** This source record describes
> the intended 0.3.0 release boundary. It must not be read as proof that a
> `v0.3.0` tag, GitHub release, or downloadable assets exist. Release-final
> evidence is added to the copy published beside the immutable assets after
> every gate in `RELEASING.md` passes.

## Candidate identity

| Field | Candidate value |
| --- | --- |
| Package | `harness-workbench` |
| Import | `harness_workbench` |
| CLI | `hwb` |
| Version | `0.3.0` |
| Intended tag | `v0.3.0` |
| Release commit | **PENDING — BLOCKING PUBLICATION** |
| Signed tag | **PENDING — BLOCKING PUBLICATION** |
| GitHub assets | **PENDING — BLOCKING PUBLICATION** |
| Package index | Not part of this release procedure |

The preceding public release is
[`v0.2.0`](https://github.com/explorefailure/harness-workbench/releases/tag/v0.2.0),
published 2026-11-08. Its checks and assets do not establish 0.3.0.

## Intended release contents

The candidate adds:

- `hwb lab`, a localhost web GUI for the Reality Check (Discover, Try, Proof,
  Recipe, History) with per-launch token, Host allowlist, and same-origin
  checks, run progress, and clean shutdown of a running check;
- `hwb setup`, which reports whether this machine can run the Reality Check and
  how to install each missing prerequisite;
- a Reality Check that runs on the Pi, Node, and Python the user has and records
  their versions instead of refusing them, plus Pi 1.x exit-status parsing;
- a local llama.cpp model runtime (`--profile local-llamacpp`) identified by the
  served GGUF's SHA-256;
- a Recipe model baseline taken from this machine instead of a shipped digest,
  and automatic refresh of an unedited Recipe made by another version; and
- install instructions corrected from a newcomer test on a clean Linux machine.

The promoted 2026-11-17 live certification binds the exact repair apparatus and
records Claude Code, Codex CLI, DeepSeek Harness, Hermes Agent, and Pi at 3/3
adapter success and 3/3 exact repair outcome with zero timeouts. This is bounded
certification evidence, not a model-quality score or a substitute for the
source and artifact release gates.

## Public and experimental boundaries

The stable public library boundary remains the documented Workbench modules,
including `harness_workbench.capture` and `harness_workbench.canon`. The
cross-harness envelope, normalizers, third-party pins, model profiles, workload
oracles, builder internals, and live-certification machinery remain
experiment-local or CLI surfaces rather than a newly promoted Python API.

Harness Workbench is an execution and measurement tool, not a security sandbox.
Workloads, feature modules, callbacks, and selected harnesses execute with the
user's permissions. A passing capture without declared independent checks does
not prove task correctness. Tested local protocol fixtures do not establish
every hosted account, model, service, or platform combination.

## Required release evidence

Publication is blocked until all of the following are recorded for one exact
release commit:

1. A clean source checkout and history-wide Gitleaks scan.
2. The complete main test suite and offline shipped-subject suite.
3. A pinned-backend wheel and normalized source distribution built under the
   required umask and commit-derived `SOURCE_DATE_EPOCH`.
4. Twine, archive-content, executable-mode, credential, checksum, and clean
   installed-artifact verification for both artifacts.
5. A second build from the exact commit served by GitHub with byte-identical
   `SHA256SUMS`.
6. Required Linux/macOS Python, package, builder compatibility, and CodeQL jobs
   for the exact release commit.
7. A signed immutable `v0.3.0` tag and successful tag-triggered checks.
8. A GitHub release containing exactly the reviewed wheel, normalized source
   distribution, checksums, and release-final conformance record.
9. Downloaded-asset checksum, archive, and clean-install verification.

PR checks, the reviewed live certification, and local preparation tests are
historical inputs to this decision. None transfer to the future tag or assets.

## Claim and instruction surfaces

The release review covers `README.md`, `CHANGELOG.md`, `RELEASING.md`,
`CONTRIBUTING.md`, `SUPPORT.md`, `SECURITY.md`, `LICENSE`, `NOTICE`,
`pyproject.toml`, `.github/`, `examples/`, all Markdown under `docs/`, and the
shipped subject documentation under `src/harness_workbench/subjects/`.
The historical records `docs/release-conformance-0.1.0.md` and
`docs/release-conformance-0.2.0.md` remain evidence for those releases only.

Command-surface review includes `hwb --help`, `hwb --version`,
`python -m harness_workbench --help`, all names returned by the command
registry, `hwb try --help`, `hwb setup --help`, `hwb lab --help`, `hwb-pi --help`, `hwb-opencode --help`, and
`hwb-hermes --help`. The package's routed public-library manifest remains the
authority for importable API; the builder and subject CLIs do not promote their
implementation modules.

## Release-final fields

The published copy must add the exact commit and tag identities, GitHub Actions
and CodeQL URLs, security-setting verification, artifact filenames and SHA-256
values, local and remote reproducibility results, downloaded-asset results,
assessor/approver identity, and all residual limitations. The tagged source
record remains a pre-publication record because its own commit identity cannot
be inserted without changing that identity.
