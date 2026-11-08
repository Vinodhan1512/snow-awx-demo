#!/usr/bin/env bash
# Run this ON THE AWX SERVER (as root or with sudo). Connects it to the Cisco DevNet sandbox VPN
# with a split tunnel: only 10.10.20.0/24 goes through the VPN; internet, DNS and AWX are untouched.
# Installs openconnect, creates a systemd service that reconnects automatically, and tests the lab router.
#
#   curl -fsSL https://raw.githubusercontent.com/Vinodhan1512/snow-awx-demo/main/scripts/sandbox_vpn_host.sh -o vpn.sh
#   sudo bash vpn.sh                       # prompts for the 3 VPN values from the sandbox "Access Details"
#   # or non-interactive:
#   sudo VPN_HOST='devnetsandbox-usw1-reservation.cisco.com:20xxx' VPN_USER='...' VPN_PASS='...' bash vpn.sh
#
# Remove later:  sudo systemctl disable --now sandbox-vpn
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }
ROUTER="${ROUTER:-10.10.20.48}"

[[ -n "${VPN_HOST:-}" ]] || read -rp "vpn_address (host:port): " VPN_HOST
[[ -n "${VPN_USER:-}" ]] || read -rp "vpn_username: " VPN_USER
[[ -n "${VPN_PASS:-}" ]] || { read -rsp "vpn_password: " VPN_PASS; echo; }

echo "==> Installing openconnect"
if command -v apt-get >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openconnect vpnc-scripts >/dev/null
elif command -v dnf >/dev/null || command -v yum >/dev/null; then
  PM=$(command -v dnf || command -v yum)
  . /etc/os-release
  if [[ "${ID}" == "amzn" ]]; then
    $PM install -y openconnect 2>/dev/null || { echo "openconnect is not packaged for Amazon Linux. Use an Ubuntu/RHEL host, or ask for the container-based option."; exit 1; }
  else
    $PM install -y openconnect 2>/dev/null || { $PM install -y epel-release || $PM install -y "https://dl.fedoraproject.org/pub/epel/epel-release-latest-${VERSION_ID%%.*}.noarch.rpm"; $PM install -y openconnect; }
  fi
else
  echo "Unsupported OS: install openconnect manually, then re-run."; exit 1
fi

VPNC=""
for f in /usr/share/vpnc-scripts/vpnc-script /etc/vpnc/vpnc-script /usr/libexec/vpnc-script; do [[ -x "$f" ]] && VPNC="$f" && break; done
[[ -n "$VPNC" ]] || { echo "vpnc-script not found after install"; exit 1; }
OC=$(command -v openconnect)

install -d -m 700 /etc/openconnect
printf '%s\n' "$VPN_PASS" > /etc/openconnect/sandbox.pass; chmod 600 /etc/openconnect/sandbox.pass
cat > /etc/openconnect/split.sh <<EOF
#!/bin/sh
# Only the DevNet lab network goes through the tunnel; default route and DNS stay as they are.
export CISCO_SPLIT_INC=1
export CISCO_SPLIT_INC_0_ADDR=10.10.20.0
export CISCO_SPLIT_INC_0_MASK=255.255.255.0
export CISCO_SPLIT_INC_0_MASKLEN=24
export CISCO_SPLIT_INC_0_PROTOCOL=0 CISCO_SPLIT_INC_0_SPORT=0 CISCO_SPLIT_INC_0_DPORT=0
unset INTERNAL_IP4_DNS INTERNAL_IP6_DNS CISCO_DEF_DOMAIN CISCO_SPLIT_DNS
unset INTERNAL_IP6_ADDRESS INTERNAL_IP6_NETMASK CISCO_IPV6_SPLIT_INC
exec $VPNC "\$@"
EOF
chmod 755 /etc/openconnect/split.sh

echo "==> Checking the VPN login"
PROBE=$($OC --authenticate --non-inter --protocol=anyconnect --user="$VPN_USER" --passwd-on-stdin "$VPN_HOST" < /etc/openconnect/sandbox.pass 2>&1 || true)
if grep -qiE "login failed|failed to complete authentication" <<<"$PROBE"; then
  echo "The VPN rejected the username/password - copy them again from the sandbox Access Details."; exit 1
fi
PIN=$(grep -oE 'pin-sha256:[A-Za-z0-9+/=]+' <<<"$PROBE" | head -1 || true)

cat > /etc/systemd/system/sandbox-vpn.service <<EOF
[Unit]
Description=Cisco DevNet sandbox VPN (split tunnel 10.10.20.0/24)
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/bin/sh -c 'exec $OC --protocol=anyconnect --non-inter --user="$VPN_USER" --passwd-on-stdin --script=/etc/openconnect/split.sh ${PIN:+--servercert $PIN} "$VPN_HOST" < /etc/openconnect/sandbox.pass'
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sandbox-vpn >/dev/null 2>&1
systemctl restart sandbox-vpn

echo "==> Waiting for the lab router ($ROUTER:22)"
for i in $(seq 1 30); do
  if timeout 3 bash -c "</dev/tcp/$ROUTER/22" 2>/dev/null; then
    echo "VPN is up: $ROUTER:22 is reachable. AWX jobs on this server can now reach the Cisco sandbox."; exit 0
  fi
  sleep 3
done
echo "Router not reachable yet. Check: journalctl -u sandbox-vpn -n 40 --no-pager"; exit 1
