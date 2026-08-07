"""Canonical serialisation and digests.

One rule, used everywhere: sorted keys, no insignificant whitespace, UTF-8.
"The digest binds the experiment" is meaningless without a byte rule.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Tuple


def canon_bytes(obj: Any) -> bytes:
    """Canonical JSON encoding. Deterministic across runs and machines."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest_obj(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canon_bytes(obj)).hexdigest()


def digest_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def digest_tree(root: str, skip: Iterable[str] = ()) -> str:
    """Digest of a directory: sorted (relpath, filedigest) pairs, rolled up.

    Hashing one file misses the manifest; hashing a tree means the digest
    actually says 'this exact code produced this run'.
    """
    skip_set = set(skip)
    entries: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in {"__pycache__", ".git"})
        for fn in sorted(filenames):
            if fn in skip_set or fn.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            entries.append((rel, digest_file(full)))
    return digest_obj(entries)


def short(dig: str, n: int = 6) -> str:
    return dig.split(":", 1)[-1][:n]


def file_digests(paths: Iterable[str], base: str) -> Dict[str, str]:
    """Digest a set of declared input paths, resolved against `base`."""
    out: Dict[str, str] = {}
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(base, p)
        out[p] = digest_file(full) if os.path.isfile(full) else "missing"
    return out
