#!/usr/bin/env python3
"""Record one Hermes shell-hook payload without changing its decision."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    evidence = os.environ.get("HWB_HERMES_HOOK_EVIDENCE")
    if not evidence:
        print("HWB_HERMES_HOOK_EVIDENCE is required", file=sys.stderr)
        return 2
    payload = json.load(sys.stdin)
    with Path(evidence).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        stream.write("\n")
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
