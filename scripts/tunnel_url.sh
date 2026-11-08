#!/usr/bin/env bash
# Prints the current public HTTPS URL of the AWX quick tunnel (waits up to 60s).
for i in $(seq 1 30); do
  url=$(journalctl -u awx-tunnel --no-pager -o cat 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  if [[ -n "$url" ]]; then echo "$url"; exit 0; fi
  sleep 2
done
echo "Could not find tunnel URL. Check: journalctl -u awx-tunnel" >&2
exit 1
