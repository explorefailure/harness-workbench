# Support for Explore Failure - Harness Workbench

Harness Workbench is an actively developed, solo-maintained open-source
project. Help is provided on a best-effort basis; there is no guaranteed
response time, resolution, compatibility exception, or support SLA.

## Where to ask

Use [GitHub Issues](https://github.com/explorefailure/harness-workbench/issues)
for usage questions and non-sensitive bug reports. Search existing issues
first. For a bug, include the Harness Workbench version or commit, Python
version, operating system, installation method, a minimal reproduction, and
the expected and actual behavior. Remove secrets and private data from specs,
commands, logs, and run records before posting them.

Questions about unsupported operating systems, Python versions outside the
documented range, deployment environments, or third-party workloads may be
answered when capacity permits but are not compatibility commitments.

## Security issues

Do not report vulnerabilities, exploit details, secrets, or proof-of-concept
code in a public issue. Use the private route and fallback procedure in
[SECURITY.md](SECURITY.md).

## Pi builder integration candidate

The unreleased builder workflow is documented in [the Pi guide](docs/pi-builder.md).
Support scope is the patched default profile, commands and evidence contract in
that guide. The old baseline is a historical comparison, not the recommended
runtime. Research-only oracles are not a general compatibility promise.
Upstream protocol drift should fail visibly rather than silently relaxing the
checks. Report the selected profile and exact Pi/Node versions with integration
bugs. CI configuration alone is not evidence of a platform pass; require successful
checks on the exact release candidate, including the patched runtime audit.

## OpenCode builder integration candidate

See [the OpenCode guide](docs/opencode-builder.md). Support is limited to the
pinned runtime, isolated CLI JSON capture, reference repair and explicit local
OpenAI-compatible endpoint configuration. Verify exact-candidate platform checks;
pinned downloads alone do not establish cross-platform runtime compatibility.
