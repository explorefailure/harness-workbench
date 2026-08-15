#!/bin/sh
set -eu
exec python3.11 runner.py --subject "${1:?subject required}"
