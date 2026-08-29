#!/usr/bin/env python3
"""Emit one already sealed offline episode into an ordinary Workbench attempt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-nonce", required=True)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    raw = args.record.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if document.get("store_nonce") != args.store_nonce:
        raise SystemExit("sealed spec store nonce disagrees with episode")
    sys.stdout.buffer.write(raw)
    if not raw.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
