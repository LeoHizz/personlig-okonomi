# GoCardless Bank Account Data – oppsett

GoCardless Bank Account Data (tidligere Nordigen) gir **gratis** lesetilgang til
egne bankkontoer via PSD2/åpen bank-API. Du trenger en konto og et sett API-nøkler.

## 1. Opprett konto

1. Gå til **https://bankaccountdata.gocardless.com/**
2. Klikk **Sign up** / **Get started** og opprett en gratis konto.
   - Dette er «Bank Account Data»-produktet – ikke forveksle med GoCardless'
     betalingsprodukt (Payments). Du skal ikke sette opp innkreving.
3. Bekreft e-post og logg inn.

## 2. Hent API-nøkler (secret_id + secret_key)

1. Inne i portalen, gå til **Developers → User secrets** (eller **Secrets**).
2. Klikk **Create new secret**.
3. Gi den et navn, f.eks. «Personlig økonomi».
4. Du får nå to verdier:
   - **Secret ID**
   - **Secret Key**  ← vises kun **én gang**, kopier den med en gang!
5. Lim disse inn i `.env`-fila på serveren:
   ```
   GOCARDLESS_SECRET_ID=din-secret-id
   GOCARDLESS_SECRET_KEY=din-secret-key
   ```

> ⚠️ Secret Key vises bare ved opprettelse. Mister du den, lag en ny.

## 3. Ingen redirect-URL må registreres

For dette API-et trenger du **ikke** å forhåndsregistrere redirect-adresser.
Appen sender automatisk `APP_BASE_URL/api/callback` som retur-adresse når du
kobler til en bank. Pass bare på at `APP_BASE_URL` i `.env` er riktig (server-IP
eller domene), slik at banken kan sende deg tilbake til appen.

## 4. Koble til bankene

Når nøklene er på plass og appen er startet:

1. Åpne dashboardet → **Koble til bank**.
2. Velg banken (søk etter «Sparebanken», «DNB», «Coop» osv.).
3. Du sendes til bankens egen innlogging (BankID). Godkjenn lesetilgang.
4. Du sendes tilbake til dashboardet, og kontoene dukker opp.
5. Gjenta for hver bank.

### Bankene dine
- **Sparebanken Norge** – søk «Sparebanken». (Etter fusjonen kan den også hete
  Sparebanken Vest / SPV i listen – velg den som matcher din innlogging.)
- **DNB** – søk «DNB».
- **Coop Mastercard** – søk «Coop». Kredittkortet utstedes via en kredittbank;
  velg oppføringen som stemmer med kortinnloggingen din.

## 5. Godt å vite

- **Varighet**: Tilgangen varer i **90 dager**. Deretter må du koble til på nytt
  (samme knapp). Appen sier fra når en tilkobling må fornyes.
- **Ratebegrensning**: Ca. **4 uttrekk per konto per døgn** på gratisnivået.
  Appen henter derfor sjelden (hver 6. time) og lagrer alt lokalt.
- **Historikk**: Norske banker gir ofte bare 90 dager tilbake i tid. For eldre
  historikk (f.eks. hele fjoråret) – bruk **CSV-import** på budsjettsiden.
- **Personvern**: Alle data lagres kun lokalt i din egen database (`data/okonomi.db`).
  Ingenting sendes videre.
