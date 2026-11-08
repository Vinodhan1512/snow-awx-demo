#!/usr/bin/env bash
# Installs k3s + AWX Operator + AWX (NodePort 30080) + a cloudflared quick tunnel.
# Idempotent: safe to re-run.
set -euo pipefail
AWX_OPERATOR_VERSION="${AWX_OPERATOR_VERSION:-2.19.1}"
NS=awx
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

: "${AWX_ADMIN_PASSWORD:?AWX_ADMIN_PASSWORD missing in .env}"

# ---------- base packages ----------
say "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl jq python3 >/dev/null

# ---------- k3s ----------
if ! command -v k3s >/dev/null; then
  say "Installing k3s"
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
say "Waiting for the k3s node"
until kubectl get nodes 2>/dev/null | grep -q ' Ready'; do sleep 3; done

# ---------- AWX operator ----------
WORK=/opt/awx-install; mkdir -p "$WORK"
kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"

cat > "$WORK/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - github.com/ansible/awx-operator/config/default?ref=${AWX_OPERATOR_VERSION}
images:
  - name: quay.io/ansible/awx-operator
    newTag: ${AWX_OPERATOR_VERSION}
namespace: ${NS}
EOF
say "Installing AWX Operator ${AWX_OPERATOR_VERSION}"
kubectl apply -k "$WORK"
kubectl -n "$NS" rollout status deployment/awx-operator-controller-manager --timeout=600s

# ---------- AWX instance ----------
kubectl -n "$NS" create secret generic awx-admin-password \
  --from-literal=password="$AWX_ADMIN_PASSWORD" --dry-run=client -o yaml | kubectl apply -f -

cat > "$WORK/awx.yaml" <<EOF
apiVersion: awx.ansible.com/v1beta1
kind: AWX
metadata:
  name: awx
  namespace: ${NS}
spec:
  service_type: nodeport
  nodeport_port: 30080
  admin_user: admin
  admin_password_secret: awx-admin-password
  web_resource_requirements:
    requests: {cpu: 100m, memory: 256Mi}
  task_resource_requirements:
    requests: {cpu: 100m, memory: 256Mi}
  ee_resource_requirements:
    requests: {cpu: 100m, memory: 128Mi}
EOF
kubectl apply -f "$WORK/awx.yaml"

say "Waiting for AWX to come up (first install takes 5-10 minutes)"
for i in $(seq 1 120); do
  if curl -fs http://localhost:30080/api/v2/ping/ >/dev/null 2>&1; then break; fi
  sleep 10; printf '.'
done
echo
curl -fs http://localhost:30080/api/v2/ping/ >/dev/null || { echo "AWX did not come up. Check: kubectl -n awx get pods"; exit 1; }
# migrations can still be finishing after ping answers
until curl -fs -u "admin:${AWX_ADMIN_PASSWORD}" http://localhost:30080/api/v2/me/ >/dev/null 2>&1; do sleep 5; printf '.'; done
echo; say "AWX is up on http://localhost:30080"

# ---------- cloudflared quick tunnel ----------
if ! command -v cloudflared >/dev/null; then
  say "Installing cloudflared"
  curl -fsSL -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
  dpkg -i /tmp/cloudflared.deb >/dev/null
fi
cat > /etc/systemd/system/awx-tunnel.service <<'EOF'
[Unit]
Description=Cloudflare quick tunnel for AWX demo
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url http://localhost:30080
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable awx-tunnel >/dev/null 2>&1
systemctl restart awx-tunnel
say "Tunnel started"
