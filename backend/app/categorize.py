"""Regelbasert kategorisering av transaksjoner, tilpasset norske forhold.

Kategoriene matcher fargepaletten i dashboardet. Rekkefølge = donut-rekkefølge.
Brukeren kan overstyre og legge til egne regler i innstillinger.
"""
from __future__ import annotations

# Kategori -> farge (samme palett som designet)
CATEGORY_COLORS: dict[str, str] = {
    "Bolig og lån": "#16324a",
    "Dagligvarer": "#2f7a5e",
    "Barn og forsikring": "#4a6a86",
    "Transport": "#5d8fb8",
    "Barn og fritid": "#8fb4a3",
    "Restaurant og kafé": "#a9c0b4",
    "Strøm": "#c2ccd6",
    "Abonnementer": "#7fa0bd",
    "Annet": "#e3e6ea",
    "Inntekt": "#2f7a5e",
    "Overføring": "#9aa0aa",
}

# Standard kategori-rekkefølge for visning (utgiftskategorier)
CATEGORY_ORDER = [
    "Bolig og lån",
    "Dagligvarer",
    "Barn og forsikring",
    "Transport",
    "Barn og fritid",
    "Restaurant og kafé",
    "Strøm",
    "Abonnementer",
    "Annet",
]

# Kategorier som regnes som "faste utgifter"
FIXED_CATEGORIES = {"Bolig og lån", "Barn og forsikring", "Strøm", "Abonnementer"}

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
    # Barn og forsikring
    ("barnehage", "Barn og forsikring"),
    ("sfo", "Barn og forsikring"),
    ("forsikring", "Barn og forsikring"),
    ("fremtind", "Barn og forsikring"),
    ("gjensidige", "Barn og forsikring"),
    ("if forsikring", "Barn og forsikring"),
    ("tryg", "Barn og forsikring"),
    ("storebrand", "Barn og forsikring"),
    ("frende", "Barn og forsikring"),
    # Barn og fritid
    ("fotball", "Barn og fritid"),
    ("håndball", "Barn og fritid"),
    ("svømme", "Barn og fritid"),
    ("idrettslag", "Barn og fritid"),
    ("kontingent", "Barn og fritid"),
    ("kino", "Barn og fritid"),
    ("leketøy", "Barn og fritid"),
    ("xxl", "Barn og fritid"),
    ("g-sport", "Barn og fritid"),
    ("gsport", "Barn og fritid"),
    # Bolig og lån
    ("boliglån", "Bolig og lån"),
    ("terminbeløp", "Bolig og lån"),
    ("husleie", "Bolig og lån"),
    ("kommunale avgifter", "Bolig og lån"),
    ("obos", "Bolig og lån"),
    ("borettslag", "Bolig og lån"),
    ("avdrag", "Bolig og lån"),
    # Annet
    ("apotek", "Annet"),
    ("vitusapotek", "Annet"),
    ("boots", "Annet"),
    ("vinmonopolet", "Annet"),
    ("h&m", "Annet"),
    ("zalando", "Annet"),
    ("clas ohlson", "Annet"),
    ("jernia", "Annet"),
    ("power", "Annet"),
    ("elkjøp", "Annet"),
    ("ikea", "Annet"),
    ("jysk", "Annet"),
    ("apple store", "Annet"),
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
