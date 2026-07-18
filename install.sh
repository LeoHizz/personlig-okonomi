#!/usr/bin/env bash
# Installasjon av "Personlig økonomi" på en fersk Ubuntu (VM eller LXC i Proxmox).
# Kjør som root eller med sudo:  sudo bash install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo "==> Personlig økonomi — installasjon"
echo "    Mappe: $APP_DIR"

# 1) Docker
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installerer Docker …"
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
else
  echo "==> Docker finnes allerede."
fi

# 2) .env
if [ ! -f .env ]; then
  echo "==> Lager .env fra mal. FYLL INN nøkler og APP_BASE_URL etterpå!"
  cp .env.example .env
  # Sett en tilfeldig SECRET_KEY
  RAND="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40)"
  sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${RAND}/" .env
  # Foreslå server-IP som APP_BASE_URL
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  if [ -n "${IP:-}" ]; then
    sed -i "s#^APP_BASE_URL=.*#APP_BASE_URL=http://${IP}:8080#" .env
  fi
  echo
  echo "  >>> Åpne .env og lim inn GOCARDLESS_SECRET_ID og GOCARDLESS_SECRET_KEY."
  echo "  >>> Kontroller at APP_BASE_URL peker på riktig adresse (nå: http://${IP:-DIN_IP}:8080)."
  echo
else
  echo "==> .env finnes allerede — beholder den."
fi

mkdir -p data

# 2b) Enable Banking-nøkkel
if [ ! -f data/enablebanking_private.pem ]; then
  echo "==> Genererer RSA-nøkkel for Enable Banking …"
  openssl genrsa -out data/enablebanking_private.pem 4096 2>/dev/null
  openssl rsa -in data/enablebanking_private.pem -pubout -out data/enablebanking_public.pem 2>/dev/null
  openssl req -new -x509 -days 3650 -key data/enablebanking_private.pem -out data/enablebanking_cert.pem -subj "/CN=personlig-okonomi" 2>/dev/null
  chmod 600 data/enablebanking_private.pem
  echo "  >>> Registrer app på https://enablebanking.com/cp (Environment: Production,"
  echo "      nøkkel: «Generate outside the browser and import public certificate»),"
  echo "      lim inn sertifikatet under, og legg Application ID i .env. Se ENABLEBANKING_SETUP.md."
  echo "  ---- data/enablebanking_cert.pem ----"
  cat data/enablebanking_cert.pem
  echo "  -------------------------------------"
fi

# 3) Bygg og start
echo "==> Bygger og starter containeren …"
docker compose up -d --build

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "==> Ferdig! Åpne dashboardet på:  http://${IP:-DIN_IP}:8080"
echo "    Husk å redigere .env (nøkler + APP_BASE_URL) og deretter:  docker compose up -d"
echo "    Logg:      docker compose logs -f"
echo "    Oppdater:  git pull && docker compose up -d --build   (eller kjør install.sh på nytt)"
