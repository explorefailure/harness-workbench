# Releasing Harness Workbench

This is the maintainer procedure for a GitHub release. A build on a laptop is
not a release. A version is public only after its tag and GitHub release exist.
The current candidate policy is:

| Stage | Python package version | Git tag | GitHub release |
|---|---|---|---|
| first candidate | `0.1.0rc1` | `v0.1.0-rc.1` | prerelease — **published 2026-08-12** |
| second candidate | `0.1.0rc2` | `v0.1.0-rc.2` | prerelease — **published 2026-08-25** |
| current final promotion | `0.1.0` | `v0.1.0` | full release — not yet created |

A candidate that has already been published is history: never retarget its tag
or reuse its number. `0.1.0rc2` exists because the source gained public API
after `0.1.0rc1` shipped — a new importable module, `harness_workbench.capture`.
The final promotion carries the verified rc2 contents into a new commit and
new build; candidate evidence does not transfer to final bytes.

Tags are signed, annotated, immutable, and must point at the exact commit whose
source produced the uploaded files. Never move or reuse a failed tag; fix the
source, increment the candidate number, and run the gate again.

## Public-visibility security gate

Repository files cannot enable GitHub security features or enforce repository
rules. As part of the public flip, the maintainer must verify these settings on
the target repository rather than treating the checked-in configuration as
proof that GitHub is enforcing it:

- enable private vulnerability reporting and verify that
  `https://github.com/explorefailure/harness-workbench/security/advisories/new`
  presents a private form;
- enable secret scanning, push protection, Dependabot alerts, and Dependabot
  security updates;
- keep the default Actions token read-only and do not allow workflows from
  pull requests to approve pull requests;
- restrict Actions to GitHub-owned actions or require full-length commit SHA
  pins; the checked-in workflows use both;
- let the pinned CodeQL workflow complete and confirm Python results appear in
  code scanning; and
- add branch and release-tag rulesets after their required check names exist,
  then require the CI and CodeQL checks on the protected branch.

Some security features may become available only after the repository is
public. If so, enable and verify them immediately after the visibility change,
before announcing the repository or creating the prerelease. Record the
settings evidence in the release conformance record. These are external
release-gate actions, not changes this procedure or a local commit can perform.

## 1. Prepare a clean release commit

First turn the preparation branch into a release commit: change the README
status from "preparing" to an accurate final-candidate statement, move the
candidate changelog entries under `## 0.1.0 — YYYY-MM-DD` with the date of this
release commit, and review
[`docs/release-conformance-0.1.0.md`](docs/release-conformance-0.1.0.md).
That record must continue to say that no final GitHub release exists yet and must
not promote preparation self-runs to release evidence. Commit the release-only
edits.

**Do not push yet.** Nothing in step 2 needs a remote — the whole source and
artifact gate is offline — so it runs here, before anything leaves the machine.
Publishing a candidate branch and then discovering it cannot build is a
published mistake rather than a local one, and a pushed candidate number is
spent whether or not it was any good. What the gate genuinely needs is a
*pristine* checkout, which is not the same thing as a *remote* one: take it
from the local repository.

```sh
umask 022
SOURCE_REPO="$PWD"
RELEASE_COMMIT="$(git rev-parse HEAD)"
test "${#RELEASE_COMMIT}" -eq 40
test -z "$(git status --porcelain --untracked-files=all)"
cd ..
test ! -e hwb-release
git clone --no-hardlinks "$SOURCE_REPO" hwb-release
cd hwb-release
git checkout --detach "$RELEASE_COMMIT"
test "$(git rev-parse HEAD)" = "$RELEASE_COMMIT"
test -z "$(git status --porcelain --untracked-files=all)"
SOURCE_DATE_EPOCH="$(git show -s --format=%ct "$RELEASE_COMMIT")"
test "$SOURCE_DATE_EPOCH" -gt 0
export SOURCE_DATE_EPOCH
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check \
  'build==1.5.0' 'setuptools==83.0.0' 'twine==7.0.0'
GITLEAKS_VERSION=8.30.1
test "$(gitleaks version)" = "$GITLEAKS_VERSION"
gitleaks git --no-banner --redact=100 --log-opts='--all' "$SOURCE_REPO"
```

Clone rather than build from the development tree. Gitignored files — a stale
`build/lib`, a run store, a measurement directory — are invisible to
`git status`, and a stale `build/lib` will silently ship files that were deleted
from source. A clone carries committed bytes and nothing else. This is not
hypothetical in this repository.

Install the pinned release tools, not the project. `pip install '.[release]'`
would run an in-tree PEP 517 build and leave a `build/` directory behind, and
step 2 begins by requiring that no `build/` exists — so that one line made step
2 unreachable from its own step 1, and a fresh clone landed in the same state.
Nothing in the gate needs the project installed: the test suite puts `src` on
`sys.path` itself, `tools/verify_release_tag.py` parses the source
`__version__`, and `tools/verify_installed_artifact.py` builds its own throwaway
environments. The version check in step 2 reads the source for the same reason —
the gate must check the version *being released*, not whichever copy happens to
be installed.

The three pins are exact, and `setuptools` is pinned because it is the build
backend. `pyproject.toml`'s `requires = ["setuptools>=77"]` is a floor that
`python -m build` resolves freshly in an isolated environment, so the release
bytes would depend on whatever the index served that day: the same commit built
under setuptools 79.0.1 and 84.0.0 produced different wheels, differing in
member modes as well as compressed bytes. The gate therefore builds with
`--no-isolation` against the pinned backend, which is why it has to be installed
here. The floor still governs third parties building from source; only the
release path is pinned. `pyproject.toml`'s `release` extra carries the same
three versions and a test fails if the two lists disagree.

Bumping that pin is a deliberate act with a visible consequence, not
housekeeping. setuptools 84 stops copying the executable bit onto `.py` package
data, so `subjects/hook.py` and `subjects/runner.py` ship non-executable and
the artifact verifier fails naming both — the change reaches a reader as a
failed gate rather than as a quietly different wheel. Only the wheel is
affected: the normalized sdist is byte-identical across 83 and 84, because
normalization reduces every member to the neutral modes and both versions copy
the checkout's executable bit into the sdist unchanged.

The history scan runs against `$SOURCE_REPO` rather than whichever checkout is
to hand. A full local clone happens to carry every branch, but that is a
property of how it was made and not a guarantee: `--single-branch`, a shallow
clone, or the GitHub clone in step 3 would each scan less, and the GitHub one
cannot see a local-only branch such as a pre-rewrite backup at all. Scan the
repository that holds every ref, so the scan's coverage never depends on the
checkout's provenance.

Stop if the checkout is dirty, tool installation fails, or the history-wide
secret scan reports a finding. Obtain Gitleaks from its official release, verify the published archive
checksum, and install it outside the repository. `.gitleaks.toml` extends the
default rules. Its sole exception is joined with `AND` across the exact
initial-history commit, one test path, and the synthetic fixture pattern that
has been removed from current source. Do not broaden that exception or add
another to make a candidate pass. Do not clean an uncertain directory to make
this check pass; start another fresh clone.

## 2. Run the source and artifact gate

For the current final promotion, these variables and their agreement check are exact:

```sh
VERSION=0.1.0
TAG=v0.1.0
test "$(PYTHONPATH=src python -c 'import harness_workbench as p; print(p.__version__)')" = "$VERSION"
python tools/verify_release_tag.py "$TAG"
python -m unittest discover -s tests -v
```

The offline subject adapter suite is a separate mandatory source gate:

```sh
PYTHONPATH=src python -m unittest discover -s src/harness_workbench/subjects -p 'test*.py' -v
```

Continue with the artifact gate only after both source gates pass:

```sh
test ! -e build
test ! -e dist
RAW_SDIST_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RAW_SDIST_DIR"' EXIT HUP INT TERM
python -m build --no-isolation --wheel --outdir dist
python -m build --no-isolation --sdist --outdir "$RAW_SDIST_DIR"
python tools/normalize_sdist.py "$RAW_SDIST_DIR"/*.tar.gz --output-dir dist
rm -rf -- "$RAW_SDIST_DIR"
trap - EXIT HUP INT TERM
python -m twine check --strict dist/*.whl dist/*.tar.gz
python tools/verify_release_artifacts.py dist
gitleaks dir --no-banner --redact=100 --config .gitleaks.toml \
  --max-archive-depth 2 dist
python tools/verify_installed_artifact.py dist/*.whl
python tools/verify_installed_artifact.py dist/*.tar.gz
python tools/release_checksums.py write dist
python tools/release_checksums.py check dist
test -z "$(git status --porcelain --untracked-files=all)"
```

The backend-built sdist exists only under the private `mktemp` directory and is
deleted after normalization. Only the normalized archive enters `dist/`, the
checksum manifest, or an upload command. `normalize_sdist.py` preserves the
backend-selected files and safe file/directory/link types while sorting
members, setting every member to `0:0` / `root:root`, reducing every mode to
`0644`, `0755` for a directory or an executable, and `0777` for a link, setting
every tar and gzip timestamp to the release commit's `SOURCE_DATE_EPOCH`,
removing machine-specific PAX fields, and writing a filename-free gzip header.
The artifact verifier rejects a raw or malformed sdist, including non-neutral
ownership, non-neutral modes, timestamp drift, absolute/traversal paths, unsafe
links, special nodes, duplicate members, and platform-bearing gzip headers.

Modes are normalized because they are not the source's — they are the umask of
whoever ran the build. Git tracks one permission bit, so that bit is kept and
the rest is fixed. Nothing normalizes the *wheel* the same way: setuptools
stores each file's mode as it found it. **Run the whole gate under `umask 022`.**
A wheel built under a different umask is byte-different for no reason a reader
could ever recover from the artefact.

The verifier checks archive modes two ways, and neither expectation comes from
the archive:

- **permission bits against a constant.** Every member must be `0644`, a
  directory or executable `0755`, a link `0777`, or the `0664` the wheel writer
  stamps on `RECORD` — that last one a known constant of the pinned backend
  rather than an observation. This is what catches umask leakage, and it fails
  with the umask named.
- **the executable set against the source tree.** The set of regular members
  carrying the executable bit must *equal* the set the checkout marks
  executable: sdist `harness_workbench-<version>/<path>` against `<path>`, wheel
  `harness_workbench/<path>` against `src/harness_workbench/<path>`, and
  generated members (`PKG-INFO`, `*.dist-info/*`) executable in neither. The
  expected value is the source file's own mode bit, which is the one bit Git
  records, so a shipped script that quietly lost its executable bit and a
  shipped document that quietly gained one both fail, and both directions are
  named in the message.

The second check exists because the first cannot stand alone: an "expected"
mode derived from the member's own executable bit says only that `0755` members
are executable, which every archive satisfies. Rewriting every sdist member to
`0755` passed that check.

The two installed-artifact commands are intentionally separate. Each creates
a clean virtual environment, installs only that artifact, checks installed
metadata and both command forms, materializes the shipped subject tree, runs
its offline adapter suite against the installed capture primitive, and executes
the documented first run. The
artifact verifier requires exactly one wheel and one normalized sdist,
verifies their contents and metadata, and rejects generated run evidence.

Stop on any non-zero exit. Never copy the raw backend sdist out of its temporary
directory or upload it. Do not upload a subset of the files and do not edit an
archive or `SHA256SUMS` by hand. If `build/` or `dist/` already exists, stop and
use another clean clone instead of mixing artifacts from different commits.

## 3. Publish the reviewed commit, then require GitHub evidence

This is the first step that leaves the machine, and it is deliberately after the
gate. Only a commit that already passed step 2 gets pushed.

```sh
cd "$SOURCE_REPO"
git push origin "$RELEASE_COMMIT":refs/heads/release/0.1.0
git fetch origin
test "$(git rev-parse 'origin/release/0.1.0^{commit}')" = "$RELEASE_COMMIT"
```

Now re-run the gate against what GitHub actually serves. Step 2 proved the
commit is sound; this proves the remote holds that commit and nothing else, and
that a recipient cloning it gets identical bytes:

```sh
umask 022
cd ..
test ! -e hwb-release-remote
git clone https://github.com/explorefailure/harness-workbench.git hwb-release-remote
cd hwb-release-remote
git fetch --tags origin
test "$(git rev-parse 'origin/release/0.1.0^{commit}')" = "$RELEASE_COMMIT"
git checkout --detach "$RELEASE_COMMIT"
test -z "$(git status --porcelain --untracked-files=all)"
SOURCE_DATE_EPOCH="$(git show -s --format=%ct "$RELEASE_COMMIT")"
test "$SOURCE_DATE_EPOCH" -gt 0
export SOURCE_DATE_EPOCH
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check \
  'build==1.5.0' 'setuptools==83.0.0' 'twine==7.0.0'
```

This clone needs its own environment. The venv, the pinned release toolchain,
and `SOURCE_DATE_EPOCH` all belong to `hwb-release`, and step 2's block assumes
they already exist. `--no-isolation` builds with whatever backend the active
environment happens to hold, so a borrowed or unactivated venv either fails
outright or builds this clone against a backend nobody pinned — which is the
one input this comparison is least able to show you.

Repeat step 2 here in full, then compare the two runs:

```sh
diff ../hwb-release/dist/SHA256SUMS dist/SHA256SUMS
```

**The manifests must be identical.** `SOURCE_DATE_EPOCH` is derived from the
release commit, and the sdist is normalized to neutral ownership, neutral modes
and that timestamp, so the same commit produces the same sdist from either
checkout. The wheel is not normalized that way and carries the modes of the
checkout it was built from, which is why both clones are made under `umask 022`
and why step 2 rejects a wheel whose modes say otherwise.

Start from the difference itself: `SHA256SUMS` names which file moved, and the
two `dist/` directories are still on disk, so compare the archives' member
lists, modes and per-member digests before theorising. Known causes, not an
exhaustive list:

- **the toolchain differs between the two clones** — a venv that was not
  activated, `--no-isolation` dropped, or an environment holding a setuptools
  other than the pin. This one is genuinely benign and it presents as a *mode*
  difference, which is exactly what a "benign umask" story would also predict,
  so check the installed versions in both clones before believing either;
- **the remote is not serving the commit that passed the gate**;
- **the build is not reproducible** for a reason nobody has found yet.

Umask drift is deliberately absent from this list: step 2 rejects it in both
clones, with the umask named, before this comparison is ever reached. If it
somehow reaches here, the mode check is what failed, and that is a finding
rather than a rebuild.

Only a toolchain difference is fixable by rebuilding. For the other two, stop. Do not
disable or loosen the comparison to get past it: a gate that fails benignly
gets switched off, and this one is the only thing that checks the bytes GitHub
serves against the bytes that passed the gate. This comparison is only
available because the gate ran before the push; it is the reason for that
ordering as much as avoiding a published failure is.

Then wait for the GitHub Actions matrix on that commit. The required evidence is
Linux and macOS on Python 3.11–3.14 plus the package job:

```sh
gh run list --commit "$RELEASE_COMMIT" --workflow CI \
  --json headSha,status,conclusion,workflowName,url
```

Stop if any required job is absent, pending, skipped, or failing. A maintainer
must inspect the run in GitHub before continuing. Do not tag first and hope a
later run passes.

## 4. Create and verify the final tag

```sh
test -z "$(git tag --list "$TAG")"
git tag -s "$TAG" "$RELEASE_COMMIT" -m "Harness Workbench $TAG"
git verify-tag "$TAG"
test "$(git rev-list -n 1 "$TAG")" = "$RELEASE_COMMIT"
python tools/verify_release_tag.py "$TAG"
git push origin "$TAG"
```

The tag-triggered CI job repeats tag-to-package agreement with read-only GitHub
permissions. Wait for every tag run to pass before creating the release. If
the local tag has not been pushed, delete it locally and fix the source. If it
has been pushed, do not move it: leave the failed candidate documented, increment
the candidate number to the next unused `0.1.0rcN` with its matching
`v0.1.0-rc.N` tag, and restart at step 1. Never reuse or retarget a candidate
number that has been pushed, whether or not its release was created.

## 5. Create the GitHub release

From `hwb-release-remote` — the checkout whose bytes came from GitHub and whose
`SHA256SUMS` matched the offline gate — with the files still in `dist/`:

```sh
python tools/release_checksums.py check dist
gh release create "$TAG" \
  dist/*.whl dist/*.tar.gz dist/SHA256SUMS \
  --verify-tag --generate-notes \
  --title "Harness Workbench $TAG"
gh release view "$TAG" \
  --json assets,isDraft,isPrerelease,tagName,targetCommitish,url
```

Check that the release is not a draft, is not marked as a prerelease, names the
right tag, and contains one wheel, one sdist, and `SHA256SUMS`. Download those
GitHub-hosted assets into an empty directory and repeat the gate against the
downloaded bytes:

```sh
DOWNLOAD_DIR="../hwb-release-download-$TAG"
test ! -e "$DOWNLOAD_DIR"
mkdir "$DOWNLOAD_DIR"
gh release download "$TAG" --dir "$DOWNLOAD_DIR"
python tools/release_checksums.py check "$DOWNLOAD_DIR"
python tools/verify_release_artifacts.py "$DOWNLOAD_DIR"
python tools/verify_installed_artifact.py "$DOWNLOAD_DIR"/*.whl
python tools/verify_installed_artifact.py "$DOWNLOAD_DIR"/*.tar.gz
```

Stop and mark the release with a clear warning if uploaded bytes or first-run
behavior differ. Never replace assets silently.

After the downloaded bytes pass, finalize the release copy of
`docs/release-conformance-0.1.0.md` with the exact release commit, tag and
verification, run URLs, setting/route evidence, asset filenames and SHA-256
values, downloaded-asset result, assessor/approver, and residual limitations.
Publish that finalized copy beside the assets or in the GitHub release body.
The source-tree record is necessarily prepared before its own containing
commit exists; the release copy is the content-addressed per-artefact record.
Do not rewrite the tagged source commit or claim an outside assurance label for
an author-side run.

## 6. Close the final promotion

Only after the final GitHub release and downloaded assets pass verification may
the README say that v0.1.0 is released. Publishing to a package index is a
separate, explicitly authorized operation and is not part of this procedure.
