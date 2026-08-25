#!/bin/sh
set -eu
exec python3.11 composition_runner.py --variant "${1:?variant required}"
