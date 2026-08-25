# Harness Workbench 0.1.0rc2 release conformance record

> **Prepared candidate record — NOT RELEASED.** This is the repository-owned
> pre-release record for an intended R2 public release. It records historical
> and current preparation CI separately from release-final evidence and is not
> evidence that a tag, GitHub release, public security setting, or downloadable
> release asset exists. Rows marked **BLOCKING PUBLICATION** must be closed with
> release-final evidence before this record can describe a released artefact.

> **This record covers candidate source `0.1.0rc2`, not a release.** That
> source is visible in [public pull request
> #9](https://github.com/explorefailure/harness-workbench/pull/9), but it has
> no `v0.1.0-rc.2` tag, GitHub release, or release assets. The preceding
> candidate `0.1.0rc1` *was* published as public GitHub prerelease
> `v0.1.0-rc.1` on 2026-08-12; its release-final record is attached to that
> prerelease as the content-addressed record for those bytes. This file is not
> that record and does not describe it.

## Artefact identity and declared reach

| Field | Value |
|---|---|
| Artefact | Harness Workbench |
| Python distribution | `harness-workbench` |
| Import package / command | `harness_workbench` / `hwb` |
| Intended version | `0.1.0rc2` |
| Intended readable tag | `v0.1.0-rc.2` |
| Preceding published candidate | `0.1.0rc1` / `v0.1.0-rc.1`, published 2026-08-12. Its public API did not include `harness_workbench.capture`; see the library-surface row below. |
| Lifecycle state | `prepared candidate source; public PR; unreleased` |
| Declared reach | **R2 — public**, declared 2026-08-11 for the intended release |
| Public attribution | Approved 2026-08-12: Garrett Davis is intentionally public as copyright holder, package author, maintainer, and Git identity associated with Explore Failure. The existing GitHub account association in the reviewed history is intentional. |
| Historical rc1 preparation | Commit `db8b426366b7a5a0775449369b24971b04f1bb1f` passed eight Linux/macOS CPython jobs plus the package job in [private CI run 31625746283](https://github.com/explorefailure/harness-workbench/actions/runs/31625746283). [Private CodeQL run 31625748519](https://github.com/explorefailure/harness-workbench/actions/runs/31625748519) failed during upload/status access. These runs predate `0.1.0rc2` and are not evidence for it. |
| Historical superseded rc1 freeze | Signed commit `bbcb6fbcd0a873eb3589028119e8b9489179fe34` passed all nine jobs in [public CI run 31627092506](https://github.com/explorefailure/harness-workbench/actions/runs/31627092506). [Public CodeQL run 31628103134](https://github.com/explorefailure/harness-workbench/actions/runs/31628103134) uploaded successfully but found a credential-shaped synthetic fixture. The tagged rc1 remediation superseded that source. |
| Earlier rc2 hosted preparation checkpoint | Exact public PR commit `f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3` passed all eight Linux/macOS CPython 3.11–3.14 cells and the package job in [CI run 32604245910](https://github.com/explorefailure/harness-workbench/actions/runs/32604245910). It also passed [CodeQL run 32604245892](https://github.com/explorefailure/harness-workbench/actions/runs/32604245892); the repository had zero open code-scanning alerts when checked on 2026-08-22. These historical preparation results apply only to that commit. |
| Post-audit promotion checkpoint | Exact release-branch commit `d1a339fe989b456580bc6b2d8216f2ab66b235e9` passed all eight Linux/macOS CPython 3.11–3.14 cells and the package job in [CI run 32815107448](https://github.com/explorefailure/harness-workbench/actions/runs/32815107448), and passed [CodeQL run 32815107568](https://github.com/explorefailure/harness-workbench/actions/runs/32815107568). The repository had zero open code-scanning alerts when checked on 2026-08-25. This closes the audit-remediation checkpoint; these remain preparation results for exactly that pre-freeze commit, and the release-only commit must rerun every gate. |
| Audit-remediation lineage | Remediation begins at commit `05054d60c816309ea6346f2d34be1c654e7f5697` and closes at the post-audit promotion checkpoint `d1a339fe989b456580bc6b2d8216f2ab66b235e9`. The source commit containing this record follows that checkpoint; these release-only changes require exact-head hosted checks after publication to the release branch. |
| Record-preparation revision | The source commit containing this file; resolve with `git rev-parse HEAD` in the reviewed checkout. It is not a release identity. |
| Release commit | **PENDING — BLOCKING PUBLICATION.** No `0.1.0rc2` release commit has been designated. Exact remote identity and release-final evidence must be recorded before publication. |
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
| `EF-SRS-08` — claim routing | **BLOCKING PUBLICATION** | Every current repository surface is routed below and mechanically inventoried. The final release notes/assets do not exist; hosted checks for the eventual release commit, disclosure review, correction/security-route test, and exact release identity remain unresolved at T1. |

**Gate decision:** do not publish. `EF-SRS-03` and `EF-SRS-08` remain blocking
for the actual release even though their repository preparation is in place.

### EF-SRS non-gate assessment

| Requirement | Pre-release status | Evidence / departure |
|---|---|---|
| `EF-SRS-04` — examples do not narrow the tool | **MET IN PREPARED SOURCE** | The first recommended example is model-free; examples cover shell workloads, local-model adjacency, custom features, failures, retries, redaction, and bounded filesystem effects. README explicitly says Ollama is an example rather than a requirement. |
| `EF-SRS-05` — documentation separated by reader need | **CONFORMS FOR THE R2 SHOULD** | README routes learning/first-run, doing, reference, feature-authoring, measurement, experiment interpretation, and release/conformance needs to separate pages. |
| `EF-SRS-06` — contribution/support posture | **MET IN PREPARED SOURCE** | The verbatim posture above links to the actual solo-maintainer process and best-effort support route without promising governance or an SLA. |
| `EF-SRS-07` — hand-off sufficiency | **MET IN PREPARED SOURCE** | `CONTRIBUTING.md` gives tests; README gives development/build checks; `RELEASING.md` gives clean source, artifact, CI, tag, release, download, and promotion procedures. Its steps 1 and 2 are **completely executable offline** and were run verbatim, end to end, from a pristine clone for this candidate, with no step skipped, reordered or adapted: history scan, full suite, build at the pinned backend, normalization, strict Twine, artifact verification, archive scan, separate clean installs, and checksums. They were not executable before `d88c2ca`, and the record said they were: step 1 ended by installing the project, which runs an in-tree build and leaves a `build/` directory, while step 2's first precondition is that no `build/` exists — so step 2 could never start, and the fresh clone its prose recommended landed in the same state. Step 1 now installs the pinned tools only. Publication moved to step 3, so no candidate is pushed before its own gate has passed it. External release steps remain explicitly pending. |
| `EF-SRS-09` — mechanical checks | **CONFORMS FOR THE R2 SHOULD IN SOURCE; RELEASE-FINAL EXTERNAL CHECKS PENDING** | The table below identifies the machine checks and where each runs. `D-03`, `D-05`, `D-06`, and `D-07` distinguish exact-commit hosted preparation from release-final evidence and checked-in configuration from observed repository state. |
| `EF-SRS-10` — recorded departure | **MET** | `D-01` through `D-09` name every known unmet predicate, the reason, recipient consequence, and closure evidence. |

## Verified first-run record

### Assurance label and verifier

- Label: **Self-run verification (preparation only)**.
- Verifier: maintainer-side author-context verification on 2026-08-22.
- Relationship and prior access: performed with full source-tree and
  implementation context; not an outsider and not independent.
- Owner help/intervention: the maintainer side prepared this record and its
  checks before running them. No outside operator was involved.
- Credentials/network: this package/first-run gate uses no application
  credentials; the unit suite and first run are network-free. Live subject
  runs are separate and have the prerequisites disclosed in the subject guide.
- Starting environment: macOS/arm64 with CPython 3.11 for the local package
  gate. Hosted compatibility evidence for checkpoint `f86e410` is recorded
  separately and does not turn the local run into outside assurance.
- Source identity: local artefact verification was run at merge commit
  `1a4713ccddee67b64c3d3bfd587edec26a5183bd`; hosted preparation was run at
  `f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3`. Neither is designated as the
  release commit.

### Exact artefact forms and commands

The two preparation forms tested locally are one wheel and one source
distribution built from a clean clone of
`1a4713ccddee67b64c3d3bfd587edec26a5183bd`:

The environment holds the pinned release toolchain and nothing else: `build`,
`setuptools` and `twine` at the exact versions the `release` extra declares.
The project itself is deliberately not installed — installing it runs an
in-tree build whose `build/` directory the gate refuses to start with, and no
gate step needs it importable from site-packages.

```sh
python3.11 -m unittest discover -s tests -v
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)"
export SOURCE_DATE_EPOCH
RAW_SDIST_DIR="$(mktemp -d)"
python3.11 -m build --no-isolation --wheel --outdir dist
python3.11 -m build --no-isolation --sdist --outdir "$RAW_SDIST_DIR"
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
wheel and ownership-neutral normalized sdist passed on 2026-08-22. Every member
of that normalized sdist had `0:0` / `root:root` ownership and the
release-source commit timestamp; a second normalization was byte-identical.
Every member of both archives carries one of the neutral permission constants,
and the members marked executable are exactly the files the checkout marks
executable — three in the wheel (`subjects/hook.py`, `subjects/runner.py` and
`subjects/run_subject.sh`) and ten in the sdist (those three plus the seven
executable files under `examples/`).
This supplies no outside assurance and does not verify a future tagged or
downloaded byte sequence.

Release-final evidence must add the designated release commit, asset filenames and SHA-256
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
  locally built wheel and sdist passed at `1a4713c`; the eight-cell hosted
  matrix, package job, and CodeQL passed at `f86e410`.
- **What that supports:** author-side evidence that the prepared source can
  produce installable archives whose documented minimal `/bin/echo` run works.
- **What it does not support:** publication, availability, hostile-code
  containment, Windows compatibility, workload quality, model quality,
  correctness outside documented invariants, all-Python/all-OS compatibility,
  an independent artifact check, independent reproduction, or long-term
  maintenance.
- **Important threats:** author-side verification shares implementation
  assumptions; the final commit and release bytes may differ; only one local
  OS supplies the local artefact preparation evidence; generated and prose
  documentation checks are incomplete by design; security settings live
  outside Git.
- **Outside assurance:** none.
- **Withholding:** no claim-sufficient run evidence is intentionally withheld,
  but the governing standards source is not shipped and complete
  release-final GitHub state is not yet recorded. See `D-08` and the pending
  release rows.
- **Current version / correction route:** this is an unreleased prepared-source
  record for a public PR. For a public release, GitHub Issues is the planned correction route
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
| `C-HWB-09` — CPython 3.11–3.14 on Linux/macOS is the intended v0.1 support target; Windows is unsupported | README, `pyproject.toml`, CI matrix | Exact public PR checkpoint `f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3` passed all eight Linux/macOS CPython 3.11–3.14 jobs and the package job in CI run 32604245910 | Exact-commit hosted preparation evidence; later audit remediation and the eventual release commit require new runs |
| `C-HWB-10` — maintenance, contribution, support, and security posture are the policies stated, without an SLA | README, `CONTRIBUTING.md`, `SUPPORT.md`, `SECURITY.md`, intake templates | Wording/routes agree mechanically | Policy declaration; EF-RS observed-result predicate not triggered |
| `C-HWB-11` — this candidate (`0.1.0rc2`) is public as source in PR #9 but has not been tagged or released; the preceding candidate `0.1.0rc1` was published as prerelease `v0.1.0-rc.1` | README, `CHANGELOG.md`, absence of a `v0.1.0-rc.2` tag, this record | Source surfaces consistently distinguish public review from publication as a release, and do not let the rc1 prerelease or the rc2 PR checks stand as release evidence for rc2 | The eventual `v0.1.0-rc.2` tag, target, release, and assets must be verified before publication |
| `C-HWB-12` — release archives include the expected source/docs/examples/tests/tools, exclude generated stores, carry agreeing metadata/licences, and install separately; the release sdist carries neutral ownership, neutral modes, and a commit-derived timestamp, and both archives mark executable exactly the files the checkout marks executable | `MANIFEST.in`, `pyproject.toml`, `tools/normalize_sdist.py`, `tools/verify_release_artifacts.py`, release tests | Strict Twine, archive inspection, normalized tar/gzip metadata, permission bits drawn from a constant set, executable members equal to the source tree's, wheel/sdist clean install, checksums pass locally | Self-run preparation; final downloaded assets absent |
| `C-HWB-13` — `harness_workbench.capture` and `harness_workbench.canon` are public, importable API; each declares `__all__`, and each `__all__` is exactly the names listed in that module's public-library manifest entry, in both directions; and a bound that fires is returned as a measurement rather than raised or encoded as a synthesized exit code | `src/harness_workbench/capture.py` and `canon.py` and their `__all__`, `docs/adapter-primitive-extraction.md`, the capture unit tests, the determinism soak over success/nonzero/malformed/saturation/timeout/ignored-termination/orphan/corrupt-evidence cases | Every exported name appears in that module's manifest entry, the entry names nothing outside its `__all__`, and the entry may name neither something the module does not define at all — dunder names included, only the literal `__all__` its own prose writes being exempt — nor a sibling module in place of one of its own exports; so adding, removing or renaming an export fails the suite, and so does describing an export that was never there; timeout, byte-limit and nonzero exit are reported in `termination_reason` with the real `returncode` preserved; the soak holds one projection digest across repeated runs | Self-run source evidence. **`capture` is a surface added after the published `0.1.0rc1`**; no recipient of that prerelease received this module, and no downloaded-asset evidence exists for it. `canon` shipped in `0.1.0rc1` with no declared surface; its `__all__` is new in `0.1.0rc2` and narrows what `import *` re-exports |

## Complete claim-bearing surface inventory

Every current public-facing or release-facing repository surface is assigned a
route. The exact path manifest below is machine checked; adding a root Markdown
file, docs page, shipped example, GitHub workflow/template, or public CLI
command fails the suite until this record routes it.

| Surface group | Exact surfaces | EF-RS-REL route |
|---|---|---|
| Front door | `README.md` | T1: `C-HWB-01` through `C-HWB-12`; purpose, ceiling, evidence state, limits, and routes |
| Reference and guides | `docs/adapter-envelope-promotion-review.md`; `docs/adapter-primitive-extraction.md`; `docs/campaign-manifests.md`; `docs/experiment-writeups.md`; `docs/measuring-your-own-code.md`; `docs/measuring.md`; `docs/the-record.md`; `docs/the-spec.md`; `docs/writing-a-feature.md` | T1: primarily `C-HWB-02` through `C-HWB-08`; the experiment template constrains interpretation and code-promotion claims rather than adding a performance claim. The extraction and envelope-review memos record internal code-promotion decisions and make no claim about any third-party harness's behaviour |
| This conformance/front-door supplement | `docs/release-conformance-0.1.0rc2.md` | T1 claim card, trace, assurance, departures, and current status |
| Examples and demonstration code/data | every exact `examples/` path in the manifest below | T1 demonstrations: `C-HWB-02`, `C-HWB-04`, `C-HWB-05`, `C-HWB-08`; no performance/generalization claim |
| CLI/help and runtime claims | `hwb --help`; `hwb --version`; `python -m harness_workbench --help`; every exact subcommand help surface below; runtime output from those commands | T1: `C-HWB-01` through `C-HWB-07`; source of truth is `src/harness_workbench/commands.py` plus parser/behavior tests |
| Public library API | every module in the exact public-library manifest below | T1: `C-HWB-13`; importable surface a recipient can depend on without going through `hwb`. `harness_workbench.capture` is **new in `0.1.0rc2`** and was not present in the published `0.1.0rc1`. |
| Package-index metadata | `pyproject.toml`; generated wheel `METADATA`; generated sdist `PKG-INFO`; README rendered as long description | T1: `C-HWB-01`, `C-HWB-09`, `C-HWB-10`, `C-HWB-12`; no package-index publication is authorized or claimed |
| Release history/process | `CHANGELOG.md`; `RELEASING.md`; future Git tag; future GitHub release notes; future wheel/sdist/`SHA256SUMS` | T1: `C-HWB-11`, `C-HWB-12`; future surfaces are **pending and must be reviewed before publication** |
| Licence, continuity, and reporting | `LICENSE`; `NOTICE`; `CONTRIBUTING.md`; `SUPPORT.md`; `SECURITY.md` | Licence routes under EF-SRS-02; policy declarations under `C-HWB-10`; any factual security/result claim routes at T1 under `C-HWB-07` |
| GitHub intake and automation | all exact `.github/` paths in the manifest below | Workflow/configuration facts route at T1 under `C-HWB-09`/`C-HWB-12`; intake wording routes under `C-HWB-10`; exact-commit preparation execution is recorded, while release-final execution and settings evidence remain pending |

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
  `PASSTHROUGH_NAMES`, `SIGNALLED`, `STDERR_LIMIT`, `STDOUT_LIMIT`,
  `STDOUT_STDERR_LIMIT`, `TIMEOUT`,
  `capture_bytes`, `capture_file`, `contained_path`, `credential_values`,
  `digest_bytes`, `digest_file`, `manifest`, `minimal_environment`,
  `parse_jsonl`, `redact_bytes`, `relative_to_root`, `run_bounded`.
  Together these are bounded subprocess execution, capture envelopes, tree
  manifests, credential discovery and byte redaction, minimal-environment
  construction, path containment, and JSONL decoding.
- `harness_workbench.canon` — the canonical-JSON and file/tree digest rule.
  Declares `__all__`, and its exact exported names are:
  `canon_bytes`, `digest_file`, `digest_obj`, `digest_tree`, `file_digests`,
  `short`.
  The shipped subject tree imports `digest_obj` from it directly and every
  adapter record digests this module by name beside `harness_workbench.capture`,
  because `capture.digest_file` wraps `canon.digest_file`: a change to the digest rule
  moves every digest in a record while `capture.py` stays byte-identical.
  Public by use and now by declaration — until `0.1.0rc2` it declared no
  `__all__`, so the exported-name check skipped the one module whose surface
  moves every digest in every record.
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
only the entry for the module in question rather than this whole section or the
whole record: every core module the shipped subject tree imports must be routed
here; every module declaring `__all__` must be routed here with its exported
names listed exactly, in both directions, and the manifest may name neither
something the module does not define nor a sibling module in place of one of
its own exports; and every importable module under `src/harness_workbench/`
outside the shipped subject tree — at any depth, including the `builtin/`
feature tree — must be either routed here or named in an explicit internal
list, so adding a module to the package forces somebody to decide which it is.

Imports are read as syntax rather than matched as text, and absolute and
package-relative spellings are resolved to one absolute module name before
anything is decided. `from harness_workbench import a, b` (both names, not the
first), `import harness_workbench.a`, `from harness_workbench.a import name`, a
function-local import, and a file in a subdirectory of the subject tree are
seen. So are the two dotted forms that name something below the top package:
`from harness_workbench.builtin.retry import feature` names the module
`builtin.retry.feature`, and `from harness_workbench.builtin import retry` binds
a package whose modules the importer can then reach — each implicates the
modules it can reach, because a spelling the rule cannot see reads as coverage
and is worse than no rule. So are their package-relative equivalents — `from ..
import runner`, `from ..builtin.retry import feature`, `from ..builtin import
retry` — which bind exactly the same modules from inside the shipped tree.
Those were dropped before analysis until `78a5c69`, relative imports being
filtered out ahead of it, so the two dotted forms this manifest had just closed
could be reopened by writing them with leading dots. A relative import that
stays inside the tree resolves under `harness_workbench.subjects`, which
discovery excludes, so the tree's own internal imports implicate nothing.
Aliasing is a spelling and not a form of its own: `import x` and `import x as y`
bind the same module and are the same syntax to this rule.

What is read is import *statements*. A core module reached only through a
dynamic call — `importlib.import_module("harness_workbench.runner")` — is not
seen by this rule, and is not claimed to be. Seeing it would mean importing or
executing the shipped tree to find out, and running import side effects across
the package to close a documentation rule is a worse risk than the gap. This is
the boundary of the rule, stated, rather than something the rule quietly covers.

`__all__` is read the same way: an assignment to that name at module scope
counts, including one indented inside a conditional and including
`globals()["__all__"] = [...]`. The line-anchored text match this replaces saw
neither, so a routed-or-internal module could move its declared surface with
the routing and exported-name rules both skipping it. A `def` or `class` body
does not count — an `__all__` bound there is a local name that never reaches
the module namespace, and firing on it would demand a decision about a surface
that does not exist.

Three further routes reach the module namespace without assigning the name
directly, and all three are read: `setattr(sys.modules[__name__], "__all__",
...)`, the same write spelled `sys.modules[__name__].__all__ = [...]`, and
importing the name from elsewhere — `from harness_workbench.capture import
__all__`, or anything bound `as __all__`. Each was confirmed to bind before it
was closed; the first governs `import *`. The rule discriminates on which
module is written: a `setattr` naming a *different* module, or any object that
is not `sys.modules[__name__]`, is not a declaration here and does not fire.

What remains unread is indirection through a variable —
`mod = sys.modules[__name__]` followed by `setattr(mod, ...)`, or an attribute
name that is computed rather than literal. Resolving those means tracking
bindings or executing the module, and running import side effects across the
package to settle a documentation rule is a worse risk than the gap. That is
the boundary, stated: this rule reads what a module's own syntax says about
`__all__`, including the indirect spellings above, but it does not follow a
name through a variable.

Discovery is every `.py` file under `src/harness_workbench/` outside the
shipped subject tree, at any depth, including dunder-named files and
directories; only `__pycache__` is skipped, being a build product rather than
source. That reaches `__init__.py` and `__main__.py`, which are decided rather
than excused: both are named in the internal list, because `__init__` holds one
public name — `__version__`, routed as package identity under `C-HWB-01` — and
`__main__` is the `python -m harness_workbench` entry point already routed in
the CLI/help manifest above. Neither is a library surface, and if either ever
declares `__all__` the routing rule fails until somebody moves it here.

The three rules read one package, and `[tool.setuptools.packages.find]` ships
every package under `src/` with no include or exclude filter — so a second
top-level package there would ship in the wheel and be classified by nothing
above. That is outside the letter of this section, which scopes itself to
`src/harness_workbench/`, and straight through its purpose. The set of
top-level packages holding Python under `src/` is therefore declared in the
suite and compared, and the declaration is refused if the discovery
configuration it reads grows a filter. The set is bound rather than discovery
widened because every routing key, internal-list entry and manifest bullet here
is written relative to `harness_workbench`: widening would file a second
package's modules under a manifest namespace that does not exist, where a
declared set fails the moment the package appears and makes widening a decision
somebody takes on purpose.

The first two rules only catch modules that advertise themselves — `conform`
does neither, and that is the shape which reached a published candidate
unrouted.

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
| `EF-RS-REL-08` disclosure/withholding | **unresolved — blocking** | Trust/data boundaries and store exclusions are documented. The history and archive secret scans have run clean for this source under pinned Gitleaks 8.30.1 (see `D-06` and the mechanical-check table); what remains is the re-run against the release commit, the public-setting review, and the final licence/security review, all of which remain pending. |
| `EF-RS-REL-09` roles/challenge/assurance | **not yet due** | Author-side preparation agent and conditions are disclosed; no outside, independent, methods-challenged, or artifact-checked label is used. Release approver remains pending. |
| `EF-RS-REL-10` criticism/correction/security routes | **unresolved — blocking** | Files name planned issue/private routes; private vulnerability reporting, public issue intake, triage retention, and route tests are not verified. |
| `EF-RS-REL-11` derivative consistency | **not yet due** | Exact current-surface manifest and claim IDs are machine checked. Future release notes, tags, package-index page, announcements, figures, talks, or dashboards require review before publication. |
| `EF-RS-REL-12` post-release maintenance | **not yet due** | No `0.1.0rc2` release exists. README/CHANGELOG/support/security surfaces prepare lifecycle and correction locations. |

## Recorded departures and unresolved predicates

An unresolved MUST remains unmet; recording it does not waive it.

| ID | Requirement / reason | Recipient consequence | Closure evidence |
|---|---|---|---|
| `D-01` | `EF-SRS-03`, `EF-RS-REL-05`: no frozen/tagged/downloaded release asset exists | A recipient cannot rely on the local first run as evidence for the bytes they will download | Exact release commit, asset SHA-256, downloaded wheel and sdist clean-install logs |
| `D-02` | `EF-SRS-08`, `EF-RS-REL-07`: release identity and public derivative do not exist | A recipient cannot resolve a canonical immutable release or tell whether release notes match source | Signed immutable tag, GitHub prerelease URL, exact target commit, release notes and assets reviewed against claim IDs |
| `D-03` | Exact post-audit checkpoint `d1a339fe989b456580bc6b2d8216f2ab66b235e9` passed the eight-cell matrix and package job in hosted CI run 32815107448, superseding preparation checkpoint `f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3`; no release commit exists yet. | A recipient has hosted preparation evidence for the complete post-audit source, but no release-final evidence for the source eventually tagged | Successful required jobs for the exact release commit and again for its immutable tag as required by the release procedure |
| `D-04` | No outside artifact check or independent run has occurred | A recipient receives author-side functionality evidence only and cannot infer independent assurance | Eligible outside checker record with relationship, prior access, environment, help, raw result, and digest |
| `D-05` | Private vulnerability reporting and public issue/correction intake are not verified | A public recipient may lack a confidential reporting path or a tested correction path | Enable settings, exercise both routes safely, retain dated evidence and named owner |
| `D-06` | **Unmet. The scans have run and are clean; the requirement is not a scanner result.** Pinned Gitleaks 8.30.1 reported no leaks over the history-wide scan (every commit reachable from every ref, `--log-opts='--all'`) and no leaks over the built `dist` archives at `--max-archive-depth 2`, both against `.gitleaks.toml`. Not vacuous: with synthetic credential-shaped strings planted in a scratch copy the same invocation reports findings and exits non-zero. What remains unmet is the manual licence/privacy/security disposition — a human judgement a scanner cannot stand in for — and a re-run against the release commit, which does not yet exist. A clean scan narrows this departure; it does not close it | A recipient can rely on the scanner result for the bytes scanned, but not on a reviewed disposition, and not on any scan of the release commit | Manual licence/privacy/security disposition by a named reviewer, plus a re-run of both scans against the exact release commit once it exists |
| `D-07` | Exact post-audit checkpoint `d1a339fe989b456580bc6b2d8216f2ab66b235e9` passed CodeQL run 32815107568, and the repository had zero open code-scanning alerts when checked on 2026-08-25. Active branch ruleset 20761261 requires the eight-cell matrix, package job, and CodeQL on `main` and `release/**`; active tag ruleset 20761275 makes `v*` tags immutable. Earlier checkpoint `f86e41031a4d6a98fbf3d0249d3a7c1416a5adc3` and CodeQL run 32604245892 remain historical preparation evidence. | A recipient can inspect exact post-audit preparation and enforcement evidence but cannot infer that the not-yet-created release commit passed | Successful CodeQL for the exact release commit and immutable tag |
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

One provenance trap around the version bump is worth naming. Every adapter
record carries an `apparatus` block naming the package version and digesting
both `capture` and `canon`, and the shipped tree writes a baseline copy at
materialize time. `capture` was added on the unreleased development branch at
`ff0bd33`, while the source version still read `0.1.0rc1`; the version moved to
`0.1.0rc2` later at `7d2d36f`. A retained development record naming version
`0.1.0rc1` therefore is **not** evidence that it came from the published rc1
tag. That tag does not contain `capture.py`, and current `canon.py` adds an
`__all__` declaration absent from the tag. No cross-release byte identity is
claimed. Apparatus agreement checks both module digests and the package
version, so an rc1-labelled baseline does not agree under rc2 even when its
module bytes happen to match.

## Mechanical checks

| Check | Requirement/claim guarded | Where it runs | Preparation state |
|---|---|---|---|
| Repository `unittest` suite | behavioral contracts plus documentation drift | local gate and `.github/workflows/ci.yml` matrix | Passed locally and in all eight hosted compatibility cells at exact post-audit checkpoint `d1a339f` in CI run 32815107448. The release-only commit requires a new run. |
| Offline subject-adapter suite from source | shared-envelope producer, comparator, oracle, apparatus, and control contracts | every Linux/macOS and Python compatibility cell plus the maintainer source gate | Passed locally and in all eight hosted cells at `d1a339f`; the release-engineering suite rejects conditional, forgiven, moved, excluded, commented, and unreachable forms of this gate. The release-only commit requires a new run. |
| Materialized subject-adapter suite from wheel and sdist | shipped subject tree and installed capture primitive agree | `tools/verify_installed_artifact.py`, separately for wheel and normalized sdist | Both locally built forms and the hosted package job passed at `d1a339f`; downloaded release assets do not exist and remain pending. |
| Conformance surface inventory test | `EF-SRS-08`, `EF-SRS-09`, `EF-RS-REL-11` | `tests/test_release_engineering.py` in every full suite | Prepared and passing locally |
| Public library-module routing test | `C-HWB-13`, `EF-SRS-08`, `EF-RS-REL-11` | `tests/test_release_engineering.py` in every full suite | Reads the routed module's own manifest entry, not the whole section and not the whole record, so neither an incidental mention elsewhere nor another module's entry can satisfy it. Enforces three rules: shipped-tree imports must be routed, `__all__` modules must be routed, and every importable module at any depth outside the shipped subject tree — dunder-named files and directories included, `__pycache__` excluded — must be routed or explicitly internal. Imports are read from the syntax tree of every file in the shipped tree, absolute and package-relative alike, each resolved to one absolute module name before anything is decided; `__all__` is read the same way, as a binding at module scope — assignment, `for` and `with` targets and the walrus alike, conditionals included, `def` and `class` bodies excluded — rather than as a line-anchored text match. `__all__` arriving indirectly is read too — by `setattr(sys.modules[__name__], ...)`, by `sys.modules[__name__].__all__ = ...`, or imported from another module — while a write naming a different module correctly does not fire. A module named only through a dynamic `importlib.import_module(...)` call, and an `__all__` reached through a variable rather than named in the syntax, remain disclosed scope, not coverage. Verified by inversion in twenty-eight directions, each re-run at `bc7abf3` and each failing the suite: removing a routed entry; adding a new module that declares `__all__`; adding one that declares nothing; adding one under `builtin/`; adding one under a dunder-named directory; importing an internal module from the shipped tree by nine spellings (`from harness_workbench import runner`, the same as the second name of a comma list, `import harness_workbench.runner`, `from harness_workbench.runner import run`, `from harness_workbench.builtin.retry import feature`, `from harness_workbench.builtin import retry`, and the package-relative `from .. import runner`, `from ..builtin.retry import feature` and `from ..builtin import retry`); importing from a subdirectory of the subject tree; a function-local import; declaring `__all__` indented inside an `if` block; assigning it through `globals()`; writing it by `setattr(sys.modules[__name__], "__all__", ...)`; assigning it as `sys.modules[__name__].__all__`; importing it by `from <module> import __all__`; binding it by `... as __all__`; binding it as a `for` target; binding it by `with ... as __all__`; binding it by the walrus `(__all__ := ...)`; `__init__.py` declaring `__all__`; the internal list naming a module that no longer exists; and a module listed as both routed and internal. Aliasing is a spelling rather than a direction — `import x` and `import x as y` present identical syntax to this rule and `asname` is read nowhere — so the four aliased forms were run as spelling checks and are not counted; the earlier count of seventeen counted one of them as a direction of its own, which is why this count is not that one plus the new work. Five of the twenty-eight passed silently until `78a5c69`: the three package-relative spellings, dropped before analysis because relative imports were filtered out ahead of it, and the two `__all__` spellings a pattern anchored at column zero could not see — indented inside an `if` block, and written through `globals()`. Four more passed silently until `5ca1d35`: the indirect spellings that reach the module namespace without naming `__all__` in a module-scope assignment — the `setattr` and `sys.modules[__name__]` writes, and the two import forms. Three more passed silently until `bc7abf3`: the statements that bind `__all__` without assigning to it — a `for` target, a `with ... as` target, and a walrus — each confirmed to govern `import *` before being closed. Inverted the other way as well, each run and each still passing the whole suite: intra-tree relative imports (`from . import oracles`, `from .repair_fixture import slugger`, `from .. import subjects`, and an aliased form of the first) resolve under `harness_workbench.subjects`, which discovery excludes, and implicate nothing. The shipped tree carries no relative import today, so that direction was written for the test rather than observed in place. This check did not exist for `0.1.0rc1`: `conform` shipped in rc1 unrouted, while `capture` was initially added unrouted only in post-rc1 development. |
| Exported-name agreement test | `C-HWB-13` | `tests/test_release_engineering.py` in every full suite | For every module declaring `__all__` — `capture` and `canon` — compares its exports against the names written in its own manifest entry in both directions, and additionally requires every name the entry writes to be something the module actually defines, so the manifest cannot promise an export that does not exist. Adding, removing or renaming an export fails until the manifest is updated. Verified by inversion in six directions, each re-run at `bc7abf3` and each failing the suite: appending a name to `__all__`, renaming an export, removing a name from `__all__` the entry still lists, writing an undefined name into the entry, writing a sibling module's name into the entry, and writing an undefined *dunder* name into the entry. The sibling direction was open until this candidate — every routed module's short name was subtracted from the entry before both checks, so an entry could claim a sibling module as part of its own surface; sibling modules are named in dotted form, which the identifier pattern ignores. The dunder direction passed silently until `78a5c69`: the whole dunder class was dropped from the entry, so `__nonexistent__` could be promised as an export and nothing looked. Only the literal `__all__` is exempt now — the name the entry prose writes when it says the module declares one — which is the false positive that filter was for, and it keeps passing |
| Distributed top-level package check | `C-HWB-13`, `EF-SRS-08` | `tests/test_release_engineering.py` in every full suite | The routing rules read `src/harness_workbench/`, while `[tool.setuptools.packages.find]` names `where = ["src"]` with no include or exclude filter — so a second top-level package under `src/` shipped in the wheel and was classified by nothing: outside the letter of the manifest, whose prose scopes itself to one package, and straight through its purpose, which is that adding a module to the distribution forces somebody to decide what it is. The set of top-level packages holding Python under `src/` is now declared in the suite and compared, and the declaration is refused if the configuration it reads changes what "found under `src/` ships" means: a filter added to `packages.find`, a key under `[tool.setuptools]` this check has not reasoned about, or a `package-dir` that no longer roots the distribution at `src/`. The second of those was itself a hole — `py-modules` sits beside `find` rather than inside it and ships a loose module `find` never returns and no directory under `src/` reveals. Binding the set was chosen over widening discovery because every routing key, internal-list entry and manifest bullet is written relative to `harness_workbench`: widening would file a second package's modules under a manifest namespace that does not exist, where a declared set fails the moment the package appears and makes the widening deliberate. Verified by inversion in four directions, each re-run at `bc7abf3` and each failing the suite: adding `src/harness_extra/__init__.py` declaring `__all__` and a public function, adding an `exclude` filter to `packages.find`, naming a loose module in `py-modules`, and remapping `package-dir` away from `src/`. The first two passed silently until `78a5c69`, the first of them having been reproduced as a package `setuptools` discovery does return; the other two passed silently until `bc7abf3`, `py-modules` having been reproduced as a module that reaches the built sdist while the suite stayed green |
| Capture primitive unit suite and determinism soak | `C-HWB-13` | `tests/test_capture.py`, `tests/test_capture_soak.py` | Eight failure modes (success, nonzero exit, malformed output, saturation, timeout, ignored termination, orphan child, corrupt evidence) hold one projection digest across repeated runs; passing locally |
| Standards-pin assertions | exact EF-SRS/EF-RS-REL version, commit, blob, and SHA-256 | `tests/test_release_engineering.py` | Prepared and passing locally |
| Relative Markdown link/anchor check | navigable front door and docs | `tests/test_workbench.py` | Passing locally |
| Spec/record/seam/feature/command table drift checks | `C-HWB-03` through `C-HWB-06` | `tests/test_workbench.py` | Passing locally |
| Registered transcript replay and unverified-transcript ceiling | evidence behind example/CLI output claims | `tests/test_workbench.py` | Passing locally |
| Build plus strict Twine | package metadata/README rendering | local gate and package CI job | Passed locally and in the hosted package job at exact post-audit checkpoint `d1a339f`; the release-only source and eventual release assets remain pending |
| Sdist normalization plus archive content/licence/generated-store/privacy verifier | `C-HWB-01`, `C-HWB-12`, EF-SRS-02 | `tools/normalize_sdist.py`, `tools/verify_release_artifacts.py` | Neutral ownership, commit-derived tar/gzip timestamps, safe paths/links, and byte-identical repeated normalization passed locally; final assets pending |
| Archive permission-bit and executable-set check | `C-HWB-12` | `tools/verify_release_artifacts.py` in the maintainer gate and the package CI job | Two checks, neither taking its expectation from the archive. Permission bits must be one of three constants (`0644`, `0755`, `0777`) plus the `0664` the pinned wheel writer stamps on `RECORD`; the set of executable members must **equal** the set the checkout marks executable, mapping sdist `harness_workbench-<version>/<path>` to `<path>` and wheel `harness_workbench/<path>` to `src/harness_workbench/<path>`, with generated members executable in neither. Verified by inversion against real built archives rather than fixtures only, eleven directions across both forms: rewriting every regular member to `0755`, stripping the executable bit from the shipped `run_subject.sh`, adding it to a file the checkout does not mark, `0600`, `0664` where the member is not `RECORD`, and `RECORD` at `0644`. The check this replaces derived each member's expected mode from that member's own executable bit, which every archive satisfies: on the same real artefacts it accepted an sdist with every regular member rewritten to `0755` and a wheel with `run_subject.sh` stripped |
| Pinned build backend | `C-HWB-12`; the step 3 comparison in `RELEASING.md` | `pyproject.toml` `release` extra, `RELEASING.md`, `.github/workflows/ci.yml`, `tests/test_release_engineering.py` | `[build-system].requires` is a floor and `python -m build` resolves a floor freshly per build, so the reproducibility claim held only under a version nothing pinned: the same commit on one machine at one umask produced different wheel bytes and different member modes under setuptools 79.0.1 and 84.0.0, 84 dropping the executable bit from `.py` package data the checkout marks executable. The release path installs `setuptools==83.0.0` with the other two release tools and builds `--no-isolation`, so the pin is the backend that actually runs; the floor still governs third parties building from source. A test reconstructs the install command from the `release` extra and requires it verbatim in both procedures, so the literal versions cannot drift from the declaration. Verified by inversion in six directions: bumping either list alone, deleting the backend pin, restoring `pip install '.[release]'`, and dropping `--no-isolation` from either surface each fail the suite. Verified against real builds as well: building the wheel at setuptools 84.0.0 under `umask 022` is rejected by the executable-set check naming `subjects/hook.py` and `subjects/runner.py`, so an unpinned backend surfaces as a failed gate rather than as different bytes two steps later. The drift is wheel-only — the normalized sdist is byte-identical across 83.0.0 and 84.0.0 |
| Separate clean wheel and sdist install/first run | EF-SRS-03, `C-HWB-02`, `C-HWB-12` | `tools/verify_installed_artifact.py` | Passed locally and in the hosted package job at exact post-audit checkpoint `d1a339f`; downloaded release assets pending |
| Checksums and tag/version verifier | content/version identity | release tools and CI | Local archive checks pass; final tag/assets pending |
| Reproducible-build agreement between independent checkouts | `C-HWB-12`; the step 3 comparison in `RELEASING.md` | maintainer release gate | Two independent pristine clones of the same commit, built at the pinned backend, produced byte-identical wheel, sdist and `SHA256SUMS`, and the normalized sdist was identical across `umask 022`, `077` and `002`. Verified by inversion: building a different commit yields different digests, so the comparison detects a checkout that is not the gated commit rather than passing on anything. This is what makes step 3's `diff` between the offline gate and the GitHub clone meaningful; without reproducibility that comparison would fail spuriously and be disabled |
| Gitleaks history and archive scan | disclosure review, `D-06` | maintainer release gate | Pinned Gitleaks 8.30.1, obtained from its official release with the published archive checksum verified and installed outside the repository. History-wide scan over every commit reachable from every ref: no leaks. Archive scan over the built wheel, sdist and `SHA256SUMS`: no leaks. No commit count is recorded here because it moves with every commit and would be stale before it was read; the scope is every ref, and the scan is re-run at the release commit. Verified by inversion — planted synthetic credential-shaped strings in a scratch copy are reported and exit non-zero, and the allowlisted fixture pattern is reported outside its allowlisted path **when it appears in a form a rule matches**, such as an assignment. It is not reported in the `echo` and JSON-value forms the initial-history commit carries in `examples/`, which trip no default rule at all; those pass because nothing matches them, not because the allowlist covers them. Must be re-run against the exact release commit once that exists |
| Hosted matrix, CodeQL, repository settings, routes, tag and release | external release predicates | GitHub | Exact post-audit checkpoint `d1a339f` passed CI run 32815107448 and CodeQL run 32815107568; zero open code-scanning alerts were observed on 2026-08-25. Active branch ruleset 20761261 requires all eight matrix cells, the package job, and CodeQL on `main` and `release/**`; active tag ruleset 20761275 prevents update or deletion of `v*` tags. Exact release-commit reruns, route checks, tag, assets, and release remain **blocking**. |

## Release-final completion block

This block is deliberately empty until the release exists. Do not copy local
preparation values into it.

- Release commit: **PENDING**
- Signed tag and verification: **PENDING**
- GitHub prerelease URL and target commit: **PENDING**
- Hosted CI/CodeQL evidence for the designated release commit: **PENDING**
- Repository rules/security-setting evidence: **PENDING**
- Released wheel/sdist/`SHA256SUMS` filenames and digests: **PENDING**
- Downloaded-asset clean-install results: **PENDING**
- Correction-route and confidential-security-route test: **PENDING**
- Outside check, if obtained (never required to relabel self-run): **NONE**
- Release approver, date, decision, residual limitations: **PENDING**
- Overall conformance: **PREPARED; NOT RELEASED; PUBLICATION BLOCKED**
