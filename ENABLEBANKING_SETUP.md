# Enable Banking – oppsett

Enable Banking gir **gratis** lesetilgang til egne bankkontoer (personlig bruk) og
har god dekning av norske banker. Dette er standard-leverandøren i appen etter at
GoCardless stengte nye registreringer (juli 2025).

Autentisering skjer med et **RSA-nøkkelpar**: appen signerer forespørsler med en
privat nøkkel, og du registrerer den offentlige nøkkelen hos Enable Banking.

## 1. Nøkkel og sertifikat er allerede laget

Kjørte du `proxmox-lxc-install.sh`, er dette allerede generert inne i containeren:
- Privat nøkkel: `/opt/okonomi/data/enablebanking_private.pem` (blir værende – del den aldri)
- Sertifikat: `/opt/okonomi/data/enablebanking_cert.pem` (dette laster du opp til Enable Banking)

Skriptet skrev også ut sertifikatet på slutten. Trenger du det igjen:
```bash
pct exec <CTID> -- cat /opt/okonomi/data/enablebanking_cert.pem
```

> Mangler sertifikatet (eldre installasjon), lag det fra den private nøkkelen:
> ```bash
> pct exec <CTID> -- openssl req -new -x509 -days 3650 \
>   -key /opt/okonomi/data/enablebanking_private.pem \
>   -out /opt/okonomi/data/enablebanking_cert.pem -subj "/CN=personlig-okonomi"
> ```
> (Docker/manuelt: samme kommando, men uten `pct exec <CTID> --` og med sti `data/...`.)

## 2. Opprett app hos Enable Banking

1. Gå til **https://enablebanking.com/cp** og opprett en (gratis) konto.
2. **Add a new application**:
   - **Environment**: **Production** (Sandbox = kun falske testbanker)
   - **Nøkkel**: velg **«Generate outside the browser and import public certificate»**
     og lim inn innholdet i `enablebanking_cert.pem` (hele `BEGIN/END CERTIFICATE`-blokken)
   - **Application name**: valgfritt, f.eks. `Personlig okonomi`
   - **Allowed redirect URLs**: `https://DITT-DOMENE/api/callback`
     (nøyaktig lik `APP_BASE_URL` i `.env` + `/api/callback`)
3. Godta vilkårene for personlig bruk (følg ev. verifisering for Production).
4. Trykk **Register** og kopier **Application ID**.

## 3. Legg inn Application ID

```bash
pct exec <CTID> -- nano /opt/okonomi/.env
#   sett:  ENABLEBANKING_APP_ID=<din-app-id>
pct exec <CTID> -- systemctl restart okonomi
```

## 4. Koble til bankene

1. Åpne dashboardet → **Koble til bank**.
2. Velg banken (Sparebanken Norge, DNB, Coop Mastercard …).
3. Logg inn med BankID og godkjenn lesetilgang.
4. Du sendes tilbake, og kontoene dukker opp. Gjenta per bank.

## Godt å vite

- **Varighet**: samtykket varer typisk 90 dager, deretter fornyer du (samme knapp).
- **Redirect URL må stemme**: hvis du bytter server-IP/domene, oppdater både
  `APP_BASE_URL` i `.env` og Redirect URL i Enable Banking-appen.
- **Personvern**: alle data lagres kun lokalt (`data/okonomi.db`). Den private
  nøkkelen forlater aldri serveren.
- **Fallback**: CSV-import på budsjettsiden fungerer uansett, uavhengig av dette.
