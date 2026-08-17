# Harness Workbench 0.1.0rc2 release conformance record

> **Frozen candidate record — NOT RELEASED.** This is the repository-owned
> pre-release record for an intended R2 public release. It records private
> preparation CI separately from release-final evidence and is not evidence
> that a tag, GitHub release, public security setting, or downloadable release
> asset exists. Rows marked **BLOCKING PUBLICATION** must be closed with
> release-final evidence before this record can describe a released artefact.

> **This record covers candidate `0.1.0rc2`, which does not exist yet.** The
> preceding candidate `0.1.0rc1` *was* published, as the public GitHub
> prerelease `v0.1.0-rc.1` on 2026-08-12; its own release-final record is
> attached to that prerelease as an asset and is the content-addressed record
> for those bytes. This file is not that record and does not describe it. The
> in-tree record always describes the candidate being prepared, so it was
> renamed from `docs/release-conformance-0.1.0rc1.md` rather than copied:
> keeping a second in-tree rc1 record would put a stale duplicate beside the
> published one and let them disagree.

## Artefact identity and declared reach

| Field | Value |
|---|---|
| Artefact | Harness Workbench |
| Python distribution | `harness-workbench` |
| Import package / command | `harness_workbench` / `hwb` |
| Intended version | `0.1.0rc2` |
| Intended readable tag | `v0.1.0-rc.2` |
| Preceding published candidate | `0.1.0rc1` / `v0.1.0-rc.1`, published 2026-08-12. Its public API did not include `harness_workbench.capture`; see the library-surface row below. |
| Lifecycle state | `frozen release candidate; unreleased` |
| Declared reach | **R2 — public**, declared 2026-08-11 for the intended release |
| Public attribution | Approved 2026-08-12: Garrett Davis is intentionally public as copyright holder, package author, maintainer, and Git identity associated with Explore Failure. The existing GitHub account association in the reviewed history is intentional. |
| Preparation baseline | Git commit `db8b426366b7a5a0775449369b24971b04f1bb1f`. **This baseline predates candidate `0.1.0rc2`.** It was the preparation baseline for `0.1.0rc1`; no hosted evidence of any kind yet exists for the `0.1.0rc2` source. |
| Preparation hosted CI | All eight Linux/macOS CPython 3.11–3.14 jobs plus the package job passed for the preparation baseline in [private CI run 31625746283](https://github.com/explorefailure/harness-workbench/actions/runs/31625746283). This is not release-final evidence for the containing freeze commit, and it is **not evidence for `0.1.0rc2` at all** — that source did not exist when the run executed. |
| Preparation CodeQL | [Private run 31625748519](https://github.com/explorefailure/harness-workbench/actions/runs/31625748519) extracted 45/45 Python files, ran its queries, and exported SARIF, but the Analyze step failed when GitHub returned `Resource not accessible by integration` during upload/status access. No CodeQL pass or uploaded result is claimed. |
| Superseded freeze evidence | Signed commit `bbcb6fbcd0a873eb3589028119e8b9489179fe34` passed all nine jobs in [public CI run 31627092506](https://github.com/explorefailure/harness-workbench/actions/runs/31627092506). [Public CodeQL run 31628103134](https://github.com/explorefailure/harness-workbench/actions/runs/31628103134) uploaded successfully but reported one high-severity clear-text-storage alert on a credential-shaped synthetic test fixture. That commit is preparation evidence only and was superseded by a source-level fixture remediation; no dismissal or scanner exception was used. |
| Record-preparation revision | The signed freeze commit containing this file; resolve with `git rev-parse HEAD` in the reviewed checkout |
| Release commit | **PENDING — BLOCKING PUBLICATION.** The containing source-freeze commit cannot name its own hash; exact remote identity and release-final evidence must be recorded after creation and before publication. |
| Release tag / GitHub release | **PENDING — BLOCKING PUBLICATION.** Neither exists. |
| Release wheel, sdist, and checksums | **PENDING — BLOCKING PUBLICATION.** Locally built preparation artefacts are disposable self-run evidence, not released assets. |

The release commit cannot be written inside the commit it identifies without
creating a self-reference. At freeze, the exact 40-character commit is recorded
in the signed tag, the GitHub release, and the finalized copy of this record
distributed beside the release assets. Until those agree, this record remains
prepared rather than released.

## Governing standards pins

Both standards were read from the clean committed versions in the Explore
Failure standards repository. Their source files had no working-tree changes
when pinned.

| Standard | Version | Content-addressed source commit | Git blob | File SHA-256 |
|---|---|---|---|---|
| Explore Failure Software Release Standard (`EF-SRS`) | `0.4.0` | `671379e920e64fa0c68c5086f0acac4c1512d4f6` | `03e0211f8784c28aa87be8978108e753c6b64088` | `a6a89937ede2b7e672868d75d995604b0ec4c2f15169d79d7ac114477915cf85` |
| Explore Failure Research Release Standard (`EF-RS-REL`) | `0.4.0` | `671379e920e64fa0c68c5086f0acac4c1512d4f6` | `d4d8d5cd278bbe0f9dffe2661ef09e851e87d028` | `81bf7d24f775355459a0787f6f54bcc68f08fe04abffce32a12ae9d1c94347cb` |

The standards repository is not included in this artefact and no public URL
for these exact bytes is asserted. The hashes make drift detectable when the
source is available; departure `D-08` records the recipient consequence.

## Maintenance posture (verbatim)

The first recipient surface, [README.md](../README.md), states:

> Maintenance status: **actively developed, solo maintained**. Focused bug fixes,
> documentation, and tests are welcome for best-effort review; larger changes
> should start with an issue. There is no response, merge, compatibility, or
> support SLA.

The operational detail is in [CONTRIBUTING.md](../CONTRIBUTING.md),
[SUPPORT.md](../SUPPORT.md), and [SECURITY.md](../SECURITY.md).

## EF-SRS release gate

| Gate | Pre-release status | Exact evidence / remaining predicate |
|---|---|---|
| `EF-SRS-01` — purpose and liveness | **MET IN PREPARED SOURCE** | README opening states the problem and behavior; the verbatim maintenance posture appears above and in README. The release-final README status must still be checked after freeze. |
| `EF-SRS-02` — licence granted | **MET IN PREPARED SOURCE** | `LICENSE` contains Apache License 2.0; `NOTICE` names the copyright holder; `pyproject.toml` declares SPDX `Apache-2.0` and both licence files; `tools/verify_release_artifacts.py` checks wheel/sdist metadata and bytes. |
| `EF-SRS-03` — first run from received artefact | **BLOCKING PUBLICATION** | The exact wheel and sdist path below pass locally as author-side self-run preparation. No tagged or GitHub-downloaded release asset exists, so the artefact a public recipient will receive has not been verified. |
| `EF-SRS-08` — claim routing | **BLOCKING PUBLICATION** | Every current repository surface is routed below and mechanically inventoried. The final release notes/assets do not exist; hosted matrix, disclosure review, correction/security-route test, and exact release identity remain unresolved at T1. |

**Gate decision:** do not publish. `EF-SRS-03` and `EF-SRS-08` remain blocking
for the actual release even though their repository preparation is in place.

### EF-SRS non-gate assessment

| Requirement | Pre-release status | Evidence / departure |
|---|---|---|
| `EF-SRS-04` — examples do not narrow the tool | **MET IN PREPARED SOURCE** | The first recommended example is model-free; examples cover shell workloads, local-model adjacency, custom features, failures, retries, redaction, and bounded filesystem effects. README explicitly says Ollama is an example rather than a requirement. |
| `EF-SRS-05` — documentation separated by reader need | **CONFORMS FOR THE R2 SHOULD** | README routes learning/first-run, doing, reference, feature-authoring, measurement, experiment interpretation, and release/conformance needs to separate pages. |
| `EF-SRS-06` — contribution/support posture | **MET IN PREPARED SOURCE** | The verbatim posture above links to the actual solo-maintainer process and best-effort support route without promising governance or an SLA. |
| `EF-SRS-07` — hand-off sufficiency | **MET IN PREPARED SOURCE** | `CONTRIBUTING.md` gives tests; README gives development/build checks; `RELEASING.md` gives clean source, artifact, CI, tag, release, download, and promotion procedures. Its steps 1 and 2 are now **completely executable offline** and were run end to end for this candidate with no step skipped: pristine checkout from the local repository, history scan, full suite, build, normalization, strict Twine, artifact verification, archive scan, separate clean installs, and checksums. Publication moved to step 3, so no candidate is pushed before its own gate has passed it. External release steps remain explicitly pending. |
| `EF-SRS-09` — mechanical checks | **CONFORMS FOR THE R2 SHOULD IN SOURCE; EXTERNAL CHECKS PENDING** | The table below identifies the machine checks and where each runs. `D-03`, `D-05`, `D-06`, and `D-07` prevent checked-in configuration from being reported as hosted evidence. |
| `EF-SRS-10` — recorded departure | **MET** | `D-01` through `D-09` name every known unmet predicate, the reason, recipient consequence, and closure evidence. |

## Verified first-run record

### Assurance label and verifier

- Label: **Self-run verification (preparation only)**.
- Verifier: maintainer-side author-context verification on 2026-08-11.
- Relationship and prior access: performed with full source-tree and
  implementation context; not an outsider and not independent.
- Owner help/intervention: the maintainer side prepared this record and its
  checks before running them. No outside operator was involved.
- Credentials/network: no application credentials; the unit suite and first
  run are network-free. Package build isolation may obtain declared build
  tooling if it is not already cached.
- Starting environment: macOS/arm64 with CPython 3.11 for the package gate.
  This local environment is not a substitute for the required hosted
  Linux/macOS CPython 3.11--3.14 matrix.
- Source identity: the signed source-freeze commit containing this record. Its
  own hash cannot be embedded here; exact remote identity remains required as
  release-final evidence before publication.

### Exact artefact forms and commands

The two received forms tested locally are one wheel and one source distribution
built from a clean archive of the record-preparation commit:

```sh
python3.11 -m unittest discover -s tests -v
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
export SOURCE_DATE_EPOCH
RAW_SDIST_DIR="$(mktemp -d)"
python3.11 -m build --wheel --outdir dist
python3.11 -m build --sdist --outdir "$RAW_SDIST_DIR"
python3.11 tools/normalize_sdist.py "$RAW_SDIST_DIR"/*.tar.gz --output-dir dist
rm -rf -- "$RAW_SDIST_DIR"
python3.11 -m twine check --strict dist/*.whl dist/*.tar.gz
python3.11 tools/verify_release_artifacts.py dist
python3.11 tools/verify_installed_artifact.py dist/*.whl
python3.11 tools/verify_installed_artifact.py dist/*.tar.gz
python3.11 tools/release_checksums.py write dist
python3.11 tools/release_checksums.py check dist
```

`verify_installed_artifact.py` creates a new temporary virtual environment for
each form, removes `PYTHONPATH`, sets `PYTHONNOUSERSITE=1`, installs only the
named archive, runs `pip check`, checks installed metadata/import identity,
and exercises both command entry forms. It then creates this exact spec in an
otherwise empty temporary working directory:

```json
{"schema":"hwbspec/v0.1","features":[],"steps":[{"id":"01","argv":["/bin/echo","hello"]}]}
```

The public path it executes is equivalent to:

```sh
hwb --version
hwb --help
python -m harness_workbench --help
hwb run hello.json
hwb ls
hwb show "$RUN_ID"
hwb verify "$RUN_ID"
```

Expected and observed preparation outcome: installation succeeds; `pip check`
reports no broken requirements; package, metadata, `hwb --version`, and source
versions agree at `0.1.0rc2`; the run reports `completed`; `hwb ls` contains its
run ID; `show` and `verify` accept the complete record. The disposable local
wheel and ownership-neutral normalized sdist passed on 2026-08-12. Every member
of that normalized sdist had `0:0` / `root:root` ownership and the
release-source commit timestamp; a second normalization was byte-identical.
This supplies no outside assurance and does not verify a future tagged or
downloaded byte sequence.

Release-final evidence must add the frozen commit, asset filenames and SHA-256
values, hosted run URLs, downloaded-asset commands/results, verifier identity,
owner intervention, and final disposition. An outsider run may be recorded as
an outside artifact check only if the EF-RS-REL separation predicate is met.

## Claim card

- **Release and tier:** Harness Workbench `0.1.0rc2`; deterministic software
  and conformance claims are routed at **EF-RS-REL T1**; H/F overlay is not
  triggered because the release has no human-subject, private-person, field,
  or human-effect evidence or claim.
- **Question:** can the stated package execute a JSON workload, retain the
  documented evidence, exercise the documented harness-measurement paths
  within their named boundaries, and expose the public library surface
  routed above — which for this candidate newly includes the capture
  primitive — with the documented behaviour at its bounds?
- **Exact system and conditions:** the content-addressed public release
  identity is pending; package version `0.1.0rc2`, CPython 3.11–3.14 target,
  Linux/macOS target, POSIX command environment, no runtime dependencies.
- **Preparation observation:** source tests and separate clean installs from a
  locally built wheel and sdist pass on the macOS environment recorded above.
- **What that supports:** author-side evidence that the prepared source can
  produce installable archives whose documented minimal `/bin/echo` run works.
- **What it does not support:** publication, availability, hostile-code
  containment, Windows compatibility, workload quality, model quality,
  correctness outside documented invariants, all-Python/all-OS compatibility,
  an independent artifact check, independent reproduction, or long-term
  maintenance.
- **Important threats:** author-side verification shares implementation
  assumptions; the final commit and hosted bytes may differ; only one local OS
  supplies preparation evidence; generated and prose documentation checks are
  incomplete by design; security settings live outside Git.
- **Outside assurance:** none.
- **Withholding:** no claim-sufficient run evidence is intentionally withheld,
  but the governing standards source is not shipped and external GitHub state
  is not yet available. See `D-08` and the pending release rows.
- **Current version / correction route:** this is an unreleased frozen-candidate
  record. For a public release, GitHub Issues is the planned correction route
  and GitHub private vulnerability reporting is the planned confidential route;
  neither route is claimed tested here.

## Claim-to-evidence trace

The claim ceiling for every row is the bounded wording in this table. Repeated
wording may reuse the claim ID; a new population, condition, result,
uncertainty, or assurance meaning requires a new ID or revision.

| Claim ID and bounded claim | Principal source / evidence | Expected and observed result | Public verification state |
|---|---|---|---|
| `C-HWB-01` — the distribution/import/CLI are `harness-workbench` / `harness_workbench` / `hwb`, version `0.1.0rc2`, with zero runtime dependencies | `pyproject.toml`, `src/harness_workbench/__init__.py`, built `METADATA`/`PKG-INFO`, `tools/verify_release_artifacts.py`, installed-artifact verifier | Exact identity/version/dependency agreement; passed for local wheel and sdist | Self-run preparation; final assets absent |
| `C-HWB-02` — the minimal feature-free POSIX spec runs and leaves a complete verifiable record | README first run, `examples/bare.json`, runner tests, installed-artifact verifier | `run` completed; ID visible to `ls`; `show` and `verify` exit zero; passed locally | Self-run preparation; final assets absent |
| `C-HWB-03` — run records preserve the documented attempts/spec/feature-source/integrity surfaces, with stated partial-run classifications | `docs/the-record.md`, runner/conformance/interrupt tests, artifact-content checks | Documented keys/layout match generated records and known incomplete/recoverable/complete cases | Reconstructible from source tests; no outside check |
| `C-HWB-04` — six opt-in builtin features expose the documented powers and seams, and an omitted/mistyped root does not silently load them | README feature table, `docs/writing-a-feature.md`, builtin manifests/source, feature/dispatcher tests | Builtin names, seam table, powers, load failure, source preservation match code | Reconstructible from source tests; no outside check |
| `C-HWB-05` — measurement commands implement only the bounded relations and verdicts documented for them | `docs/measuring.md`, `docs/campaign-manifests.md`, `docs/measuring-your-own-code.md`, `src/harness_workbench/commands.py`, behavioral and sensitivity tests | Registered command/verdict universe, schemas, transcripts, known-red probes, and named limitations agree | T1 self-run source evidence; campaign generality is not claimed |
| `C-HWB-06` — selected documentation surfaces are mechanically checked, not guaranteed as prose | docs drift/transcript tests, the experiment-learning-record check, and `tests/test_release_engineering.py` | Field/table/command inventories, registered transcripts, and per-experiment learning-record presence pass; unregistered transcript ceiling does not rise | Self-run; exact checked surfaces are named in README and `docs/experiment-writeups.md` |
| `C-HWB-07` — the tool executes trusted commands/modules and replay is not OS isolation | README, `SECURITY.md`, all core docs, command/module execution tests | Required trust-boundary wording is present; misleading sandbox wording is rejected | Inspectable source plus self-run tests; not a security audit |
| `C-HWB-08` — examples demonstrate model-free, model-adjacent, feature-attachment, retry/redaction, and bounded-effects use without making a provider a requirement | exact example inventory below, example transcripts, package-content verifier | Shipped examples are included in sdist; selected transcripts re-run; no runtime dependency or network reference in the unit suite | Partly machine checked; unregistered examples remain inspectable demonstrations |
| `C-HWB-09` — CPython 3.11–3.14 on Linux/macOS is the intended v0.1 support target; Windows is unsupported | README, `pyproject.toml`, CI matrix | Private preparation CI passed all eight Linux/macOS CPython 3.11–3.14 jobs at `db8b426366b7a5a0775449369b24971b04f1bb1f`; the exact freeze-commit matrix is **pending** | Preparation evidence only; not release-final evidence |
| `C-HWB-10` — maintenance, contribution, support, and security posture are the policies stated, without an SLA | README, `CONTRIBUTING.md`, `SUPPORT.md`, `SECURITY.md`, intake templates | Wording/routes agree mechanically | Policy declaration; EF-RS observed-result predicate not triggered |
| `C-HWB-11` — this candidate (`0.1.0rc2`) is frozen and has not been tagged, released, or made public; the preceding candidate `0.1.0rc1` was published as prerelease `v0.1.0-rc.1` and the source surfaces say so | README, `CHANGELOG.md`, absence of a `v0.1.0-rc.2` tag, this record | Source surfaces consistently distinguish this unreleased candidate from the already-published one, and do not let the published prerelease stand as evidence for this source | `v0.1.0-rc.2` tag/release/public state must be verified before publication; the rc1 prerelease is not evidence for rc2 |
| `C-HWB-12` — release archives include the expected source/docs/examples/tests/tools, exclude generated stores, carry agreeing metadata/licences, and install separately; the release sdist carries neutral ownership and a commit-derived timestamp | `MANIFEST.in`, `pyproject.toml`, `tools/normalize_sdist.py`, `tools/verify_release_artifacts.py`, release tests | Strict Twine, archive inspection, normalized tar/gzip metadata, wheel/sdist clean install, checksums pass locally | Self-run preparation; final downloaded assets absent |
| `C-HWB-13` — `harness_workbench.capture` is public, importable API; its `__all__` is exactly the 24 names listed in the public-library manifest, in both directions; and a bound that fires is returned as a measurement rather than raised or encoded as a synthesized exit code | `src/harness_workbench/capture.py` and its `__all__`, `docs/adapter-primitive-extraction.md`, the capture unit tests, the determinism soak over success/nonzero/malformed/saturation/timeout/ignored-termination/orphan/corrupt-evidence cases | Every exported name appears in the manifest and the manifest names no attribute outside `__all__`, so adding or removing an export fails the suite; timeout, byte-limit and nonzero exit are reported in `termination_reason` with the real `returncode` preserved; the soak holds one projection digest across repeated runs | Self-run source evidence. **Surface added after the published `0.1.0rc1`**; no recipient of that prerelease received this module, and no downloaded-asset evidence exists for it |

## Complete claim-bearing surface inventory

Every current public-facing or release-facing repository surface is assigned a
route. The exact path manifest below is machine checked; adding a root Markdown
file, docs page, shipped example, GitHub workflow/template, or public CLI
command fails the suite until this record routes it.

| Surface group | Exact surfaces | EF-RS-REL route |
|---|---|---|
| Front door | `README.md` | T1: `C-HWB-01` through `C-HWB-12`; purpose, ceiling, evidence state, limits, and routes |
| Reference and guides | `docs/adapter-primitive-extraction.md`; `docs/campaign-manifests.md`; `docs/experiment-writeups.md`; `docs/measuring-your-own-code.md`; `docs/measuring.md`; `docs/the-record.md`; `docs/the-spec.md`; `docs/writing-a-feature.md` | T1: primarily `C-HWB-02` through `C-HWB-08`; the experiment template constrains interpretation and code-promotion claims rather than adding a performance claim. The extraction memo records an internal code-promotion decision and makes no claim about any third-party harness's behaviour |
| This conformance/front-door supplement | `docs/release-conformance-0.1.0rc2.md` | T1 claim card, trace, assurance, departures, and current status |
| Examples and demonstration code/data | every exact `examples/` path in the manifest below | T1 demonstrations: `C-HWB-02`, `C-HWB-04`, `C-HWB-05`, `C-HWB-08`; no performance/generalization claim |
| CLI/help and runtime claims | `hwb --help`; `hwb --version`; `python -m harness_workbench --help`; every exact subcommand help surface below; runtime output from those commands | T1: `C-HWB-01` through `C-HWB-07`; source of truth is `src/harness_workbench/commands.py` plus parser/behavior tests |
| Public library API | every module in the exact public-library manifest below | T1: `C-HWB-13`; importable surface a recipient can depend on without going through `hwb`. `harness_workbench.capture` is **new in `0.1.0rc2`** and was not present in the published `0.1.0rc1`. |
| Package-index metadata | `pyproject.toml`; generated wheel `METADATA`; generated sdist `PKG-INFO`; README rendered as long description | T1: `C-HWB-01`, `C-HWB-09`, `C-HWB-10`, `C-HWB-12`; no package-index publication is authorized or claimed |
| Release history/process | `CHANGELOG.md`; `RELEASING.md`; future Git tag; future GitHub release notes; future wheel/sdist/`SHA256SUMS` | T1: `C-HWB-11`, `C-HWB-12`; future surfaces are **pending and must be reviewed before publication** |
| Licence, continuity, and reporting | `LICENSE`; `NOTICE`; `CONTRIBUTING.md`; `SUPPORT.md`; `SECURITY.md` | Licence routes under EF-SRS-02; policy declarations under `C-HWB-10`; any factual security/result claim routes at T1 under `C-HWB-07` |
| GitHub intake and automation | all exact `.github/` paths in the manifest below | Workflow/configuration facts route at T1 under `C-HWB-09`/`C-HWB-12`; intake wording routes under `C-HWB-10`; hosted execution/settings remain pending |

### Exact shipped-example manifest

- `examples/attaching/check.sh`
- `examples/attaching/features/myfeature/FEATURE.json`
- `examples/attaching/features/myfeature/feature.py`
- `examples/attaching/mine.json`
- `examples/attaching/workload.json`
- `examples/bare.json`
- `examples/determinism-14b.json`
- `examples/determinism.json`
- `examples/echo.sh`
- `examples/effect-boundary/README.md`
- `examples/effect-boundary/breach.json`
- `examples/effect-boundary/clean.json`
- `examples/effect-boundary/features/spill/FEATURE.json`
- `examples/effect-boundary/features/spill/feature.py`
- `examples/effect-boundary/state/README.txt`
- `examples/effect-boundary/write-allowed.sh`
- `examples/flaky/README.md`
- `examples/flaky/config.txt`
- `examples/flaky/flaky.sh`
- `examples/flaky/noretry.json`
- `examples/flaky/redact.json`
- `examples/flaky/retry.json`
- `examples/flaky/stable.json`
- `examples/flaky/stable.sh`
- `examples/leak.sh`
- `examples/ollama_probe.py`
- `examples/p1.txt`
- `examples/p2.txt`
- `examples/prompts/q1.txt`
- `examples/prompts/q2.txt`
- `examples/prompts/q3.txt`
- `examples/redact.json`
- `examples/retry.json`
- `examples/smoke.json`

### Exact GitHub-surface manifest

- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/change_proposal.yml`
- `.github/ISSUE_TEMPLATE/config.yml`
- `.github/ISSUE_TEMPLATE/usage_question.yml`
- `.github/dependabot.yml`
- `.github/pull_request_template.md`
- `.github/workflows/ci.yml`
- `.github/workflows/codeql.yml`

### Exact CLI/help manifest

- `hwb --help`
- `hwb --version`
- `python -m harness_workbench --help`
- `hwb run --help`
- `hwb subjects --help`
- `hwb ls --help`
- `hwb show --help`
- `hwb verify --help`
- `hwb diff --help`
- `hwb sweep --help`
- `hwb interfere --help`
- `hwb blast --help`
- `hwb catch --help`
- `hwb fidelity --help`
- `hwb sensitivity --help`
- `hwb efficacy --help`
- `hwb steady --help`
- `hwb effects --help`
- `hwb interrupt --help`
- `hwb order --help`
- `hwb confine --help`
- `hwb replay --help`
- library verdict surface `harness_workbench.conform` (no top-level command)

### Exact public library-module manifest

A module is public here on any of three grounds: it declares its own `__all__`,
the shipped subject tree imports it (making it API a recipient receives and
runs, whatever the declaration says), or it is routed by name as a library
surface with no top-level command. All three are claim bearing — a recipient
can import them, and nothing in the CLI manifest above would notice if their
surface changed.

- `harness_workbench.capture` — **new in `0.1.0rc2`.** Declares `__all__`, and
  its exact exported names are:
  `Bounded`, `CREDENTIAL_MARKERS`, `CREDENTIAL_MIN_LENGTH`, `CaptureError`,
  `DEFAULT_SIDECAR_LIMIT`, `DEFAULT_STDERR_LIMIT`, `DEFAULT_STDOUT_LIMIT`,
  `PASSTHROUGH_NAMES`, `SIGNALLED`, `STDERR_LIMIT`, `STDOUT_LIMIT`, `TIMEOUT`,
  `capture_bytes`, `capture_file`, `contained_path`, `credential_values`,
  `digest_bytes`, `digest_file`, `manifest`, `minimal_environment`,
  `parse_jsonl`, `redact_bytes`, `relative_to_root`, `run_bounded`.
  Together these are bounded subprocess execution, capture envelopes, tree
  manifests, credential discovery and byte redaction, minimal-environment
  construction, path containment, and JSONL decoding.
- `harness_workbench.canon` — the canonical-JSON and file/tree digest rule.
  Declares no `__all__`, but the shipped subject tree imports `digest_obj` from
  it directly and every adapter record digests this module by name beside
  `capture`, because `capture.digest_file` wraps `canon.digest_file`: a change
  to the digest rule moves every digest in a record while `capture.py` stays
  byte-identical. Public by use.
- `harness_workbench.conform` — library verdict surface, no top-level command.
  Declares no `__all__` and is imported by no shipped tree; it is public
  because this record says so, which is why the completeness rule below exists.

`capture` is the module the adapter tree imports instead of carrying a second
implementation of bounded capture;
[`docs/adapter-primitive-extraction.md`](adapter-primitive-extraction.md)
records what was promoted, what was deliberately left adapter-local, and the
one intended behaviour change (a bound that fires is reported in
`termination_reason` and no longer synthesized into an exit code). The adapter
tree itself is opt-in data and is not part of this manifest.

`tests/test_release_engineering.py` enforces this manifest three ways, reading
only this section rather than the whole record: every core module the shipped
subject tree imports must be routed here; every module declaring `__all__` must
be routed here with its exported names listed exactly; and every module under
`src/harness_workbench/` must be either routed here or named in an explicit
internal list, so adding a module to the package forces somebody to decide
which it is. The first two rules only catch modules that advertise themselves —
`conform` does neither, and that is the shape which reached a published
candidate unrouted.

## EF-RS-REL component status

This is a proportional T1 packet for deterministic software claims. Statuses
describe the intended release, not merely whether a file has been drafted.

| Requirement | Status | Evidence / next action |
|---|---|---|
| `EF-RS-REL-01` canonical front door | **not yet due** | README plus this record prepare the function; public URL/current release identity do not exist. |
| `EF-RS-REL-02` release abstract | **not yet due** | README opening and claim card prepare the question, bounded result, evidence state, importance, and non-claims. Recheck after release-status edits. |
| `EF-RS-REL-03` technical report | **not yet due** | This record, README, guides, tests, and release tools prepare deterministic questions, conditions, expected/observed results, limits, failures, and next gates. |
| `EF-RS-REL-04` claim card | **not yet due** | Claim card is prepared above; it is not yet a public released claim card. |
| `EF-RS-REL-05` evidence/reproduction package | **unresolved — blocking** | Source trace and local clean-install path exist; final content-addressed commit, released checksums/assets, and downloaded-asset run are absent. |
| `EF-RS-REL-06` inspection aid | **not yet due** | The minimal first run and model-free example prepare immediately executable aids. Reassess the SHOULD at freeze if final claims become harder to inspect. |
| `EF-RS-REL-07` version/archive/citation | **unresolved — blocking** | Package version is prepared; exact release commit, signed tag, asset checksums, public URL, and release record are absent. DOI trigger is not asserted. |
| `EF-RS-REL-08` disclosure/withholding | **unresolved — blocking** | Trust/data boundaries and store exclusions are documented; history/archive secret scan, public-setting review, and final licence/security review remain pending. |
| `EF-RS-REL-09` roles/challenge/assurance | **not yet due** | Author-side preparation agent and conditions are disclosed; no outside, independent, methods-challenged, or artifact-checked label is used. Release approver remains pending. |
| `EF-RS-REL-10` criticism/correction/security routes | **unresolved — blocking** | Files name planned issue/private routes; private vulnerability reporting, public issue intake, triage retention, and route tests are not verified. |
| `EF-RS-REL-11` derivative consistency | **not yet due** | Exact current-surface manifest and claim IDs are machine checked. Future release notes, tags, package-index page, announcements, figures, talks, or dashboards require review before publication. |
| `EF-RS-REL-12` post-release maintenance | **not yet due** | No release exists. README/CHANGELOG/support/security surfaces prepare lifecycle and correction locations. |

## Recorded departures and unresolved predicates

An unresolved MUST remains unmet; recording it does not waive it.

| ID | Requirement / reason | Recipient consequence | Closure evidence |
|---|---|---|---|
| `D-01` | `EF-SRS-03`, `EF-RS-REL-05`: no frozen/tagged/downloaded release asset exists | A recipient cannot rely on the local first run as evidence for the bytes they will download | Exact release commit, asset SHA-256, downloaded wheel and sdist clean-install logs |
| `D-02` | `EF-SRS-08`, `EF-RS-REL-07`: release identity and public derivative do not exist | A recipient cannot resolve a canonical immutable release or tell whether release notes match source | Signed immutable tag, GitHub prerelease URL, exact target commit, release notes and assets reviewed against claim IDs |
| `D-03` | Private preparation CI passed at `db8b426366b7a5a0775449369b24971b04f1bb1f`, and the nine-job public matrix passed at superseded freeze `bbcb6fbcd0a873eb3589028119e8b9489179fe34`; hosted CI has not yet been observed for the containing remediated freeze commit | A recipient has no release-final evidence for the exact source intended for tagging | Required CI run URL and nine successful job conclusions for the exact remediated freeze commit |
| `D-04` | No outside artifact check or independent run has occurred | A recipient receives author-side functionality evidence only and cannot infer independent assurance | Eligible outside checker record with relationship, prior access, environment, help, raw result, and digest |
| `D-05` | Private vulnerability reporting and public issue/correction intake are not verified | A public recipient may lack a confidential reporting path or a tested correction path | Enable settings, exercise both routes safely, retain dated evidence and named owner |
| `D-06` | **Scans run and clean for this candidate; the manual disposition is still pending.** Pinned Gitleaks 8.30.1 reported no leaks over the history-wide scan (74 commits, all refs, `--log-opts='--all'`) and no leaks over the built `dist` archives at `--max-archive-depth 2`, both against `.gitleaks.toml`. Not vacuous: with synthetic credential-shaped strings planted in a scratch copy the same invocation reports findings and exits non-zero. What remains unmet is the manual licence/privacy/security disposition, which is a human judgement and not a scanner result | A recipient can rely on the scanner result for these bytes, but not yet on a reviewed disposition | Manual licence/privacy/security disposition by a named reviewer, plus a re-run of both scans against the exact release commit once it exists |
| `D-07` | Public visibility, read-only Actions tokens, secret scanning, push protection, Dependabot security updates, and successful CodeQL upload were verified on 2026-08-12. The superseded freeze produced alert #1 on a credential-shaped synthetic fixture; the containing source remediates it, but exact-commit CodeQL and ruleset evidence remain pending. | A recipient cannot infer that the remediation is accepted or that required checks and immutable release refs are enforced | Zero open CodeQL alerts on the exact remediated freeze plus dated branch/tag ruleset evidence |
| `D-08` | The exact governing-standard source bytes are pinned but not shipped or asserted publicly reachable | A recipient without the standards repository can detect a supplied file's equality but cannot independently inspect the complete governing text from this artefact alone | Publish or ship the exact pinned standards, or record a stable public content-addressed location |
| `D-09` | No final release approver/date/disposition exists | A recipient cannot treat preparation as an authorization to release | Named approver, date, decision, and residual limitations after all blockers close |

## Measurement evidence that is not in the release surface

This is disclosure, not a departure: nothing below is an unmet requirement, and
none of it ships. It is recorded because a reader who found it independently
would reasonably ask why the record was silent.

The cross-harness containment matrix retained during development was measured
against an adapter-tree digest that is no longer `HEAD` — a subject-pairing fix
landed after the matrix was cut. That is the apparatus baseline working as
designed: a record binds the apparatus that measured it, and nothing the matrix
reports depended on the later fix. The matrix and its run stores live in an
untracked working directory, match no `MANIFEST.in` include pattern, and are
absent from both archives; this was verified against a built sdist rather than
assumed. **No adapter-tree measurement claim is routed anywhere in this
record**, so no claim here rests on that evidence.

One consequence of the version bump is worth naming. Every adapter record
carries an `apparatus` block naming the package version and digesting both
`capture` and `canon`, and the shipped tree writes a baseline copy at
materialize time. Agreement against that baseline is computed **from the module
digests only — the version label is recorded and deliberately not compared.**
Because `capture.py` and `canon.py` are byte-identical across `0.1.0rc1` →
`0.1.0rc2`, baselines cut under rc1 still agree under rc2, and retained records
still name `0.1.0rc1`. That is the intended reading of "digest what determines
the work": the digest is the commitment and the version is a label. It is
recorded here so nobody later mistakes an agreeing baseline for evidence that
the package version was unchanged.

## Mechanical checks

| Check | Requirement/claim guarded | Where it runs | Preparation state |
|---|---|---|---|
| Full `unittest` suite | behavioral contracts plus documentation drift | local gate and `.github/workflows/ci.yml` matrix | Passed locally; private preparation matrix passed at `db8b426366b7a5a0775449369b24971b04f1bb1f`; nine-job public matrix passed at superseded freeze `bbcb6fbcd0a873eb3589028119e8b9489179fe34`; exact remediated-freeze matrix pending |
| Conformance surface inventory test | `EF-SRS-08`, `EF-SRS-09`, `EF-RS-REL-11` | `tests/test_release_engineering.py` in every full suite | Prepared and passing locally |
| Public library-module routing test | `C-HWB-13`, `EF-SRS-08`, `EF-RS-REL-11` | `tests/test_release_engineering.py` in every full suite | Reads the manifest section alone, not the whole record, so an incidental mention elsewhere cannot satisfy it. Enforces three rules: shipped-tree imports must be routed, `__all__` modules must be routed, and every module must be routed or explicitly internal. Verified by inversion in all three directions — removing the `capture` entry, adding a new `__all__`-declaring module, and adding an unclassified module each fail the suite. This check did not exist for `0.1.0rc1`, which is why a new public module reached a published candidate unrouted |
| Exported-name agreement test | `C-HWB-13` | `tests/test_release_engineering.py` in every full suite | Compares `capture.__all__` against the names written in the manifest in both directions, so adding or removing an export fails until the manifest is updated. Verified by inversion: appending a name to `__all__` fails the suite |
| Capture primitive unit suite and determinism soak | `C-HWB-13` | `tests/test_capture.py`, `tests/test_capture_soak.py` | Eight failure modes (success, nonzero exit, malformed output, saturation, timeout, ignored termination, orphan child, corrupt evidence) hold one projection digest across repeated runs; passing locally |
| Standards-pin assertions | exact EF-SRS/EF-RS-REL version, commit, blob, and SHA-256 | `tests/test_release_engineering.py` | Prepared and passing locally |
| Relative Markdown link/anchor check | navigable front door and docs | `tests/test_workbench.py` | Passing locally |
| Spec/record/seam/feature/command table drift checks | `C-HWB-03` through `C-HWB-06` | `tests/test_workbench.py` | Passing locally |
| Registered transcript replay and unverified-transcript ceiling | evidence behind example/CLI output claims | `tests/test_workbench.py` | Passing locally |
| Build plus strict Twine | package metadata/README rendering | local gate and package CI job | Passed locally; private preparation package job passed at `db8b426366b7a5a0775449369b24971b04f1bb1f`; exact freeze-commit job pending |
| Sdist normalization plus archive content/licence/generated-store/privacy verifier | `C-HWB-01`, `C-HWB-12`, EF-SRS-02 | `tools/normalize_sdist.py`, `tools/verify_release_artifacts.py` | Neutral ownership, commit-derived tar/gzip timestamps, safe paths/links, and byte-identical repeated normalization passed locally; final assets pending |
| Separate clean wheel and sdist install/first run | EF-SRS-03, `C-HWB-02`, `C-HWB-12` | `tools/verify_installed_artifact.py` | Passed locally as self-run; downloaded assets pending |
| Checksums and tag/version verifier | content/version identity | release tools and CI | Local archive checks pass; final tag/assets pending |
| Reproducible-build agreement between independent checkouts | `C-HWB-12`; the step 3 comparison in `RELEASING.md` | maintainer release gate | Two independent pristine clones of the same commit produced byte-identical wheel, sdist and `SHA256SUMS`. Verified by inversion: building a different commit yields different digests, so the comparison detects a checkout that is not the gated commit rather than passing on anything. This is what makes step 3's `diff` between the offline gate and the GitHub clone meaningful; without reproducibility that comparison would fail spuriously and be disabled |
| Gitleaks history and archive scan | disclosure review, `D-06` | maintainer release gate | Pinned Gitleaks 8.30.1, obtained from its official release with the published archive checksum verified and installed outside the repository. History-wide scan over all refs: no leaks in 74 commits. Archive scan over the built wheel, sdist and `SHA256SUMS`: no leaks. Verified by inversion — planted synthetic credential-shaped strings in a scratch copy are reported and exit non-zero, and the sole allowlisted fixture pattern is still reported when it appears outside its one allowlisted path. Must be re-run against the exact release commit once that exists |
| Hosted matrix, CodeQL, repository settings, routes, tag and release | external release predicates | GitHub | Superseded public freeze passed CI and uploaded CodeQL, exposing one release-blocking fixture alert; exact remediated-freeze CI, zero-open-alert result, rulesets, tag, and release remain **blocking** |

## Release-final completion block

This block is deliberately empty until the release exists. Do not copy local
preparation values into it.

- Release commit: **PENDING**
- Signed tag and verification: **PENDING**
- GitHub prerelease URL and target commit: **PENDING**
- Hosted CI/CodeQL evidence: **PENDING**
- Repository rules/security-setting evidence: **PENDING**
- Released wheel/sdist/`SHA256SUMS` filenames and digests: **PENDING**
- Downloaded-asset clean-install results: **PENDING**
- Correction-route and confidential-security-route test: **PENDING**
- Outside check, if obtained (never required to relabel self-run): **NONE**
- Release approver, date, decision, residual limitations: **PENDING**
- Overall conformance: **PREPARED; NOT RELEASED; PUBLICATION BLOCKED**
