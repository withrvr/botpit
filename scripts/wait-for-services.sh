#!/usr/bin/env sh
# Block until every named compose service reports healthy.
# Usage: scripts/wait-for-services.sh postgres redis   (WAIT_TIMEOUT=60 seconds)
set -eu
timeout="${WAIT_TIMEOUT:-60}"
deadline=$(( $(date +%s) + timeout ))
while :; do
  # `ps --format json` emits one object per line; Health is "" for services without a check.
  unhealthy=$(docker compose ps --format json "$@" | python3 -c '
import json, sys
names = set(sys.argv[1:])
seen = {}
for line in sys.stdin:
    line = line.strip()
    if line:
        row = json.loads(line)
        seen[row["Service"]] = row.get("Health") or row.get("State")
print(" ".join(sorted(n for n in names if seen.get(n) != "healthy")))
' "$@")
  [ -z "$unhealthy" ] && { echo "ready: $*"; exit 0; }
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "timed out after ${timeout}s waiting for: $unhealthy" >&2
    exit 1
  fi
  sleep 1
done
