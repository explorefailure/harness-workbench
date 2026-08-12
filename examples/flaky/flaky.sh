#!/bin/sh
# A deliberately flaky integration check. No model, no network, no daemon.
#
# It fails its first $FLAKY_FAIL_TIMES invocations (default 2) and succeeds
# after that, counting in a state file beside itself. Deterministic ON
# PURPOSE: a demo that flips a coin cannot show that retry made the
# difference, because you can never tell a rescued run from a lucky one.
#
# Reset with:  rm -f examples/flaky/.flaky-state
set -e

STATE="${FLAKY_STATE:-$(dirname "$0")/.flaky-state}"
LIMIT="${FLAKY_FAIL_TIMES:-2}"

n=0
[ -f "$STATE" ] && n=$(cat "$STATE")
n=$((n + 1))
echo "$n" > "$STATE"

echo "checking $1 (invocation $n)"

# An unmistakably synthetic sentinel gives `redact` real bytes to scrub from
# captured output without imitating any credential format.
echo "fixture: redaction-fixture-FIXTUREBYTES"

if [ "$n" -le "$LIMIT" ]; then
  echo "connection reset by peer" >&2
  exit 1
fi

echo "ok: $1 healthy"
