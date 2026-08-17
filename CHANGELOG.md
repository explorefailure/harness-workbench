# Changelog

This project follows [Semantic Versioning](https://semver.org/) for public
releases. Python package versions use PEP 440; release-candidate Git tags use
the equivalent hyphenated spelling (for example, package `0.1.0rc1` is tagged
`v0.1.0-rc.1`).

## 0.1.0rc2 — unreleased

Preparation note: this candidate has no tag, no GitHub release, and no assets.
Nothing below is published. The heading gains a date only at step 1 of
[`RELEASING.md`](RELEASING.md).

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
  there. `capture` reached the published `0.1.0rc1` candidate unrouted because
  no check looked at modules.
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
- Identify a subject call by its request and its id rather than by its id
  alone, which is not unique.

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

- [`0.1.0rc1` — 2026-08-12](https://github.com/explorefailure/harness-workbench/releases/tag/v0.1.0-rc.1)
  — first public GitHub prerelease; not published to PyPI.

[Semantic Versioning]: https://semver.org/
