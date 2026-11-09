#!/usr/bin/env bash
# Run this ON THE AWX SERVER (with sudo). Connects it to the Cisco DevNet sandbox VPN with a split tunnel:
# only 10.10.20.0/24 goes through the VPN; internet, DNS and AWX are untouched. Reconnects automatically.
#   Ubuntu / RHEL-family : installs openconnect and runs it as a systemd service
#   Amazon Linux         : no openconnect package exists, so it runs openconnect in a small container
#                          on the host network (routes still apply to this server)
#
#   curl -fsSL https://raw.githubusercontent.com/Vinodhan1512/snow-awx-demo/main/scripts/sandbox_vpn_host.sh -o vpn.sh
#   sudo bash vpn.sh            # prompts for the 3 VPN values from the sandbox "Access Details"
#   # or: sudo VPN_HOST='devnetsandbox-usw1-reservation.cisco.com:20xxx' VPN_USER='...' VPN_PASS='...' bash vpn.sh
#
# Remove later:  sudo systemctl disable --now sandbox-vpn     (Amazon Linux: sudo docker rm -f sandbox-vpn)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }
ROUTER="${ROUTER:-10.10.20.48}"

[[ -n "${VPN_HOST:-}" ]] || read -rp "vpn_address (host:port): " VPN_HOST
[[ -n "${VPN_USER:-}" ]] || read -rp "vpn_username: " VPN_USER
[[ -n "${VPN_PASS:-}" ]] || { read -rsp "vpn_password: " VPN_PASS; echo; }

install -d -m 700 /etc/openconnect
printf '%s\n' "$VPN_PASS" > /etc/openconnect/sandbox.pass
printf '%s' "$VPN_HOST" > /etc/openconnect/host
printf '%s' "$VPN_USER" > /etc/openconnect/user
chmod 600 /etc/openconnect/sandbox.pass

# Split-tunnel wrapper around vpnc-script: $1 = path of vpnc-script on the system that will run openconnect
write_split() {
  {
    echo '#!/bin/sh'
    echo '# Only the DevNet lab network goes through the tunnel; default route and DNS stay as they are.'
    echo 'export CISCO_SPLIT_INC=1'
    echo 'export CISCO_SPLIT_INC_0_ADDR=10.10.20.0'
    echo 'export CISCO_SPLIT_INC_0_MASK=255.255.255.0'
    echo 'export CISCO_SPLIT_INC_0_MASKLEN=24'
    echo 'export CISCO_SPLIT_INC_0_PROTOCOL=0 CISCO_SPLIT_INC_0_SPORT=0 CISCO_SPLIT_INC_0_DPORT=0'
    echo 'unset INTERNAL_IP4_DNS INTERNAL_IP6_DNS CISCO_DEF_DOMAIN CISCO_SPLIT_DNS'
    echo 'unset INTERNAL_IP6_ADDRESS INTERNAL_IP6_NETMASK CISCO_IPV6_SPLIT_INC'
    echo "exec $1 \"\$@\""
  } > /etc/openconnect/split.sh
  chmod 755 /etc/openconnect/split.sh
}

wait_router() {
  echo "==> Waiting for the lab router ($ROUTER:22)"
  for _ in $(seq 1 40); do
    if timeout 3 bash -c "</dev/tcp/$ROUTER/22" 2>/dev/null; then
      echo "VPN is up: $ROUTER:22 is reachable. AWX jobs on this server can now reach the Cisco sandbox."
      exit 0
    fi
    sleep 3
  done
  echo "Router not reachable yet. Check: $1"
  exit 1
}

# ---------- install ----------
MODE=native
echo "==> Installing openconnect"
if command -v apt-get >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openconnect vpnc-scripts >/dev/null
elif command -v dnf >/dev/null || command -v yum >/dev/null; then
  PM=$(command -v dnf || command -v yum)
  . /etc/os-release
  if ! $PM install -y openconnect >/dev/null 2>&1; then
    if [[ "${ID}" == "amzn" ]]; then
      MODE=container
    else
      $PM install -y epel-release >/dev/null 2>&1 \
        || $PM install -y "https://dl.fedoraproject.org/pub/epel/epel-release-latest-${VERSION_ID%%.*}.noarch.rpm"
      $PM install -y openconnect
    fi
  fi
else
  echo "Unsupported OS: install openconnect manually, then re-run."; exit 1
fi

# ---------- Amazon Linux: containerised openconnect on the host network ----------
if [[ "$MODE" == "container" ]]; then
  echo "==> Amazon Linux detected: running openconnect in a container on the host network"
  command -v docker >/dev/null || $PM install -y docker >/dev/null
  systemctl enable --now docker >/dev/null 2>&1
  modprobe tun 2>/dev/null || true
  write_split /etc/vpnc/vpnc-script            # path inside the Alpine container
  cat > /etc/openconnect/run.sh <<'RUN'
#!/bin/sh
apk add --no-cache openconnect vpnc iproute2 >/dev/null
U="$(cat /etc/openconnect/user)"; H="$(cat /etc/openconnect/host)"
PIN=$(openconnect --authenticate --non-inter --protocol=anyconnect --user="$U" --passwd-on-stdin "$H" \
      < /etc/openconnect/sandbox.pass 2>&1 | grep -o 'pin-sha256:[A-Za-z0-9+/=]*' | head -1)
exec openconnect --protocol=anyconnect --non-inter --user="$U" --passwd-on-stdin \
     --script=/etc/openconnect/split.sh ${PIN:+--servercert $PIN} "$H" < /etc/openconnect/sandbox.pass
RUN
  chmod 755 /etc/openconnect/run.sh
  docker rm -f sandbox-vpn >/dev/null 2>&1 || true
  docker run -d --name sandbox-vpn --restart always --network host --cap-add NET_ADMIN --device /dev/net/tun \
    -v /etc/openconnect:/etc/openconnect alpine:3.20 /etc/openconnect/run.sh >/dev/null
  wait_router "sudo docker logs --tail 40 sandbox-vpn"
fi

# ---------- Ubuntu / RHEL: native openconnect as a systemd service ----------
VPNC=""
for f in /usr/share/vpnc-scripts/vpnc-script /etc/vpnc/vpnc-script /usr/libexec/vpnc-script; do
  [[ -x "$f" ]] && VPNC="$f" && break
done
[[ -n "$VPNC" ]] || { echo "vpnc-script not found after install"; exit 1; }
OC=$(command -v openconnect)
write_split "$VPNC"

echo "==> Checking the VPN login"
PROBE=$($OC --authenticate --non-inter --protocol=anyconnect --user="$VPN_USER" --passwd-on-stdin "$VPN_HOST" \
        < /etc/openconnect/sandbox.pass 2>&1 || true)
if grep -qiE "login failed|failed to complete authentication" <<<"$PROBE"; then
  echo "The VPN rejected the username/password - copy them again from the sandbox Access Details."; exit 1
fi
PIN=$(grep -oE 'pin-sha256:[A-Za-z0-9+/=]+' <<<"$PROBE" | head -1 || true)

cat > /etc/systemd/system/sandbox-vpn.service <<UNIT
[Unit]
Description=Cisco DevNet sandbox VPN (split tunnel 10.10.20.0/24)
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/bin/sh -c 'exec $OC --protocol=anyconnect --non-inter --user="\$\$(cat /etc/openconnect/user)" --passwd-on-stdin --script=/etc/openconnect/split.sh ${PIN:+--servercert $PIN} "\$\$(cat /etc/openconnect/host)" < /etc/openconnect/sandbox.pass'
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable sandbox-vpn >/dev/null 2>&1
systemctl restart sandbox-vpn
wait_router "sudo journalctl -u sandbox-vpn -n 40 --no-pager"
