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

NOT A STABLE API. Every subject is pinned to an exact third-party release, one
of them a developer preview that documents breaking changes as expected. The
tree ships so the workbench can demonstrate itself against real harnesses; it
carries no compatibility promise, and core imports nothing from it.
"""
from __future__ import annotations

import os
import shutil
from typing import List, Tuple


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
    return written, skipped
