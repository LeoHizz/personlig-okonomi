#!/usr/bin/env bash
# =============================================================================
#  Personlig økonomi — automatisk LXC-oppsett for Proxmox
# -----------------------------------------------------------------------------
#  Kjøres på PROXMOX-VERTEN (ikke inne i en container), som root.
#
#  Forutsetning: hele prosjektmappa er kopiert til verten, og du kjører dette
#  skriptet fra inne i den mappa. Eksempel:
#     scp -r "Personlig økonomi" root@proxmox:/root/okonomi-src
#     ssh root@proxmox
#     cd /root/okonomi-src && bash proxmox-lxc-install.sh
#
#  Valgfrie miljøvariabler (kan settes før du kjører):
#     CTID=210            # container-id (default: neste ledige)
#     HOSTNAME=okonomi    # containernavn
#     CORES=1 RAM=1024 DISK=6 BRIDGE=vmbr0 STORAGE=local-lvm
#     GOCARDLESS_SECRET_ID=...  GOCARDLESS_SECRET_KEY=...  # bakes rett inn
# =============================================================================
set -euo pipefail

# ---- kjør fra prosjektmappa ----
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SRC_DIR"
if [ ! -d backend ] || [ ! -d frontend ]; then
  echo "FEIL: kjør dette skriptet fra prosjektmappa (mangler backend/ og frontend/)." >&2
  exit 1
fi
if ! command -v pct >/dev/null 2>&1; then
  echo "FEIL: 'pct' ikke funnet. Dette skriptet må kjøres på Proxmox-verten." >&2
  exit 1
fi

# ---- innstillinger ----
CTID="${CTID:-$(pvesh get /cluster/nextid)}"
HOSTNAME="${HOSTNAME:-okonomi}"
CORES="${CORES:-1}"
RAM="${RAM:-1024}"
DISK="${DISK:-6}"
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORE="${TEMPLATE_STORE:-local}"
PORT=8080

# Innlogging (HTTP Basic).
#   - ikke satt          -> generer sterkt passord (skrives ut på slutten)
#   - APP_PASSWORD=off    -> slå AV appens eget passord (f.eks. når Cloudflare
#                           Access / annen proxy allerede krever innlogging)
#   - APP_PASSWORD=<verdi> -> bruk din egen
APP_USER="${APP_USER:-familien}"
APP_PASSWORD_GENERATED=0
if [ "${APP_PASSWORD:-}" = "off" ] || [ "${APP_PASSWORD:-}" = "none" ]; then
  APP_PASSWORD=""
elif [ -z "${APP_PASSWORD:-}" ]; then
  APP_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
  APP_PASSWORD_GENERATED=1
fi

echo "=========================================================="
echo "  Personlig økonomi -> ny LXC"
echo "  CTID=$CTID  navn=$HOSTNAME  cores=$CORES  ram=${RAM}MB  disk=${DISK}GB"
echo "  bridge=$BRIDGE  storage=$STORAGE"
echo "=========================================================="

# ---- 1) skaff Debian-mal ----
echo "==> Finner Debian 12-mal …"
pveam update >/dev/null 2>&1 || true
TMPL_NAME="$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -n1)"
if [ -z "$TMPL_NAME" ]; then
  echo "FEIL: fant ingen debian-12-standard-mal i 'pveam available'." >&2
  exit 1
fi
if ! pveam list "$TEMPLATE_STORE" 2>/dev/null | grep -q "$TMPL_NAME"; then
  echo "==> Laster ned mal $TMPL_NAME …"
  pveam download "$TEMPLATE_STORE" "$TMPL_NAME"
fi
TEMPLATE="${TEMPLATE_STORE}:vztmpl/${TMPL_NAME}"

# ---- 2) opprett container ----
echo "==> Oppretter container $CTID …"
pct create "$CTID" "$TEMPLATE" \
  --hostname "$HOSTNAME" \
  --cores "$CORES" --memory "$RAM" --swap 512 \
  --rootfs "${STORAGE}:${DISK}" \
  --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
  --unprivileged 1 \
  --features nesting=0 \
  --onboot 1 \
  --description "Personlig økonomi dashboard"

echo "==> Starter container …"
pct start "$CTID"

# ---- 3) vent på nettverk ----
echo "==> Venter på IP-adresse …"
IP=""
for _ in $(seq 1 30); do
  IP="$(pct exec "$CTID" -- bash -c "hostname -I 2>/dev/null | awk '{print \$1}'" 2>/dev/null || true)"
  [ -n "$IP" ] && break
  sleep 2
done
if [ -z "$IP" ]; then
  echo "ADVARSEL: fant ingen IP automatisk. Sjekk containerens nettverk." >&2
  IP="DIN_CONTAINER_IP"
fi
echo "    IP: $IP"

# ---- 4) installer avhengigheter i containeren ----
echo "==> Installerer Python i containeren …"
pct exec "$CTID" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip ca-certificates tzdata >/dev/null"

echo "==> Setter tidssone (Europe/Oslo) …"
pct exec "$CTID" -- bash -c "ln -sf /usr/share/zoneinfo/Europe/Oslo /etc/localtime && echo 'Europe/Oslo' > /etc/timezone" 2>/dev/null || true

# ---- 5) kopier appen inn ----
echo "==> Kopierer appfiler inn i containeren …"
TARBALL="/tmp/okonomi-app-$$.tar.gz"
tar czf "$TARBALL" backend frontend
pct exec "$CTID" -- mkdir -p /opt/okonomi
pct push "$CTID" "$TARBALL" /root/okonomi-app.tar.gz
pct exec "$CTID" -- bash -c "tar xzf /root/okonomi-app.tar.gz -C /opt/okonomi && rm -f /root/okonomi-app.tar.gz && mkdir -p /opt/okonomi/data"
rm -f "$TARBALL"

# ---- 6) virtuelt miljø + avhengigheter ----
echo "==> Setter opp Python-miljø (kan ta et par minutter) …"
pct exec "$CTID" -- bash -c "export DEBIAN_FRONTEND=noninteractive; apt-get install -y -qq openssl >/dev/null; cd /opt/okonomi && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r backend/requirements.txt"

# ---- 6b) Enable Banking: generer RSA-nøkkelpar ----
echo "==> Genererer RSA-nøkkel for Enable Banking …"
pct exec "$CTID" -- bash -c "
  KEY=/opt/okonomi/data/enablebanking_private.pem
  if [ ! -f \"\$KEY\" ]; then
    openssl genrsa -out \"\$KEY\" 4096 2>/dev/null
    openssl rsa -in \"\$KEY\" -pubout -out /opt/okonomi/data/enablebanking_public.pem 2>/dev/null
    openssl req -new -x509 -days 3650 -key \"\$KEY\" -out /opt/okonomi/data/enablebanking_cert.pem -subj '/CN=personlig-okonomi' 2>/dev/null
    chmod 600 \"\$KEY\"
  fi
"

# ---- 7) .env ----
echo "==> Lager .env …"
RAND_SECRET="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40)"
PROVIDER="${PROVIDER:-enablebanking}"
EB_APP_ID="${ENABLEBANKING_APP_ID:-}"
GC_ID="${GOCARDLESS_SECRET_ID:-}"
GC_KEY="${GOCARDLESS_SECRET_KEY:-}"
# Offentlig adresse: sett APP_BASE_URL=https://dittdomene.io hvis appen nås via
# reverse proxy / Cloudflare Tunnel. Ellers brukes container-IP.
APP_BASE_URL="${APP_BASE_URL:-http://${IP}:${PORT}}"
pct exec "$CTID" -- bash -c "cat > /opt/okonomi/.env <<EOF
PROVIDER=${PROVIDER}
ENABLEBANKING_APP_ID=${EB_APP_ID}
ENABLEBANKING_KEY_PATH=/opt/okonomi/data/enablebanking_private.pem
GOCARDLESS_SECRET_ID=${GC_ID}
GOCARDLESS_SECRET_KEY=${GC_KEY}
APP_BASE_URL=${APP_BASE_URL}
PORT=${PORT}
COUNTRY=no
ACCESS_DAYS=90
APP_USER=${APP_USER}
APP_PASSWORD=${APP_PASSWORD}
SECRET_KEY=${RAND_SECRET}
SYNC_MIN_INTERVAL_HOURS=6
AUTO_SYNC=1
AUTO_SYNC_HOUR=5
DB_PATH=/opt/okonomi/data/okonomi.db
EOF"

# ---- 8) systemd-tjeneste ----
echo "==> Oppretter systemd-tjeneste …"
pct exec "$CTID" -- bash -c "cat > /etc/systemd/system/okonomi.service <<'EOF'
[Unit]
Description=Personlig okonomi dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/okonomi
EnvironmentFile=/opt/okonomi/.env
ExecStart=/opt/okonomi/.venv/bin/uvicorn app.main:app --app-dir /opt/okonomi/backend --host 0.0.0.0 --port 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF"
pct exec "$CTID" -- bash -c "systemctl daemon-reload && systemctl enable --now okonomi.service >/dev/null 2>&1"

sleep 2
STATUS="$(pct exec "$CTID" -- systemctl is-active okonomi.service 2>/dev/null || echo ukjent)"

echo
echo "=========================================================="
echo "  ✅ Ferdig! Container $CTID ($HOSTNAME) kjører."
echo "     Tjeneste-status: $STATUS"
echo
echo "  Åpne dashbordet:   ${APP_BASE_URL}"
echo "  (container internt: http://${IP}:${PORT}  – pek reverse proxy / cloudflared hit)"
echo
if [ -z "$APP_PASSWORD" ]; then
  echo "  🔐 Appens eget passord er AV — tilgang styres av din proxy (Cloudflare Access)."
else
  echo "  🔐 Innlogging (nettleseren spør om brukernavn/passord):"
  echo "        Brukernavn: ${APP_USER}"
  echo "        Passord:    ${APP_PASSWORD}"
  if [ "$APP_PASSWORD_GENERATED" = "1" ]; then
    echo "     (auto-generert — noter det ned nå! Ligger også i /opt/okonomi/.env)"
  fi
fi
echo "     Endre senere: pct exec $CTID -- nano /opt/okonomi/.env  (så: systemctl restart okonomi)"
echo
if [ "$PROVIDER" = "enablebanking" ] && [ -z "$EB_APP_ID" ]; then
  echo "  ⚠  Enable Banking må registreres (gratis) for at live bankkobling skal virke:"
  echo "     1) Gå til https://enablebanking.com/cp og opprett en app:"
  echo "        - Environment:   Production"
  echo "        - Nøkkel:        «Generate outside the browser and import public certificate»"
  echo "        - Redirect URL:  ${APP_BASE_URL}/api/callback"
  echo "        - Lim inn SERTIFIKATET som vises under."
  echo "     2) Kopier Application ID og legg den inn:"
  echo "        pct exec $CTID -- nano /opt/okonomi/.env   # sett ENABLEBANKING_APP_ID"
  echo "        pct exec $CTID -- systemctl restart okonomi"
  echo "     (full guide i ENABLEBANKING_SETUP.md)"
  echo
  echo "  ---- SERTIFIKAT (lim inn i Enable Banking) ----"
  pct exec "$CTID" -- cat /opt/okonomi/data/enablebanking_cert.pem
  echo "  -----------------------------------------------"
  echo
  echo "  💡 Uten Enable Banking kan du uansett bruke CSV-import med en gang."
  echo
fi
echo "  Nyttige kommandoer (fra Proxmox-verten):"
echo "     pct exec $CTID -- systemctl status okonomi     # status"
echo "     pct exec $CTID -- journalctl -u okonomi -f      # logg"
echo "     pct exec $CTID -- systemctl restart okonomi     # restart"
echo "=========================================================="
