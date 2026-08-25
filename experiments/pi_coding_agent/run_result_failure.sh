#!/bin/sh
set -eu
exec python3.11 result_failure_runner.py --variant "${1:?variant required}"
