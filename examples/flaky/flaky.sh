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

# A token-shaped string, so `redact` has something real to scrub out of the
# captured output. It is not a credential; it is what one looks like.
#
# The prefix is deliberately not a real vendor's. A convincing imitation of
# a live-key format trips GitHub secret scanning and trufflehog on a public
# push, and a fake that pages someone is a worse demo than a fake that reads
# as fake. Keep it implausible to a scanner and obvious to a human -- and
# note that naming the real prefixes here would defeat the point too.
echo "auth: using key notakey-live-4f9a2b7c1e00"

if [ "$n" -le "$LIMIT" ]; then
  echo "connection reset by peer" >&2
  exit 1
fi

echo "ok: $1 healthy"
