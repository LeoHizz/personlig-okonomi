# Enable Banking – oppsett

Enable Banking gir **gratis** lesetilgang til egne bankkontoer (personlig bruk) og
har god dekning av norske banker. Dette er standard-leverandøren i appen etter at
GoCardless stengte nye registreringer (juli 2025).

Autentisering skjer med et **RSA-nøkkelpar**: appen signerer forespørsler med en
privat nøkkel, og du registrerer den offentlige nøkkelen hos Enable Banking.

## 1. Nøkkelen er allerede laget

Kjørte du `proxmox-lxc-install.sh`, er nøkkelparet allerede generert inne i
containeren:
- Privat: `/opt/okonomi/data/enablebanking_private.pem` (blir værende – del den aldri)
- Offentlig: `/opt/okonomi/data/enablebanking_public.pem` (denne laster du opp)

Skriptet skrev også ut den offentlige nøkkelen på slutten. Trenger du den igjen:
```bash
pct exec <CTID> -- cat /opt/okonomi/data/enablebanking_public.pem
```

> Kjører du Docker/manuelt i stedet, lag nøkkelen selv:
> ```bash
> openssl genrsa -out data/enablebanking_private.pem 4096
> openssl rsa -in data/enablebanking_private.pem -pubout -out data/enablebanking_public.pem
> ```

## 2. Opprett app hos Enable Banking

1. Gå til **https://enablebanking.com/cp** og opprett en (gratis) konto.
2. Opprett en ny **applikasjon**:
   - **Environment**: Production (for ekte bankkontoer)
   - **Redirect URL**: `http://DIN-SERVER-IP:8080/api/callback`
     (samme som `APP_BASE_URL` i `.env` + `/api/callback`)
   - **Public key**: lim inn / last opp innholdet i `enablebanking_public.pem`
3. Godta vilkårene for personlig bruk.
4. Kopier **Application ID** som appen får.

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
