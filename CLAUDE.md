# CLAUDE.md — Personlig økonomi

Startpunkt for enhver ny sesjon. Les denne først, deretter `git log --oneline -20`
og `README.md`. Skriv på **norsk** til brukeren (Frode).

## Hva dette er
Selvhostet dashboard for privatøkonomi. Henter bankdata via åpen bank-API
(Enable Banking som standard, GoCardless som alternativ), kategoriserer norske
transaksjoner, og viser forbruk, cashflow, budsjett, likviditet og formue.
Kjører hjemme på Frodes Proxmox-server (LXC eller Docker). Bygget etter Frodes
eget design fra Claude Design (`_design/`, ikke i git).

## Teknisk stack
- **Backend**: Python 3.12 + FastAPI + SQLite. All kode i `backend/app/`.
- **Frontend**: Vanilla JS/CSS/HTML i `frontend/` (ingen byggesteg, ingen rammeverk).
- **Kjøring**: Docker (`Dockerfile` + `docker-compose.yml`) eller systemd i LXC.
- Avhengigheter: fastapi, uvicorn, httpx, python-dotenv, pyjwt[crypto]. Ikke noe mer.

## Kjøre lokalt (for utvikling på Mac)
```bash
cd backend
pip install -r requirements.txt      # første gang (helst i .venv)
uvicorn app.main:app --reload --app-dir backend --port 8080
# åpne http://localhost:8080
```
Uten `.env`/bankoppsett: bruk **demo-modus** (POST `/api/demo`, eller knapp i UI)
for realistiske testdata. Databasen er `data/okonomi.db` (SQLite, gitignored).

## Kodekart (backend/app/)
| Fil | Ansvar |
|-----|--------|
| `main.py` | FastAPI-app, alle API-ruter, servering av frontend, auto-synk-scheduler |
| `aggregate.py` | **Størst.** All utregning: dashboard, budsjett, analyse, cashflow, likviditet, butikk-historikk |
| `categorize.py` | Kategoriregler for norske butikker/mottakere + brukerregler |
| `db.py` | SQLite-skjema og spørringer |
| `sync.py` | Henter/oppdaterer transaksjoner fra leverandør, dedupe |
| `enablebanking.py` / `gocardless.py` | Leverandør-integrasjoner (JWT, kontoer, transaksjoner) |
| `provider.py` | Velger aktiv leverandør ut fra `PROVIDER` i `.env` |
| `importer.py` | CSV-import fra nettbank |
| `demo.py` | Genererer demo-data |
| `labels.py` | Merkelapper (eier/konto-etiketter, f.eks. Felles/DNB/COOP/Jobb) |
| `config.py` | Miljøvariabler / innstillinger |

Frontend: `app.js` (all logikk, ~1200 linjer), `styles.css`, `index.html` (shell).

## Datamodell — viktig skille
- **Fra banken (automatisk)**: kontoer, saldo, transaksjoner, kategorisering,
  forbruk, cashflow, sparerate, abonnementer.
- **Brukeren fyller inn selv (Innstillinger)**: budsjett per kategori, boligverdi,
  fond/eiendeler, lån (saldo/rente/avdrag, amortiseres), hvem som eier hver konto,
  sparemål. Banken gir IKKE disse.

## Konvensjoner
- Commit-meldinger på **norsk**, imperativ, kort og konkret (se git-historikken).
- Commit ofte — hver gang noe fungerer. Git er prosjektets hukommelse mellom sesjoner.
- Ingen nye tunge avhengigheter uten grunn — hold appen lett og selvinstallerbar.
- Frontend er bevisst rammeverksfri. Ikke innfør React/build-steg uten at Frode vil det.
- All brukervendt tekst i appen er på norsk.
- Ikke commit `.env`, `data/`, `_design/`, `*.zip` (allerede i `.gitignore`).
- **Kjør ALLTID `/code-review` på ny/endret kode av noe omfang, og rett de reelle
  funnene før du går videre.** (Etablert juli 2026 – har allerede fanget ekte bugs
  i egen kode.) Merk påstander «verifisert» vs. «hypotese»; verifiser før du
  konkluderer, særlig før du skylder på tredjepart (bank/API).
- **Bank-API: aldri treffe Enable Banking live (test-kall, ekstra synk, backfill)
  uten å varsle Frode og få klart ja FØRST.** Rene DB-spørringer/kodeendringer
  trenger ikke varsel.
- **Arkitektur: kilde før tolkning.** Rådata fra banken lagres urørt i
  `raw_transactions` (append-only, innholds-hash). Alt appen viser deriveres FRA
  det (`sync.rebuild_from_raw`, ingen API-kall). `sync_runs` logger hvert
  synk-forsøk (ok/feil) – ikke svelg feil.

## Deploy (Frodes server)
- **LXC (anbefalt)**: `proxmox-lxc-install.sh` (ny), `proxmox-lxc-update.sh`
  (oppdater, beholder db+.env). Systemd-tjeneste `okonomi`, auto-synk kl. 05.
- **Docker**: `install.sh` → `docker compose up -d --build`.
- Se `README.md`, `ENABLEBANKING_SETUP.md`, `GOCARDLESS_SETUP.md`, `REMOTE_ACCESS.md`.

## Status og neste fase
Appen er komplett og i drift. Siste arbeid: ryddigere lån-skjema med amortisering,
konto-dedupe/identifisering, fanging av mer transaksjonsinfo (rå-svar lagres).

**Neste fase: forbedringer på appen.** (Konkrete oppgaver avklares med Frode ved
sesjonsstart — oppdater dette avsnittet når retning er satt.)
