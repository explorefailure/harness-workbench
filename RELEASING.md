# Releasing Harness Workbench

This is the maintainer procedure for a GitHub release. A build on a laptop is
not a release. A version is public only after its tag and GitHub release exist.
The current candidate policy is:

| Stage | Python package version | Git tag | GitHub release |
|---|---|---|---|
| first candidate | `0.1.0rc1` | `v0.1.0-rc.1` | prerelease |
| final promotion | `0.1.0` | `v0.1.0` | full release |

Tags are signed, annotated, immutable, and must point at the exact commit whose
source produced the uploaded files. Never move or reuse a failed tag; fix the
source, increment the candidate number, and run the gate again.

## 1. Prepare a clean candidate commit

First turn the preparation branch into a release commit: change the README
status from "preparing" to an accurate candidate-release statement, change the
changelog heading to `## 0.1.0rc1 — YYYY-MM-DD`, remove its preparation note,
and commit those release-only edits. Do not claim that a GitHub prerelease
exists yet. Push that reviewed commit to `release/0.1.0rc1` so a fresh clone can
resolve it, then do the gate from the fresh clone rather than a development
worktree.

```sh
git clone https://github.com/explorefailure/harness-workbench.git hwb-release
cd hwb-release
git fetch --tags origin
RELEASE_COMMIT="$(git rev-parse 'origin/release/0.1.0rc1^{commit}')"
test "${#RELEASE_COMMIT}" -eq 40
git checkout --detach "$RELEASE_COMMIT"
test "$(git rev-parse HEAD)" = "$RELEASE_COMMIT"
test -z "$(git status --porcelain --untracked-files=all)"
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check '.[release]'
```

Stop if the commit is not the reviewed commit, the checkout is dirty, dependency
installation fails, or the release-tool versions differ from `pyproject.toml`.
Do not clean an uncertain directory to make this check pass; start another
fresh clone.

## 2. Run the source and artifact gate

For the first candidate, these variables and their agreement check are exact:

```sh
VERSION=0.1.0rc1
TAG=v0.1.0-rc.1
test "$(python -c 'import harness_workbench as p; print(p.__version__)')" = "$VERSION"
python tools/verify_release_tag.py "$TAG"
python -m unittest discover -s tests -v
test ! -e build
test ! -e dist
python -m build --sdist --wheel
python -m twine check --strict dist/*.whl dist/*.tar.gz
python tools/verify_release_artifacts.py dist
python tools/verify_installed_artifact.py dist/*.whl
python tools/verify_installed_artifact.py dist/*.tar.gz
python tools/release_checksums.py write dist
python tools/release_checksums.py check dist
test -z "$(git status --porcelain --untracked-files=all)"
```

The two installed-artifact commands are intentionally separate. Each creates a
clean virtual environment, installs only that artifact, checks installed
metadata and both command forms, and executes the documented first run. The
artifact verifier requires exactly one wheel and one sdist, verifies their
contents and metadata, and rejects generated run evidence.

Stop on any non-zero exit. Do not upload a subset of the files and do not edit
an archive or `SHA256SUMS` by hand. If `build/` or `dist/` already exists, stop
and use another clean clone instead of mixing artifacts from different commits.

## 3. Require GitHub evidence before tagging

Wait for the GitHub Actions matrix to pass on the already-pushed, reviewed
candidate commit. The required evidence is Linux and
macOS on Python 3.11–3.14 plus the package job. Confirm the remote commit rather
than trusting a branch label:

```sh
git fetch origin
test "$(git rev-parse origin/release/0.1.0rc1)" = "$RELEASE_COMMIT"
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
has been pushed, do not move it: leave the failed candidate documented, bump to
`0.1.0rc2` / `v0.1.0-rc.2`, and restart at step 1.

## 5. Create the GitHub prerelease

From the same verified checkout, with the files still in `dist/`:

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
