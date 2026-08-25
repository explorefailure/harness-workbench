#!/bin/sh
set -eu

for candidate in python3 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
  then
    exec "$candidate" control_runner.py "$@"
  fi
done

echo "Harness Workbench Pi experiment requires Python 3.11+" >&2
exit 2
