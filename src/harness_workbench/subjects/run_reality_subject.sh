#!/bin/sh
set -eu

# Reality Checks pass the console entrypoint's interpreter explicitly.  This
# keeps installed execution independent of PATH while leaving the reusable
# subject specifications and their certified launcher unchanged.
exec "${HWB_PYTHON:?HWB_PYTHON must name the invoking hwb Python runtime}" runner.py \
  --subject "${1:?subject required}" \
  --workload "${2:-write}"
