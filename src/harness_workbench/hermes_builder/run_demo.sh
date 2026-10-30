#!/bin/sh
set -eu
exec python3 adapter.py adapter_config.json --demo --timeout 90 "$@"
