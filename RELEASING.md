# Releasing Harness Workbench

This is the maintainer procedure for a GitHub release. A build on a laptop is
not a release. A version is public only after its tag and GitHub release exist.
The current candidate policy is:

| Stage | Python package version | Git tag | GitHub release |
|---|---|---|---|
| first candidate | `0.1.0rc1` | `v0.1.0-rc.1` | prerelease — **published 2026-08-12** |
| current candidate | `0.1.0rc2` | `v0.1.0-rc.2` | prerelease — not yet created |
| final promotion | `0.1.0` | `v0.1.0` | full release |

A candidate that has already been published is history: never retarget its tag
or reuse its number. `0.1.0rc2` exists because the source gained public API
after `0.1.0rc1` shipped — a new importable module, `harness_workbench.capture`
— so the published prerelease no longer describes this tree.

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

## 1. Prepare a clean candidate commit

First turn the preparation branch into a release commit: change the README
status from "preparing" to an accurate candidate-release statement, change the
changelog heading from `## 0.1.0rc2 — unreleased` to `## 0.1.0rc2 — YYYY-MM-DD`
with the date of this release commit, remove its preparation note,
and review
[`docs/release-conformance-0.1.0rc2.md`](docs/release-conformance-0.1.0rc2.md).
That record must continue to say that no GitHub prerelease exists yet and must
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
python -m pip install --disable-pip-version-check '.[release]'
GITLEAKS_VERSION=8.30.1
test "$(gitleaks version)" = "$GITLEAKS_VERSION"
gitleaks git --no-banner --redact=100 --log-opts='--all' "$SOURCE_REPO"
```

Clone rather than build from the development tree. Gitignored files — a stale
`build/lib`, a run store, a measurement directory — are invisible to
`git status`, and a stale `build/lib` will silently ship files that were deleted
from source. A clone carries committed bytes and nothing else. This is not
hypothetical in this repository.

The history scan runs against `$SOURCE_REPO` rather than whichever checkout is
to hand. A full local clone happens to carry every branch, but that is a
property of how it was made and not a guarantee: `--single-branch`, a shallow
clone, or the GitHub clone in step 3 would each scan less, and the GitHub one
cannot see a local-only branch such as a pre-rewrite backup at all. Scan the
repository that holds every ref, so the scan's coverage never depends on the
checkout's provenance.

Stop if the checkout is dirty, dependency installation fails, the release-tool
versions differ from `pyproject.toml`, or the history-wide secret scan reports a
finding. Obtain Gitleaks from its official release, verify the published archive
checksum, and install it outside the repository. `.gitleaks.toml` extends the
default rules. Its sole exception is joined with `AND` across the exact
initial-history commit, one test path, and the synthetic fixture pattern that
has been removed from current source. Do not broaden that exception or add
another to make a candidate pass. Do not clean an uncertain directory to make
this check pass; start another fresh clone.

## 2. Run the source and artifact gate

For the current candidate, these variables and their agreement check are exact:

```sh
VERSION=0.1.0rc2
TAG=v0.1.0-rc.2
test "$(python -c 'import harness_workbench as p; print(p.__version__)')" = "$VERSION"
python tools/verify_release_tag.py "$TAG"
python -m unittest discover -s tests -v
test ! -e build
test ! -e dist
RAW_SDIST_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RAW_SDIST_DIR"' EXIT HUP INT TERM
python -m build --wheel --outdir dist
python -m build --sdist --outdir "$RAW_SDIST_DIR"
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
backend-selected files, file modes, and safe file/directory/link types while
sorting members, setting every member to `0:0` / `root:root`, setting every tar
and gzip timestamp to the release commit's `SOURCE_DATE_EPOCH`, removing
machine-specific PAX fields, and writing a filename-free gzip header. The
artifact verifier rejects a raw or malformed sdist, including non-neutral
ownership, timestamp drift, absolute/traversal paths, unsafe links, special
nodes, duplicate members, and platform-bearing gzip headers.

The two installed-artifact commands are intentionally separate. Each creates
a clean virtual environment, installs only that artifact, checks installed
metadata and both command forms, and executes the documented first run. The
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
git push origin "$RELEASE_COMMIT":refs/heads/release/0.1.0rc2
git fetch origin
test "$(git rev-parse 'origin/release/0.1.0rc2^{commit}')" = "$RELEASE_COMMIT"
```

Now re-run the gate against what GitHub actually serves. Step 2 proved the
commit is sound; this proves the remote holds that commit and nothing else, and
that a recipient cloning it gets identical bytes:

```sh
cd ..
test ! -e hwb-release-remote
git clone https://github.com/explorefailure/harness-workbench.git hwb-release-remote
cd hwb-release-remote
git fetch --tags origin
test "$(git rev-parse 'origin/release/0.1.0rc2^{commit}')" = "$RELEASE_COMMIT"
git checkout --detach "$RELEASE_COMMIT"
test -z "$(git status --porcelain --untracked-files=all)"
```

Repeat step 2 here in full, then compare the two runs:

```sh
diff ../hwb-release/dist/SHA256SUMS dist/SHA256SUMS
```

**The manifests must be identical.** `SOURCE_DATE_EPOCH` is derived from the
release commit and the sdist is normalized to neutral ownership and that
timestamp, so the same commit must produce the same bytes from either checkout.
A difference means the remote is not serving the commit that passed the gate, or
the build is not reproducible — either way, stop. This comparison is only
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

## 4. Create and verify the candidate tag

```sh
test -z "$(git tag --list "$TAG")"
git tag -s "$TAG" "$RELEASE_COMMIT" -m "Harness Workbench $TAG"
git verify-tag "$TAG"
test "$(git rev-list -n 1 "$TAG")" = "$RELEASE_COMMIT"
python tools/verify_release_tag.py "$TAG"
git push origin "$TAG"
```

The tag-triggered CI job repeats tag-to-package agreement with read-only GitHub
permissions. Wait for every tag run to pass before creating the prerelease. If
the local tag has not been pushed, delete it locally and fix the source. If it
has been pushed, do not move it: leave the failed candidate documented, increment
the candidate number to the next unused `0.1.0rcN` with its matching
`v0.1.0-rc.N` tag, and restart at step 1. Never reuse or retarget a candidate
number that has been pushed, whether or not its release was created.

## 5. Create the GitHub prerelease

From `hwb-release-remote` — the checkout whose bytes came from GitHub and whose
`SHA256SUMS` matched the offline gate — with the files still in `dist/`:

```sh
python tools/release_checksums.py check dist
gh release create "$TAG" \
  dist/*.whl dist/*.tar.gz dist/SHA256SUMS \
  --verify-tag --prerelease --generate-notes \
  --title "Harness Workbench $TAG"
gh release view "$TAG" \
  --json assets,isDraft,isPrerelease,tagName,targetCommitish,url
```

Check that the release is not a draft, is marked as a prerelease, names the
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
`docs/release-conformance-0.1.0rc2.md` with the exact release commit, tag and
verification, run URLs, setting/route evidence, asset filenames and SHA-256
values, downloaded-asset result, assessor/approver, and residual limitations.
Publish that finalized copy beside the assets or in the GitHub release body.
The source-tree record is necessarily prepared before its own containing
commit exists; the release copy is the content-addressed per-artefact record.
Do not rewrite the tagged source commit or claim an outside assurance label for
an author-side run.

## 6. Promote to final `v0.1.0`

The final is a new commit and a new build, not a rename of the candidate files.
Apply accepted candidate fixes, change `__version__` from `0.1.0rcN` to
`0.1.0`, update the README status, and move the changelog entries from the
candidate heading to a dated `0.1.0` release heading. Then start
again from step 1 with:

```sh
VERSION=0.1.0
TAG=v0.1.0
python tools/verify_release_tag.py "$TAG"
```

Run the complete source, matrix, build, strict Twine, archive, separate-install,
checksum, signed-tag, and downloaded-asset gates again. Create the final release
without `--prerelease`:

```sh
gh release create "$TAG" \
  dist/*.whl dist/*.tar.gz dist/SHA256SUMS \
  --verify-tag --generate-notes \
  --title "Harness Workbench $TAG"
```

Only after the final GitHub release and downloaded assets pass verification may
the README say that v0.1.0 is released. Publishing to a package index is a
separate, explicitly authorized operation and is not part of this procedure.
