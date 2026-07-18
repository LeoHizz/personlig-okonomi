# Personlig økonomi

Et selvhostet dashboard for å holde oversikt over privatøkonomien, koblet mot
egne bankkontoer via åpen bank-API. Bygget etter ditt eget design fra Claude Design.

**Bankdata-leverandør** (velges med `PROVIDER` i `.env`):
- **Enable Banking** (standard) – gratis for personlig bruk, god norsk dekning.
  Se [ENABLEBANKING_SETUP.md](ENABLEBANKING_SETUP.md).
- **GoCardless Bank Account Data** – kun for eksisterende kontoer (nye
  registreringer stengt siden juli 2025). Se [GOCARDLESS_SETUP.md](GOCARDLESS_SETUP.md).

Støtter norske banker – testet mot **Sparebanken Norge, DNB og Coop Mastercard
(kredittbanken)**. Banklisten hentes live, så alle støttede norske banker fungerer.
CSV-import fungerer uansett, uten noen leverandør.

---

## Hva vises hvor kommer fra

| Automatisk fra banken (åpen bank-API)     | Du fyller inn selv (Innstillinger)          |
|-------------------------------------------|---------------------------------------------|
| Kontoer og saldo                          | Budsjett per kategori                        |
| Transaksjoner (dato, beløp, mottaker)     | Boligverdi, fond og andre eiendeler          |
| Kategorisering (norske butikker)          | Lån (saldo, rente, avdrag)                   |
| Forbruk per kategori, cashflow, inn/ut    | Hvem som eier hver konto (Felles/navn)       |
| Sparerate, abonnementer                   | Sparemål                                     |

Banken gir **ikke** boligverdi, fondsavkastning, lånedetaljer eller budsjett –
derfor fyller du disse inn selv. Alt annet regnes ut fra ekte transaksjoner.

---

## Sider

- **Oversikt** – nøkkeltall, månedsoppsummering, forbruk per kategori (donut),
  cashflow, kontoer, lån og regnskap mot budsjett.
- **Budsjett og regnskap** – faktisk forbruk per måned per kategori, med knappen
  **Foreslå budsjett** (faste kategorier = siste kjente beløp, variable = snitt
  siste 12 måneder). Juster og lagre.
- **Transaksjoner** – søk, filtrer på person/kategori.

---

## Installasjon

### Alternativ A — Automatisk LXC i Proxmox (anbefalt)

Ett skript som oppretter LXC-containeren, installerer alt og starter tjenesten.

1. **Kopier prosjektmappa til Proxmox-verten** (én gang), fra Mac-en:
   ```bash
   scp -r "Personlig økonomi" root@PROXMOX-IP:/root/okonomi-src
   ```
2. **Kjør skriptet på Proxmox-verten** (SSH inn, som root):
   ```bash
   cd /root/okonomi-src
   bash proxmox-lxc-install.sh
   ```
   Skriptet laster ned en Debian-mal, lager en uprivilegert LXC, installerer
   Python + appen, setter opp en systemd-tjeneste (starter automatisk ved boot),
   og skriver ut adressen, f.eks. `http://192.168.1.51:8080`.

   Valgfritt kan du styre ressurser/nøkler med miljøvariabler:
   ```bash
   CTID=210 RAM=1024 DISK=6 \
   GOCARDLESS_SECRET_ID=xxx GOCARDLESS_SECRET_KEY=yyy \
   bash proxmox-lxc-install.sh
   ```
3. **Konfigurer Enable Banking** (skriptet genererer RSA-nøkkelen for deg og
   skriver ut den offentlige nøkkelen). Registrer appen gratis, lim inn
   Application ID og restart – se [ENABLEBANKING_SETUP.md](ENABLEBANKING_SETUP.md):
   ```bash
   pct exec <CTID> -- nano /opt/okonomi/.env      # sett ENABLEBANKING_APP_ID
   pct exec <CTID> -- systemctl restart okonomi
   ```
4. **Åpne dashboardet** på adressen skriptet skrev ut.

**Oppdatere til ny kode senere:** kopier ny prosjektmappe til verten og kjør
`CTID=<id> bash proxmox-lxc-update.sh` (beholder database og `.env`).

**Drift (fra Proxmox-verten):**
```bash
pct exec <CTID> -- systemctl status okonomi      # status
pct exec <CTID> -- journalctl -u okonomi -f       # logg
pct exec <CTID> -- systemctl restart okonomi      # restart
```
**Automatisk synk:** appen synker alle kontoer én gang i døgnet (kl. 05 som
standard). Styres i `.env` med `AUTO_SYNC=1` og `AUTO_SYNC_HOUR=5`. Du kan alltid
trykke **↻ Synk** manuelt i tillegg.

Backup: ta vare på `/opt/okonomi/data/` og `/opt/okonomi/.env` inne i containeren
(eller bruk Proxmox-backup av hele containeren).

---

### Alternativ B — Docker på en Ubuntu-VM/LXC

Hvis du heller vil bruke Docker:

1. Kopier mappa til serveren: `scp -r "Personlig økonomi" ubuntu@IP:/opt/okonomi`
2. På serveren: `cd /opt/okonomi && sudo bash install.sh`
   (installerer Docker, lager `.env`, bygger og starter)
3. Legg inn GoCardless-nøkler og `APP_BASE_URL` i `.env`, så `docker compose up -d`
4. Åpne `http://server-ip:8080`

---

## Bruk

1. **Koble til bank**: Klikk «Koble til bank» → velg banken din → logg inn i
   bankens egen løsning. Du gir lesetilgang i 90 dager (så må du fornye).
   Gjenta for hver bank (Sparebanken Norge, DNB, Coop Mastercard).
2. **Synkroniser**: Klikk «↻ Synk» for å hente nye transaksjoner.
3. **Importer historikk**: På budsjettsiden → «Importer CSV». Eksporter
   transaksjoner fra nettbanken (CSV) og last opp for å fylle inn tidligere år.
4. **Sett budsjett**: Budsjettsiden → «Foreslå budsjett» → juster → «Lagre».
5. **Fyll inn bolig/fond/lån**: Innstillinger → Eiendeler / Lån.
6. **Merk kontoer**: Innstillinger → Kontoer → sett visningsnavn, etikett (SPV/
   DNB/COOP) og eier.

### Viktig om ratebegrensning
GoCardless' gratisnivå tillater ca. **4 uttrekk per konto per døgn**. Appen lagrer
alt lokalt og henter derfor bare nytt hver 6. time (juster med
`SYNC_MIN_INTERVAL_HOURS`). Dashboardet leser alltid fra den lokale databasen.

---

## Drift

```bash
docker compose logs -f          # se logg
docker compose restart          # restart
docker compose up -d --build    # oppdater etter kodeendring
```

**Backup**: All data ligger i `data/okonomi.db` (SQLite). Ta backup av `data/`-mappa
og `.env`.

**Automatisk synk** (valgfritt): legg en cron-jobb på serveren:
```bash
# hver 6. time
0 */6 * * * curl -s -X POST http://localhost:8080/api/sync >/dev/null
```

---

## Sikkerhet
- Sett `APP_PASSWORD` i `.env` – da kreves innlogging for hele appen.
- Appen bør kun være tilgjengelig på ditt eget nettverk (ikke eksponer mot
  internett uten HTTPS og reverse proxy).
- API-nøklene ligger kun i `.env` på serveren, aldri i frontend.

---

## Teknisk
- **Backend**: Python + FastAPI, SQLite. Kode i `backend/app/`.
- **Frontend**: Vanilla JS/CSS i `frontend/` – reproduserer designet.
- **Kjøring**: Docker (`Dockerfile` + `docker-compose.yml`).

### Endre kategorisering
Kategoriregler ligger i `backend/app/categorize.py`. Du kan også legge til egne
regler via innstillinger (lagres i databasen).
