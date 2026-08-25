#!/bin/sh
set -eu
# Third argument is the guard variant; empty for the write and repair
# workloads, which take none.
if [ -n "${3:-}" ]; then
  exec python3.11 runner.py \
    --subject "${1:?subject required}" \
    --workload "${2:-write}" \
    --variant "$3"
fi
exec python3.11 runner.py \
  --subject "${1:?subject required}" \
  --workload "${2:-write}"
