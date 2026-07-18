#!/usr/bin/env bash
# =============================================================================
#  Personlig økonomi — oppdater en eksisterende LXC med ny kode
# -----------------------------------------------------------------------------
#  Kjøres på PROXMOX-VERTEN fra prosjektmappa, når du har endret koden.
#     CTID=210 bash proxmox-lxc-update.sh
# =============================================================================
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SRC_DIR"

CTID="${CTID:?Sett CTID, f.eks: CTID=210 bash proxmox-lxc-update.sh}"

if [ ! -d backend ] || [ ! -d frontend ]; then
  echo "FEIL: kjør fra prosjektmappa (mangler backend/ og frontend/)." >&2
  exit 1
fi

echo "==> Kopierer ny kode til container $CTID …"
TARBALL="/tmp/okonomi-upd-$$.tar.gz"
tar czf "$TARBALL" backend frontend
pct push "$CTID" "$TARBALL" /root/okonomi-upd.tar.gz
# behold data/ og .env, bytt ut backend/ og frontend/
pct exec "$CTID" -- bash -c "rm -rf /opt/okonomi/backend /opt/okonomi/frontend && tar xzf /root/okonomi-upd.tar.gz -C /opt/okonomi && rm -f /root/okonomi-upd.tar.gz"
rm -f "$TARBALL"

echo "==> Oppdaterer avhengigheter …"
pct exec "$CTID" -- bash -c "cd /opt/okonomi && .venv/bin/pip install -q -r backend/requirements.txt"

echo "==> Restarter tjenesten …"
pct exec "$CTID" -- systemctl restart okonomi

sleep 2
STATUS="$(pct exec "$CTID" -- systemctl is-active okonomi.service 2>/dev/null || echo ukjent)"
echo "✅ Oppdatert. Tjeneste-status: $STATUS"
