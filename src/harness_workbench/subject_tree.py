"""The external harnesses shipped inside the installed package.

A *feature* is a control you own and can invert. A *subject* is a harness you
run from the outside -- Claude Code, Codex CLI, DeepSeek Harness, Hermes Agent,
Pi -- and measure. The two are shipped on the same terms and for the same
reason, but they are not the same kind of thing, and the vocabulary should not
pretend otherwise.

MATERIALIZED, NEVER IMPORTED. The builtin feature tree is loaded by the runner,
so it can stay inside the package and be named by a spec. Subjects are executed
as ordinary child processes named by `steps[].argv`, so a spec has to reach them
through the filesystem. `hwb subjects init <dir>` copies the tree into a real
directory the user owns; from there every path is relative to the spec, exactly
like a hand-written workload.

That copy is what preserves the record. Each spec declares its adapter sources
in `inputs`, so `freeze` and `receipt` digest the exact bytes that ran. Had
these been imported from the installed package instead, which adapter ran would
have become a property of whichever version happened to be installed -- an
undeclared variable deciding the work, which is the failure the digest rule
exists to prevent.

The adapters are what that rule protects, and they are still copied. It does
NOT follow that the tree imports nothing: the adapters now import
`harness_workbench.capture` rather than carrying a second implementation of it.
The adapters decide what the subject is asked to do and arrive by digest; the
primitive measures it and arrives by import. The line is between deciding the
work and measuring it, not between core and tree.

DO NOT DEFEND THAT LINE BY POINTING AT `features_root`, which is where this
docstring first went wrong. A feature is not merely named: `features.py`
digests every feature's source tree into `features[].digest`, records its
version, and the runner copies the feature's actual bytes into the run
directory beside the record. A feature is identified three ways. The capture
primitive is identified by an `apparatus` block the adapter writes into its own
stdout -- not digested by `freeze`, not bound by `receipt`, not preserved
anywhere. The cases are not equivalent, and the primitive is the weaker one.

So the cost is real and unmitigated by analogy: `freeze` digests the files a
spec declares in `inputs`, which are files beside the spec, and after
materialization a module imported from site-packages cannot be one of them. An
upgraded primitive changes how a run was measured without moving a declared
digest. What exists today is disclosure -- the `apparatus` block names the
version and the digests of `capture` and `canon` as they actually ran, and the
cross-subject comparison refuses runs that were not captured by the same one.
That catches divergence BETWEEN subjects in one comparison. It does not catch a
uniform upgrade across all of them, which is the likelier shape. See
`docs/adapter-primitive-extraction.md`.

NOT A STABLE API. Every subject is pinned to an exact third-party release, one
of them a developer preview that documents breaking changes as expected. The
tree ships so the workbench can demonstrate itself against real harnesses; it
carries no compatibility promise, and core imports nothing from it.
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Tuple

APPARATUS = "apparatus.json"


def subjects_root() -> str:
    """The subject tree shipped inside the installed package."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "subjects")


def subject_files() -> List[str]:
    """Every shipped file, relative to the tree. Empty if absent, never an error."""
    root = subjects_root()
    if not os.path.isdir(root):
        return []
    found: List[str] = []
    for directory, _, names in os.walk(root):
        if "__pycache__" in directory:
            continue
        for name in names:
            path = os.path.join(directory, name)
            found.append(os.path.relpath(path, root))
    return sorted(found)


def apparatus_manifest() -> Dict[str, Any]:
    """Which build of the primitive this copy of the tree was cut against.

    The adapters import `capture` (and, through it, `canon`) from the installed
    package rather than carrying it, so those bytes decide how a subject is
    measured while sitting outside everything a spec can declare. Written at
    materialize time, this is the baseline the adapter compares itself to at
    run time -- which is what turns "the primitive changed" from invisible into
    a recorded fault.

    Deliberately NOT added to any spec's `inputs`. Declaring it would put it
    under `freeze`, and `freeze` would be weaker here than it looks: a baseline
    lock is created on first run, so materializing into a FRESH directory --
    the normal way to get a new copy -- writes a new manifest and a new lock
    together and reports no drift. The comparison that catches an upgrade has
    to happen against the running primitive, not against a lock made at the
    same moment.
    """
    from . import canon, capture

    return {
        "schema": "hwb-subject-apparatus/v0.1",
        "package": "harness_workbench",
        "version": __import__("harness_workbench").__version__,
        "modules": {
            name: {
                "file": os.path.basename(module.__file__),
                "sha256": canon.digest_file(module.__file__).split(":", 1)[1],
            }
            for name, module in sorted(
                {"canon": canon, "capture": capture}.items()
            )
        },
    }


def materialize(destination: str, force: bool = False) -> Tuple[List[str], List[str]]:
    """Copy the shipped tree into a directory the caller owns.

    Returns (written, skipped). Refuses to overwrite by default: the tree is a
    starting point people edit, and silently restoring shipped bytes over an
    edited adapter would discard exactly the work that makes it theirs.
    """
    root = subjects_root()
    if not os.path.isdir(root):
        raise FileNotFoundError("no subject tree is installed with this package")

    written: List[str] = []
    skipped: List[str] = []
    for relative in subject_files():
        target = os.path.join(destination, relative)
        if os.path.exists(target) and not force:
            skipped.append(relative)
            continue
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        shutil.copyfile(os.path.join(root, relative), target)
        shutil.copymode(os.path.join(root, relative), target)
        written.append(relative)

    # Written last and unconditionally, unlike the copied files: it describes
    # THIS install, so preserving an older copy's manifest would be preserving
    # a false statement about which primitive is now importable. It is not an
    # edited file in the sense the skip rule protects -- nobody hand-writes it.
    manifest_path = os.path.join(destination, APPARATUS)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(apparatus_manifest(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    if APPARATUS not in written:
        written.append(APPARATUS)
    return written, skipped
