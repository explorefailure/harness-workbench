#!/bin/sh
set -eu
exec python3.11 failure_rewrite_runner.py --variant "${1:?variant required}"
