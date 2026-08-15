#!/bin/sh
set -eu
exec python3.11 policy_order_runner.py --variant "${1:?variant required}"
