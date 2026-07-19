"""Regelbasert kategorisering av transaksjoner, tilpasset norske forhold.

Kategoriene matcher fargepaletten i dashboardet. Rekkefølge = donut-rekkefølge.
Brukeren kan overstyre og legge til egne regler i innstillinger.
"""
from __future__ import annotations

# Kategori -> farge (samme palett som designet)
CATEGORY_COLORS: dict[str, str] = {
    "Boliglån og husleie": "#16324a",
    "Kommunale avgifter": "#3d6b7a",
    "Dagligvarer": "#2f7a5e",
    "Barn": "#4a6a86",
    "Kjæledyr": "#9a8c5e",
    "Forsikring": "#8a7bb0",
    "Transport": "#5d8fb8",
    "Fritid": "#8fb4a3",
    "Restaurant og kafé": "#a9c0b4",
    "Helse": "#6ba3a0",
    "Klær og sko": "#c99a6a",
    "Reise og ferie": "#6d86c9",
    "Hus og hjem": "#9c8f7a",
    "Strøm": "#c2ccd6",
    "Abonnementer": "#7fa0bd",
    "Renter og gebyrer": "#b5546a",
    "Gaver og veldedighet": "#c98ab5",
    "Annet": "#e3e6ea",
    "Inntekt": "#2f7a5e",
    "Overføring": "#9aa0aa",
}

# Standard kategori-rekkefølge for visning (utgiftskategorier)
# Økes hver gang reglene/kategoriene endres, slik at eksisterende (ikke-manuelle)
# transaksjoner re-kategoriseres automatisk ved neste oppstart.
RULES_VERSION = 6

CATEGORY_ORDER = [
    "Boliglån og husleie",
    "Kommunale avgifter",
    "Dagligvarer",
    "Barn",
    "Kjæledyr",
    "Forsikring",
    "Transport",
    "Fritid",
    "Restaurant og kafé",
    "Helse",
    "Klær og sko",
    "Reise og ferie",
    "Hus og hjem",
    "Strøm",
    "Abonnementer",
    "Renter og gebyrer",
    "Gaver og veldedighet",
    "Annet",
]

# Kategorier som regnes som "faste utgifter"
FIXED_CATEGORIES = {"Boliglån og husleie", "Kommunale avgifter", "Barn", "Forsikring", "Strøm", "Abonnementer"}

# Nøkkelord -> kategori. Matches som delstreng, ufølsom for store/små bokstaver.
# Rekkefølgen betyr noe: første treff vinner.
DEFAULT_RULES: list[tuple[str, str]] = [
    # Inntekt
    ("lønn", "Inntekt"),
    ("lonn", "Inntekt"),
    ("salary", "Inntekt"),
    ("barnetrygd", "Inntekt"),
    ("nav ", "Inntekt"),
    ("skatteetaten", "Inntekt"),
    ("utbytte", "Inntekt"),
    ("refusjon", "Inntekt"),
    # Overføring / kortavregning (unngå dobbelttelling – står FØR Dagligvarer)
    ("kredittbanken", "Overføring"),
    ("coop mastercard", "Overføring"),
    ("coop kreditt", "Overføring"),
    ("nedbetaling kredittkort", "Overføring"),
    ("egen overføring", "Overføring"),
    ("overføring egen konto", "Overføring"),
    # Dagligvarer
    ("rema", "Dagligvarer"),
    ("kiwi", "Dagligvarer"),
    ("meny", "Dagligvarer"),
    ("coop", "Dagligvarer"),
    ("extra", "Dagligvarer"),
    ("obs ", "Dagligvarer"),
    ("bunnpris", "Dagligvarer"),
    ("spar ", "Dagligvarer"),
    ("joker", "Dagligvarer"),
    ("europris", "Dagligvarer"),
    ("oda", "Dagligvarer"),
    ("holdbart", "Dagligvarer"),
    ("normal", "Dagligvarer"),
    # Restaurant og kafé
    ("foodora", "Restaurant og kafé"),
    ("wolt", "Restaurant og kafé"),
    ("egon", "Restaurant og kafé"),
    ("mcdonald", "Restaurant og kafé"),
    ("burger king", "Restaurant og kafé"),
    ("peppes", "Restaurant og kafé"),
    ("dolly dimple", "Restaurant og kafé"),
    ("espresso", "Restaurant og kafé"),
    ("kaffebrenneri", "Restaurant og kafé"),
    ("cafe", "Restaurant og kafé"),
    ("kafe", "Restaurant og kafé"),
    ("restaurant", "Restaurant og kafé"),
    ("bar ", "Restaurant og kafé"),
    ("sushi", "Restaurant og kafé"),
    ("pizza", "Restaurant og kafé"),
    # Transport
    ("circle k", "Transport"),
    ("circlek", "Transport"),
    ("esso", "Transport"),
    ("shell", "Transport"),
    ("uno-x", "Transport"),
    ("uno x", "Transport"),
    ("st1", "Transport"),
    ("ferde", "Transport"),
    ("bom", "Transport"),
    ("autopass", "Transport"),
    ("ruter", "Transport"),
    ("skyss", "Transport"),
    ("vy ", "Transport"),
    ("flytoget", "Transport"),
    ("atb", "Transport"),
    ("kolumbus", "Transport"),
    ("bolt", "Transport"),
    ("uber", "Transport"),
    ("taxi", "Transport"),
    ("parkering", "Transport"),
    ("easypark", "Transport"),
    ("parkeringsselskap", "Transport"),
    # Strøm og nettleie
    ("fjordkraft", "Strøm"),
    ("tibber", "Strøm"),
    ("fortum", "Strøm"),
    ("hafslund", "Strøm"),
    ("nettleie", "Strøm"),
    ("bkk", "Strøm"),
    ("elvia", "Strøm"),
    ("lnett", "Strøm"),
    ("glitre", "Strøm"),
    ("agva", "Strøm"),
    ("strøm", "Strøm"),
    # Abonnementer
    ("netflix", "Abonnementer"),
    ("spotify", "Abonnementer"),
    ("hbo", "Abonnementer"),
    ("max.com", "Abonnementer"),
    ("viaplay", "Abonnementer"),
    ("disney", "Abonnementer"),
    ("tv 2", "Abonnementer"),
    ("tv2", "Abonnementer"),
    ("strive", "Abonnementer"),
    ("icloud", "Abonnementer"),
    ("google one", "Abonnementer"),
    ("google storage", "Abonnementer"),
    ("apple.com/bill", "Abonnementer"),
    ("youtube", "Abonnementer"),
    ("amazon prime", "Abonnementer"),
    ("audible", "Abonnementer"),
    ("storytel", "Abonnementer"),
    ("schibsted", "Abonnementer"),
    ("aviser", "Abonnementer"),
    ("dagbladet", "Abonnementer"),
    ("vg+", "Abonnementer"),
    ("treningssenter", "Abonnementer"),
    ("sats", "Abonnementer"),
    ("elixia", "Abonnementer"),
    ("chatgpt", "Abonnementer"),
    ("openai", "Abonnementer"),
    ("anthropic", "Abonnementer"),
    ("claude", "Abonnementer"),
    # Barn (barnehage/SFO)
    ("barnehage", "Barn"),
    ("sfo", "Barn"),
    # Forsikring
    ("forsikring", "Forsikring"),
    ("fremtind", "Forsikring"),
    ("gjensidige", "Forsikring"),
    ("if forsikring", "Forsikring"),
    ("tryg", "Forsikring"),
    ("storebrand", "Forsikring"),
    ("frende", "Forsikring"),
    ("eika forsikring", "Forsikring"),
    # Fritid (aktiviteter/sport)
    ("fotball", "Fritid"),
    ("håndball", "Fritid"),
    ("svømme", "Fritid"),
    ("idrettslag", "Fritid"),
    ("kontingent", "Fritid"),
    ("kino", "Fritid"),
    ("leketøy", "Fritid"),
    ("xxl", "Fritid"),
    ("g-sport", "Fritid"),
    ("gsport", "Fritid"),
    # Boliglån og husleie
    ("boliglån", "Boliglån og husleie"),
    ("terminbeløp", "Boliglån og husleie"),
    ("husleie", "Boliglån og husleie"),
    ("obos", "Boliglån og husleie"),
    ("borettslag", "Boliglån og husleie"),
    ("avdrag", "Boliglån og husleie"),
    # Kommunale avgifter
    ("kommunale avgifter", "Kommunale avgifter"),
    ("kommunale gebyr", "Kommunale avgifter"),
    ("renovasjon", "Kommunale avgifter"),
    ("vann og avløp", "Kommunale avgifter"),
    ("vann og avlop", "Kommunale avgifter"),
    ("avløpsgebyr", "Kommunale avgifter"),
    ("feieavgift", "Kommunale avgifter"),
    ("feiing", "Kommunale avgifter"),
    ("eiendomsskatt", "Kommunale avgifter"),
    ("kommune", "Kommunale avgifter"),
    # Helse
    ("apotek", "Helse"),
    ("vitusapotek", "Helse"),
    ("boots", "Helse"),
    ("farmasi", "Helse"),
    ("tannlege", "Helse"),
    ("legevakt", "Helse"),
    ("legekontor", "Helse"),
    ("fysioterap", "Helse"),
    ("optiker", "Helse"),
    ("brilleland", "Helse"),
    ("synsam", "Helse"),
    ("specsavers", "Helse"),
    # Klær og sko
    ("h&m", "Klær og sko"),
    ("h & m", "Klær og sko"),
    ("zalando", "Klær og sko"),
    ("boozt", "Klær og sko"),
    ("cubus", "Klær og sko"),
    ("dressmann", "Klær og sko"),
    ("bik bok", "Klær og sko"),
    ("vero moda", "Klær og sko"),
    ("jack & jones", "Klær og sko"),
    ("zara", "Klær og sko"),
    ("nike", "Klær og sko"),
    ("skoringen", "Klær og sko"),
    ("eurosko", "Klær og sko"),
    ("vionette", "Klær og sko"),
    # Reise og ferie
    ("scandinavian airlines", "Reise og ferie"),
    ("norwegian air", "Reise og ferie"),
    ("widerøe", "Reise og ferie"),
    ("wideroe", "Reise og ferie"),
    ("flytoget", "Reise og ferie"),
    ("booking.com", "Reise og ferie"),
    ("hotell", "Reise og ferie"),
    ("airbnb", "Reise og ferie"),
    ("hurtigruten", "Reise og ferie"),
    ("expedia", "Reise og ferie"),
    ("finnair", "Reise og ferie"),
    ("color line", "Reise og ferie"),
    ("fjord line", "Reise og ferie"),
    # Hus og hjem
    ("clas ohlson", "Hus og hjem"),
    ("jernia", "Hus og hjem"),
    ("ikea", "Hus og hjem"),
    ("jysk", "Hus og hjem"),
    ("power", "Hus og hjem"),
    ("elkjøp", "Hus og hjem"),
    ("elkjop", "Hus og hjem"),
    ("biltema", "Hus og hjem"),
    ("jula", "Hus og hjem"),
    ("maxbo", "Hus og hjem"),
    ("montér", "Hus og hjem"),
    ("byggmakker", "Hus og hjem"),
    ("plantasjen", "Hus og hjem"),
    ("kid interiør", "Hus og hjem"),
    ("princess", "Hus og hjem"),
    ("apple store", "Hus og hjem"),
    # Renter og gebyrer (tullekostnader)
    ("gebyr", "Renter og gebyrer"),
    ("purregebyr", "Renter og gebyrer"),
    ("purring", "Renter og gebyrer"),
    ("forsinkelsesrente", "Renter og gebyrer"),
    ("overtrekksrente", "Renter og gebyrer"),
    ("kredittrente", "Renter og gebyrer"),
    ("omkostning", "Renter og gebyrer"),
    ("årsavgift", "Renter og gebyrer"),
    ("kortavgift", "Renter og gebyrer"),
    ("termingebyr", "Renter og gebyrer"),
    ("fakturagebyr", "Renter og gebyrer"),
    ("inkasso", "Renter og gebyrer"),
    ("varselgebyr", "Renter og gebyrer"),
    ("minibankgebyr", "Renter og gebyrer"),
    # Kjæledyr
    ("veterinær", "Kjæledyr"),
    ("veterinar", "Kjæledyr"),
    ("dyrlege", "Kjæledyr"),
    ("dyreklinikk", "Kjæledyr"),
    ("smådyrklinikk", "Kjæledyr"),
    ("smadyrklinikk", "Kjæledyr"),
    ("dyrebutikk", "Kjæledyr"),
    ("dyrehjørnet", "Kjæledyr"),
    ("dyrehjornet", "Kjæledyr"),
    ("dyrekassen", "Kjæledyr"),
    ("musti", "Kjæledyr"),
    ("mirri", "Kjæledyr"),
    ("zoobutikk", "Kjæledyr"),
    ("kjæledyr", "Kjæledyr"),
    ("kjaledyr", "Kjæledyr"),
    ("hundemat", "Kjæledyr"),
    ("kattemat", "Kjæledyr"),
    ("vom og hundemat", "Kjæledyr"),
    # Gaver og veldedighet
    ("røde kors", "Gaver og veldedighet"),
    ("rode kors", "Gaver og veldedighet"),
    ("redd barna", "Gaver og veldedighet"),
    ("kreftforeningen", "Gaver og veldedighet"),
    ("leger uten grenser", "Gaver og veldedighet"),
    ("unicef", "Gaver og veldedighet"),
    ("plan norge", "Gaver og veldedighet"),
    ("plan international", "Gaver og veldedighet"),
    ("kirkens nødhjelp", "Gaver og veldedighet"),
    ("kirkens nodhjelp", "Gaver og veldedighet"),
    ("frelsesarmeen", "Gaver og veldedighet"),
    ("sos-barnebyer", "Gaver og veldedighet"),
    ("sos barnebyer", "Gaver og veldedighet"),
    ("norsk folkehjelp", "Gaver og veldedighet"),
    ("flyktninghjelpen", "Gaver og veldedighet"),
    ("regnskogfondet", "Gaver og veldedighet"),
    ("blindeforbundet", "Gaver og veldedighet"),
    ("dyrebeskyttelsen", "Gaver og veldedighet"),
    ("amnesty", "Gaver og veldedighet"),
    ("strømmestiftelsen", "Gaver og veldedighet"),
    ("strommestiftelsen", "Gaver og veldedighet"),
    ("interflora", "Gaver og veldedighet"),
    ("gavekort", "Gaver og veldedighet"),
    # Annet
    ("vinmonopolet", "Annet"),
    # Overføringer mellom egne kontoer / Vipps
    ("overføring", "Overføring"),
    ("egen overføring", "Overføring"),
]


def _rules() -> list[tuple[str, str]]:
    """Kombiner brukerregler (fra DB) med standardreglene. Brukerregler først."""
    from . import db

    user = db.get_setting("category_rules", []) or []
    custom = [(r["pattern"].lower(), r["category"]) for r in user if r.get("pattern")]
    return custom + DEFAULT_RULES


def learn_rule(counterparty: str | None, category: str) -> None:
    """Lag/oppdater en brukerregel ut fra et motpartsnavn, så samme sted
    kjennes igjen automatisk neste gang."""
    from . import db

    pattern = " ".join((counterparty or "").split()).lower()
    if not pattern:
        return
    rules = db.get_setting("category_rules", []) or []
    for r in rules:
        if (r.get("pattern") or "").strip().lower() == pattern:
            r["category"] = category
            break
    else:
        rules.insert(0, {"pattern": pattern, "category": category})
    db.set_setting("category_rules", rules)


def apply_pattern_to_existing(pattern: str, category: str) -> int:
    """Sett kategori på alle ikke-manuelle linjer som matcher ett mønster.
    Brukes når man retter én kategori – påvirker bare samme sted."""
    from . import db

    pat = " ".join((pattern or "").split()).lower()
    if not pat:
        return 0
    rows = db.query("SELECT id, counterparty, remittance, category_source FROM transactions")
    changed = 0
    for r in rows:
        if r["category_source"] == "manual":
            continue
        text = " ".join(f"{r['counterparty'] or ''} {r['remittance'] or ''}".split()).lower()
        if pat in text:
            db.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, r["id"]))
            changed += 1
    return changed


def apply_rules_to_existing() -> int:
    """Kjør reglene på nytt over alle transaksjoner som ikke er manuelt satt.
    Returnerer antall som endret kategori."""
    from . import db

    rows = db.query(
        "SELECT id, counterparty, remittance, amount, category, category_source FROM transactions"
    )
    changed = 0
    for r in rows:
        if r["category_source"] == "manual":
            continue
        newcat = categorize(r["counterparty"], r["remittance"], r["amount"])
        if newcat != r["category"]:
            db.execute("UPDATE transactions SET category = ? WHERE id = ?", (newcat, r["id"]))
            changed += 1
    return changed


def categorize(counterparty: str | None, remittance: str | None, amount: float) -> str:
    text = f"{counterparty or ''} {remittance or ''}".lower()
    for pattern, category in _rules():
        if pattern in text:
            # Positive beløp som traff en utgiftsregel er som regel refusjon/retur;
            # men hvis regelen selv er Inntekt/Overføring, behold den.
            return category
    # Fallback ut fra fortegn
    if amount > 0:
        return "Inntekt"
    return "Annet"
