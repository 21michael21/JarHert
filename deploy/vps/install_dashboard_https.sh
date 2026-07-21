#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/require_personal_vps.sh"
require_personal_vps_local

DOMAIN="${JARHERT_DASHBOARD_DOMAIN:?set JARHERT_DASHBOARD_DOMAIN=your-public-domain}"
if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Dashboard domain contains unsupported characters." >&2
  exit 2
fi

if command -v caddy >/dev/null 2>&1 && [[ -f /etc/caddy/Caddyfile ]] && \
   ! grep -q '^# JarHert Dashboard managed file$' /etc/caddy/Caddyfile; then
  echo "Refusing to overwrite an existing unmanaged Caddyfile." >&2
  exit 2
fi

sudo apt-get update
sudo apt-get install -y caddy
sudo install -d -m 755 /etc/caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
# JarHert Dashboard managed file
$DOMAIN {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8788
}
EOF
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo systemctl enable --now caddy
sudo systemctl reload caddy

echo "dashboard_https=ready url=https://$DOMAIN"
