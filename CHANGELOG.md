# Changelog

This project follows [Semantic Versioning](https://semver.org/) for public
releases. Python package versions use PEP 440; release-candidate Git tags use
the equivalent hyphenated spelling (for example, package `0.1.0rc1` is tagged
`v0.1.0-rc.1`).

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
