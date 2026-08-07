#!/bin/sh
# a careless script: echoes a credential alongside its real output
echo "connecting with token notakey-live-4f9a2b7c1e8d"
cat "$1"
echo "done (key notakey-live-4f9a2b7c1e8d)"
