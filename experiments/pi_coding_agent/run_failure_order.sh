#!/bin/sh
set -eu
exec python3.11 failure_order_runner.py --variant "${1:?variant required}"
