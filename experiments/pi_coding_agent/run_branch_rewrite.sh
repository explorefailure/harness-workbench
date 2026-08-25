#!/bin/sh
set -eu
exec python3.11 branch_rewrite_runner.py --variant "${1:?variant required}"
