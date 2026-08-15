#!/bin/sh
set -eu

variant=${1:?plan or act variant is required}
for candidate in python3 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
  then
    exec "$candidate" plan_runner.py --variant "$variant"
  fi
done
exit 2
