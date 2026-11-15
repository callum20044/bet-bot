#!/usr/bin/env bash
# Open the UK tunnel to Betfair through Callum's Oracle server (London). Betfair blocks GitHub's US
# addresses, so Betfair's three addresses are pointed at local ports that ssh forwards via the UK server.
# Bot code needs no changes: it still calls https://identitysso.betfair.com etc. and the certificates match.
# Needs secrets BETFAIR_SSH_HOST / BETFAIR_SSH_KEY / BETFAIR_SSH_HOSTKEY (set by server/setup.sh).
set -euo pipefail
: "${BF_HOST:?missing BETFAIR_SSH_HOST secret}"
d=$(mktemp -d); chmod 700 "$d"
printf '%s\n' "$BF_KEY" > "$d/key"; chmod 600 "$d/key"
printf '%s\n' "$BF_HOSTKEY" > "$d/known_hosts"
printf '127.0.0.2 identitysso.betfair.com\n127.0.0.3 api.betfair.com\n127.0.0.4 identitysso-cert.betfair.com\n' | sudo tee -a /etc/hosts >/dev/null
sudo ssh -f -N -i "$d/key" -o UserKnownHostsFile="$d/known_hosts" -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ConnectTimeout=20 \
  -L 127.0.0.2:443:identitysso.betfair.com:443 -L 127.0.0.3:443:api.betfair.com:443 \
  -L 127.0.0.4:443:identitysso-cert.betfair.com:443 "rt@$BF_HOST"
sleep 2
echo "UK tunnel to Betfair is open"
