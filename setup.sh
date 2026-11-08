#!/usr/bin/env bash
# One-shot setup on the EC2 instance (run as root from the repo folder):
#   ./setup.sh               full install + configuration + smoke test
#   ./setup.sh refresh-url   after a reboot/stop-start: re-point ServiceNow to the new tunnel URL
#   ./setup.sh reconfigure   re-run AWX + ServiceNow configuration only (e.g. new Cisco credentials)
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
say() { printf '\n\033[1;32m### %s\033[0m\n' "$*"; }

[[ -f .env ]] || { echo "Missing .env - run: cp .env.example .env && nano .env"; exit 1; }
set -a; source ./.env; set +a
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

EXISTING_AWX=false
[[ -n "${AWX_URL:-}" && "${AWX_URL}" != *localhost* ]] && EXISTING_AWX=true
export AWX_URL="${AWX_URL%/}"
for v in SN_INSTANCE SN_USERNAME SN_PASSWORD GIT_REPO_URL AWX_ADMIN_PASSWORD CISCO_HOST CISCO_USERNAME CISCO_PASSWORD; do
  [[ -n "${!v:-}" ]] || { echo ".env: $v is empty"; exit 1; }
done
SN_INSTANCE="${SN_INSTANCE#https://}"; export SN_INSTANCE="${SN_INSTANCE%/}"
chmod +x scripts/*.sh

mode="${1:-full}"

if [[ "$EXISTING_AWX" == "true" ]]; then
  export AWX_PUBLIC_URL="$AWX_URL"
  say "Using existing AWX at $AWX_URL (no install, no tunnel)"
  K=""; [[ "${AWX_VERIFY_TLS:-true}" == "false" ]] && K="-k"
  curl -fsS $K "$AWX_URL/api/v2/ping/" >/dev/null \
    || { echo "Cannot reach $AWX_URL/api/v2/ping/ - check the URL, or set AWX_VERIFY_TLS=false for a self-signed cert"; exit 1; }
  [[ "$mode" == "refresh-url" ]] && { python3 scripts/snow_config.py --url-only; exit 0; }
  mode=reconfigure
fi

if [[ "$mode" == "refresh-url" ]]; then
  export AWX_PUBLIC_URL=$(scripts/tunnel_url.sh)
  python3 scripts/snow_config.py --url-only
  say "ServiceNow now points to $AWX_PUBLIC_URL"
  exit 0
fi

if [[ "$mode" == "full" ]]; then
  [[ $EUID -eq 0 ]] || { echo "Installing AWX needs root: sudo -i, then ./setup.sh"; exit 1; }
  say "1/4  Installing k3s, AWX and the tunnel (10-15 min on first run)"
  bash scripts/install_awx.sh
fi

say "2/4  Configuring AWX"
python3 scripts/awx_config.py

say "3/4  Configuring ServiceNow"
[[ "$EXISTING_AWX" == "true" ]] || export AWX_PUBLIC_URL=$(scripts/tunnel_url.sh)
python3 scripts/snow_config.py

if [[ "${RUN_SMOKE_TEST:-true}" == "true" ]]; then
  say "4/4  Smoke test: read-only diagnostics incident"
  python3 scripts/create_ticket.py diag || echo "Smoke test did not pass - see README > Troubleshooting"
fi

cat <<EOF

=====================================================================
 READY FOR THE DEMO
 AWX UI ........ $AWX_PUBLIC_URL      (admin / your AWX_ADMIN_PASSWORD)
 ServiceNow .... https://$SN_INSTANCE
 Run-sheet ..... README.md  >  "Live demo script"

 After stopping/starting the EC2 instance, run:  ./setup.sh refresh-url
=====================================================================
EOF
