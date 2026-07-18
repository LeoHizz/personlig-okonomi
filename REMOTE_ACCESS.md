# Fjerntilgang via Cloudflare Tunnel (cloudflared) + Access

Slik eksponerer du appen på et eget subdomene (f.eks. `okonomi.hjemme.io`),
beskyttet av Cloudflare Access (Google-pålogging). Appens eget passord er da
skrudd av – Cloudflare Access er eneste innlogging.

Antatt IP for containeren i eksemplene: `192.168.1.51` (bytt til din).

## 1. Installer appen med offentlig URL og uten eget passord

På Proxmox-verten, fra prosjektmappa:
```bash
APP_BASE_URL=https://okonomi.hjemme.io \
APP_PASSWORD=off \
bash proxmox-lxc-install.sh
```
- `APP_BASE_URL` = den offentlige HTTPS-adressen (brukes som bankens retur-adresse).
- `APP_PASSWORD=off` = appen krever ikke eget passord (Cloudflare Access dekker det).

Skriptet skriver ut container-IP (intern), URL og den offentlige nøkkelen.

## 2. Rut subdomenet i cloudflared

I tunnel-konfigurasjonen (typisk `/etc/cloudflared/config.yml` der cloudflared
kjører) legg til en ingress-regel som peker på containeren:

```yaml
ingress:
  - hostname: okonomi.hjemme.io
    service: http://192.168.1.51:8080
  # ... dine eksisterende regler ...
  - service: http_status:404
```

Opprett DNS-route for vertsnavnet (peker til tunnelen):
```bash
cloudflared tunnel route dns <TUNNEL-NAVN> okonomi.hjemme.io
```
Start cloudflared på nytt:
```bash
systemctl restart cloudflared
```

> Kjører cloudflared som Docker/annet: legg til samme ingress-regel i din config
> og opprett DNS-recorden i Cloudflare-dashbordet (CNAME til `<tunnel-id>.cfargotunnel.com`).

## 3. Cloudflare Access-policy (Google-pålogging)

I Cloudflare Zero Trust-dashbordet → **Access → Applications → Add application**
→ Self-hosted:
- **Application domain**: `okonomi.hjemme.io`
- **Policy**: Allow, med din Google-e-post (samme mønster som server2.hjemme.io).

Viktig: **ikke** blokker stien `/api/callback`. Den treffes av nettleseren din
(som allerede er Google-innlogget) når banken sender deg tilbake, så standard
«hele domenet bak Access» fungerer fint – du trenger ingen unntak.

## 4. Enable Banking Redirect URL

Når du registrerer appen på https://enablebanking.com/cp, sett:
```
Redirect URL:  https://okonomi.hjemme.io/api/callback
```
(må være nøyaktig lik `APP_BASE_URL` + `/api/callback`).

## 5. Test

1. Åpne `https://okonomi.hjemme.io` → Google-pålogging (Cloudflare Access).
2. Dashbordet vises.
3. **Koble til bank** → BankID → du sendes tilbake til `/api/callback` → kontoene dukker opp.

## Bytter du domene senere?

Oppdater begge stedene, ellers feiler bank-returen:
```bash
pct exec <CTID> -- nano /opt/okonomi/.env      # APP_BASE_URL=https://nytt-domene
pct exec <CTID> -- systemctl restart okonomi
```
…og Redirect URL i Enable Banking-appen.
