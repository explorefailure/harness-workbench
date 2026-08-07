#!/bin/sh
# The stateless companion to flaky.sh.
#
# Campaigns (blast, catch, efficacy) run a spec MANY times and compare the
# records. A workload that remembers previous runs makes every one of those
# comparisons a measurement of the leftover state instead -- so the campaign
# examples use this, and flaky.sh stays for the single-run demos.
set -e
echo "config: $(cat "$1")"
echo "ok: checked $1"
