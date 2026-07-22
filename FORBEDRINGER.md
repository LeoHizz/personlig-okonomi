# Forbedringer — idébank

Løpende liste over forbedringer Frode ønsker på økonomidashboardet. Ideer legges
inn her etter hvert som de dukker opp. Vi tar en **samlet vurdering** før vi kjører
en oppdatering — ikke plukk enkeltsaker uten avklaring.

**Arbeidsflyt:** Frode skriver inn ideen i chat → jeg legger den til under «Innkommende»
med dato. Når vi skal planlegge en runde, sorterer vi, prioriterer og flytter til
«Planlagt» / «Ferdig».

Status-koder: 🆕 ny · 🔎 må avklares · ✅ ferdig · ⏸️ utsatt · ❌ forkastet

---

## Innkommende (uvurdert)

### 1. Vis kontoeier i butikk-detaljvisning 🆕
_(2026-07-22)_
I butikk-oversikten («Siste kjøp», f.eks. ADYEN N.V.) vises kun kontonavn
(Sparekonto / Brukskonto), ikke hvem kontoen tilhører. Legg til kontoeier /
etikett (samme eier-label som brukes ellers, f.eks. Felles/DNB/Jobb) også her,
slik at hver transaksjonslinje viser både konto og eier.

### 2. KI-oppsummering: avvik, feil/mangler og handlingsforslag 🆕
_(2026-07-22)_
KI-oppsummeringen skal ikke bare beskrive tallene, men aktivt peke på potensielle
feil/mangler og foreslå konkrete aksjoner når den ser noe. Eks. (kun til
inspirasjon): unaturlig høye rentekostnader sammenlignet med normalen → tips om å
redusere. Altså: flagg avvik/utliggere, mulige datafeil (feilkategorisering,
manglende poster), og gi handlingsrettede råd der det er relevant.
🔎 Avklares: hva sammenlignes mot (egen historikk / typiske andeler)? Hvor konservativ
skal den være for å unngå «støy»/falske alarmer?

### 3. Kategoriregler bør kunne betinges på konto 🆕
_(2026-07-22)_
Brukerreglene (Mønster → Kategori) matcher i dag kun på tekst/butikknavn. De bør
kunne ta med hvilken konto transaksjonen faktisk gjelder som en faktor, slik at
samme mønster kan gi ulik kategori avhengig av konto (eller at en regel kun gjelder
én bestemt konto). Eks: en overføring til et navn kan bety noe annet fra brukskonto
enn fra sparekonto.
🔎 Avklares: konto som *valgfritt* tilleggsfelt (tom = gjelder alle, som i dag), så
eksisterende regler ikke brytes. Match på konto-eier/etikett eller konkret konto?

### 4. Refusjon av utlegg (Vipps-tilbakebetaling) feilklassifiseres som inntekt 🔎
_(2026-07-22)_
Scenario: Frode legger ut 1000 kr for en middag (→ forbruk «Restaurant»). Vennene
vippser tilbake 200 kr hver. I dag kommer disse inn som **inntekt** → blåser opp både
inntekt og sparerate, samtidig som restaurant-kategorien fortsatt viser fulle 1000 kr.
Dobbel feil. Reell kostnad var 200 kr.

Tre modelleringsvalg (til diskusjon om ambisjonsnivå):
- **A) Inntekt** — dagens oppførsel. Feil (overvurderer inntekt + forbruk).
- **B) Overføring** — nøytralt, holdes utenfor inntekt/forbruk (som lån). Bedre, men
  restaurant-kategorien står fortsatt på 1000 (forbruk overvurdert).
- **C) Refusjon/utlegg tilbakebetalt** — motposteres mot opprinnelig kategori, så
  netto-kostnad blir 200. Eneste som gir riktig inntekt, forbruk *og* sparerate.
  Krever ny «refusjon»-håndtering (og evt. kobling refusjon ↔ opprinnelig utlegg).
🔎 Avklares: hvor sofistikert (B som quick win vs. C som mål)? Hvordan skille
Vipps-refusjon fra ekte inntekt (mønster/regel/beløp-match mot nylig utlegg)?
Relaterer til #3 (kontobetingede regler) og lån=overføring-modellen.

### 5. Proaktiv varsling om bank-tilkoblingens helse 🆕
_(2026-07-22)_
I dag oppdager man at en bank har sluttet å levere data først når tallene stopper
(f.eks. SPV som ga 400 i 2 døgn uten at noe sa fra på forsiden). Appen bør varsle
proaktivt:
- **Samtykke-alder / re-auth:** vis «SPV-samtykket bør fornyes om X dager» beregnet
  fra `requisitions.created_at + 90 dager` (PSD2 krever ny BankID ~hver 90. dag).
- **Stille stopp:** flagg tydelig på dashboardet når en konto/bank ikke har hatt en
  vellykket synk på N dager (bruk `sync_runs` + `last_synced`), ikke bare på
  kontoinnstillinger. Skill mellom ratebegrensning (forbigående) og samtykke/annet
  (krever handling).
🔎 Avklares: terskel for «stille stopp»-varsel (2–3 dager?). Egen «bank-helse»-widget
vs. banner. Relaterer til den ærlige synk-rapporteringen som nettopp ble lagt inn.

### 6. Bruk bankens EKSAKTE rente/avdrag-splitt når den finnes 🆕
_(2026-07-22)_
Lånerenter beregnes i dag via amortiseringsestimat (startsaldo + rente + terminbeløp).
Men banken oppgir ofte den nøyaktige splitten i selve transaksjonsteksten, f.eks. fra
CSV-eksporten for boliglånet (−24 036):
«Avdrag: kr 6.000,00  Renter: kr 17.967,00  Terminomkostninger: kr 69,00».
Parse denne når den finnes → eksakt rente/avdrag per måned i stedet for estimat, og
terminomkostninger som eget lite gebyr. Mer presist enn amortiseringen, og selvkorrigerende
ved flytende rente.
🔎 Avklares: leverer Enable Banking-API-et denne detaljen i `remittance`, eller bare
CSV-eksporten? (Via API så vi kun «Lån · 36» — altså kortere.) Hvis kun CSV: gjelder
forslaget CSV-importerte lån; ellers undersøk om API-remittance har mer. Fallback til
amortiseringsestimatet når teksten mangler. Relaterer til lån=overføring-modellen og
rente/avdrag-grafen som nettopp ble lagt inn.

---

## Planlagt (neste runde)

_(fylles når vi prioriterer)_

---

## Ferdig

_(flyttes hit når implementert + committet)_

---

## Forkastet / utsatt

_(med kort begrunnelse)_
