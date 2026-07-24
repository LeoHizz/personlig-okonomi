"""Bygger dataene dashboardet viser, ut fra transaksjoner i databasen +
manuelle verdier (budsjett, boligverdi, lån) som brukeren setter i innstillinger.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from . import categorize, db, labels as labelmod

# Kategorier som ikke er "forbruk" i donut/oversikt
NON_EXPENSE = {"Inntekt", "Overføring"}


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def _month_label(month: str) -> str:
    names = [
        "januar", "februar", "mars", "april", "mai", "juni",
        "juli", "august", "september", "oktober", "november", "desember",
    ]
    y, m = month.split("-")
    return f"{names[int(m) - 1].capitalize()} {y}"


def _prev_months(month: str, count: int) -> list[str]:
    y, m = map(int, month.split("-"))
    out = []
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def _parse_rate(s) -> float:
    """«4,73» / «4.73%» -> 0.0473 (årlig rente som desimal)."""
    try:
        return float(str(s).replace(",", ".").strip().rstrip("%").strip()) / 100.0
    except (ValueError, TypeError):
        return 0.0


def _months_between(a: str, b: str) -> int:
    """Antall måneder fra a til b (begge 'YYYY-MM'). Negativ hvis b er før a."""
    try:
        ay, am = (int(x) for x in a.split("-")[:2])
        by, bm = (int(x) for x in b.split("-")[:2])
    except (ValueError, AttributeError, TypeError):
        return 0
    return (by - ay) * 12 + (bm - am)


def _fmt(n: float) -> str:
    """Norsk tallformat: mellomrom som tusenskille, ingen desimaler."""
    return f"{round(n):,}".replace(",", " ")


def account_current_balance(account_id: str) -> float:
    rows = db.query(
        "SELECT balance_type, amount FROM balances WHERE account_id = ?", (account_id,)
    )
    if not rows:
        return 0.0
    by_type = {r["balance_type"]: r["amount"] for r in rows}
    # Kredittkort: vis UTESTÅENDE (bokført/closing), ikke ledig kreditt (available).
    # Vanlige kontoer: disponibelt (available) stemmer med nettbanken – bokført kan
    # avvike fordi reserverte/ikke-bokførte poster ikke er trukket fra ennå.
    # 'manual' brukes for kontoer uten bank-saldo (f.eks. Coop-kort via CSV).
    acc = db.query("SELECT is_credit FROM accounts WHERE id = ?", (account_id,))
    is_credit = bool(acc and acc[0]["is_credit"])
    if is_credit:
        order = ("closing", "expected", "manual", "available", "opening", "other")
    else:
        order = ("available", "closing", "expected", "opening", "manual", "other")
    for pref in order:
        if pref in by_type:
            return by_type[pref]
    return rows[0]["amount"]


def _norm_persons(persons) -> list[str]:
    """Godta liste eller kommaseparert streng; fjern tomme og 'Alle'."""
    if not persons:
        return []
    if isinstance(persons, str):
        persons = persons.split(",")
    return [p.strip() for p in persons if p and p.strip() and p.strip() != "Alle"]


def _month_transactions(month: str, persons=None) -> list[dict]:
    persons = _norm_persons(persons)
    sql = (
        "SELECT t.*, a.owner AS owner, a.bank_code AS bank_code, a.name AS acct_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND substr(t.booking_date,1,7) = ?"
    )
    params: list = [month]
    if persons:
        sql += " AND a.owner IN (" + ",".join("?" for _ in persons) + ")"
        params += persons
    sql += " ORDER BY t.booking_date DESC"
    return [dict(r) for r in db.query(sql, params)]


def _persons_list() -> list[str]:
    # «Felles» eier ofte lån/verdier uten egen bankkonto – ta med eiere fra manuelle
    # poster også, så «Felles» alltid er valgbar som person.
    owners = set()
    for r in db.query(
        "SELECT DISTINCT owner FROM accounts "
        "WHERE owner IS NOT NULL AND owner != '' AND hidden = 0"
    ):
        owners.add(r["owner"])
    for x in (db.get_setting("manual_assets", []) or []) + (db.get_setting("manual_liabilities", []) or []):
        o = (x.get("owner") or "Felles").strip()
        if o:
            owners.add(o)
    return ["Alle"] + sorted(owners)


def _income_expense(txs: list[dict]) -> tuple[float, float]:
    # Inntekt = alle positive beløp unntatt interne overføringer.
    # Forbruk = alle negative beløp unntatt interne overføringer.
    income = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] != "Overføring")
    expense = sum(-t["amount"] for t in txs if t["amount"] < 0 and t["category"] != "Overføring")
    return income, expense


def _months_range(start: str, count: int) -> list[str]:
    """Liste med `count` måneder fra og med `start` (YYYY-MM)."""
    try:
        y, m = int(start[:4]), int(start[5:7])
    except (ValueError, IndexError):
        return []
    out = []
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _loan_payment_months(pattern: str | None, persons=None) -> dict[str, float]:
    """{YYYY-MM: sum faktisk betalt} fra utgående overføringer som matcher pattern."""
    pattern = (pattern or "").strip().lower()
    if not pattern:
        return {}
    persons = _norm_persons(persons)
    like = f"%{pattern}%"
    rows = db.query(
        "SELECT substr(t.booking_date,1,7) AS m, t.amount AS amt, a.owner AS owner "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND t.amount < 0 AND "
        "(lower(t.remittance) LIKE ? OR lower(t.counterparty) LIKE ? OR lower(t.entry_reference) LIKE ?)",
        (like, like, like),
    )
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        if persons and (r["owner"] or "") not in persons:
            continue
        if r["m"]:
            out[r["m"]] += -r["amt"]
    return dict(out)


def _amortize(lb: dict, month: str, payments: dict | None = None) -> tuple[float, float]:
    """(restgjeld, månedens rente) for `month`. Bruker FAKTISKE betalinger
    (payments: {YYYY-MM: beløp}) der de finnes, ellers registrert terminbeløp –
    så amortiseringen (og rente-estimatet) følger virkeligheten, også ved
    flytende rente / varierende terminbeløp."""
    rate = _parse_rate(lb.get("rate"))
    r = rate / 12.0 if rate > 0 else 0.0
    bal = float(lb.get("start_balance", 0) or 0)
    monthly = float(lb.get("monthly_payment", 0) or 0)
    start = lb.get("start_date", "") or month
    if _months_between(start, month) < 0:
        return bal, 0.0  # lånet har ikke startet ennå → ingen rente denne måneden
    elapsed = max(0, _months_between(start, month))
    for mm in _months_range(start, elapsed):
        interest = bal * r
        pay = (payments or {}).get(mm)
        if pay is None:
            pay = monthly
        bal -= max(0.0, pay - interest)
        if bal <= 0:
            bal = 0.0
            break
    return max(0.0, bal), max(0.0, bal * r)


def _loan_interest(liabilities: list[dict], month: str, pay_map: dict | None = None) -> float:
    """Estimert lånerente for `month` (auto-lån). Renter = ekte kostnad → forbruk;
    avdrag er sparing. Bruker faktiske betalinger via pay_map når tilgjengelig."""
    total = 0.0
    for lb in liabilities or []:
        if not lb.get("auto") or _parse_rate(lb.get("rate")) <= 0:
            continue
        pat = (lb.get("pay_match") or "").strip().lower()
        # Uten pay_match kan vi ikke skille lånetrekket ut av forbruket – da ville
        # renten blitt talt i tillegg til hele terminbeløpet (dobbelttelling). Vent
        # med Lånerenter til brukeren har satt pay_match.
        if not pat:
            continue
        _, interest = _amortize(lb, month, (pay_map or {}).get(pat))
        total += interest
    return total


def _loan_split_total(liabilities: list[dict], pay_map: dict | None = None) -> tuple[float, float]:
    """Samlet (rente, avdrag) på tvers av ALLE auto-lån, over hele nedbetalingen vi har
    faktiske betalinger for. Rente fra amortiseringen; avdrag = betalt − rente."""
    tot_int = tot_prin = 0.0
    for lb in liabilities or []:
        if not lb.get("auto") or _parse_rate(lb.get("rate")) <= 0:
            continue
        pat = (lb.get("pay_match") or "").strip().lower()
        if not pat:
            continue
        months = (pay_map or {}).get(pat, {})
        for m, paid in months.items():
            _, rente = _amortize(lb, m, months)
            interest = min(rente, paid)
            tot_int += interest
            tot_prin += max(0.0, paid - interest)
    return tot_int, tot_prin


def _income_spending(txs: list[dict]) -> tuple[float, float]:
    """Som KPI-ene INN/UT: inntekt (positive, ekskl. Overføring) og forbruk
    (negative, ekskl. både Inntekt og Overføring). Brukes til cashflow så tallet
    blir nøyaktig = INN − UT (og dermed spareraten)."""
    income = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] != "Overføring")
    spending = sum(-t["amount"] for t in txs if t["amount"] < 0 and t["category"] not in NON_EXPENSE)
    return income, spending


def _record_liquidity_snapshot(cash: float, debt: float, net: float) -> None:
    """Lagre dagens netto likviditet (ett øyeblikksbilde per dato, siste vinner)."""
    today = date.today().isoformat()
    db.execute(
        "INSERT INTO liquidity_snapshots(date, cash, debt, net) VALUES(?,?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET cash=excluded.cash, debt=excluded.debt, net=excluded.net",
        (today, round(cash, 2), round(debt, 2), round(net, 2)),
    )


def _liquidity_history(month: str) -> list[dict]:
    """12 mnd med faktiske øyeblikksbilder (siste måling i hver måned)."""
    snaps: dict[str, dict] = {}
    for r in db.query("SELECT date, cash, debt, net FROM liquidity_snapshots ORDER BY date"):
        ym = (r["date"] or "")[:7]
        if ym:
            snaps[ym] = r  # ORDER BY date -> siste i måneden vinner
    points = []
    for m in _prev_months(month, 12):
        s = snaps.get(m)
        points.append({
            "month": m, "label": _month_label(m).split()[0][:3],
            "cash": round(s["cash"]) if s else 0,
            "debt": round(s["debt"]) if s else 0,
            "net": round(s["net"]) if s else 0,
            "current": m == month,
            "has": s is not None,
        })
    return points


def build_dashboard(month: str | None = None, persons=None) -> dict:
    month = month or current_month()
    persons = _norm_persons(persons)
    filtering = bool(persons)
    txs = _month_transactions(month, persons)
    income, expense = _income_expense(txs)

    budgets = db.get_setting("budgets", {}) or {}
    manual_assets = db.get_setting("manual_assets", []) or []
    manual_liabilities = db.get_setting("manual_liabilities", []) or []
    if filtering:
        # Personfilter = kun egne poster. «Felles» er en egen person som eier lån,
        # verdier og felles regningskonto – velg «Felles» for å se dem.
        manual_assets = [x for x in manual_assets if (x.get("owner") or "Felles") in persons]
        manual_liabilities = [x for x in manual_liabilities if (x.get("owner") or "Felles") in persons]
    household = db.get_setting("household_name", "Min økonomi")
    savings_goal = db.get_setting("savings_goal_pct", 20)

    # Faktiske lånebetalinger per mnd (fra overføringene) – driver amortisering + rente.
    loan_pay_map: dict[str, dict] = {}
    for lb in manual_liabilities:
        pat = (lb.get("pay_match") or "").strip().lower()
        if lb.get("auto") and pat and pat not in loan_pay_map:
            loan_pay_map[pat] = _loan_payment_months(pat, persons)

    # --- kategorier ---
    cat_totals: dict[str, float] = defaultdict(float)
    cat_items: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for t in txs:
        cat = t["category"]
        if cat in NON_EXPENSE or t["amount"] >= 0:
            continue
        amt = -t["amount"]
        cat_totals[cat] += amt
        key = t["counterparty"] or t["remittance"] or "Diverse"
        cat_items[cat][key][0] += amt
        cat_items[cat][key][1] += 1

    # Estimerte lånerenter (fra registrerte lån) telles som forbruk – de er en ekte
    # kostnad. Avdrag holdes utenfor (sparing, teller i netto formue).
    loan_interest = _loan_interest(manual_liabilities, month, loan_pay_map)
    if loan_interest > 0:
        cat_totals["Lånerenter"] += loan_interest

    total_expense = sum(cat_totals.values()) or expense or 1.0

    categories = []
    for name in categorize.CATEGORY_ORDER:
        amount = cat_totals.get(name, 0.0)
        if amount <= 0 and name not in budgets:
            continue
        budget = budgets.get(name, 0)
        items = sorted(cat_items.get(name, {}).items(), key=lambda kv: kv[1][0], reverse=True)[:6]
        item_list = [
            {
                "label": k + (f" ({v[1]} kjøp)" if v[1] > 1 else ""),
                "name": k,
                "amt": _fmt(v[0]),
                "flag": "",
            }
            for k, v in items
        ]
        over = budget and amount > budget
        if budget:
            diff = amount - budget
            if diff > 0:
                delta = f"{_fmt(diff)} kr over budsjett"
            else:
                delta = f"{_fmt(-diff)} kr under budsjett"
        else:
            delta = "Ingen budsjett satt"
        categories.append(
            {
                "name": name,
                "color": categorize.CATEGORY_COLORS.get(name, "#9aa0aa"),
                "amount": round(amount),
                "amountFmt": _fmt(amount),
                "pct": round(amount / total_expense * 100, 1),
                "budget": budget,
                "over": bool(over),
                "delta": delta,
                "items": item_list,
                "fixed": name in categorize.FIXED_CATEGORIES,
            }
        )

    # Ta med kategorier uten forhåndsdefinert rekkefølge (uventede)
    for name, amount in cat_totals.items():
        if name not in categorize.CATEGORY_ORDER and name not in NON_EXPENSE:
            categories.append(
                {
                    "name": name,
                    "color": categorize.CATEGORY_COLORS.get(name, "#9aa0aa"),
                    "amount": round(amount),
                    "amountFmt": _fmt(amount),
                    "pct": round(amount / total_expense * 100, 1),
                    "budget": budgets.get(name, 0),
                    "over": False,
                    "delta": "Ingen budsjett satt",
                    "items": [],
                    "fixed": False,
                }
            )

    categories.sort(key=lambda c: c["amount"], reverse=True)

    fixed_expense = sum(c["amount"] for c in categories if c["fixed"])

    # --- kontoer ---
    if filtering:
        ph = ",".join("?" for _ in persons)
        acc_rows = db.query(
            f"SELECT * FROM accounts WHERE hidden = 0 AND owner IN ({ph}) ORDER BY sort_order, name",
            persons,
        )
    else:
        acc_rows = db.query(
            "SELECT * FROM accounts WHERE hidden = 0 ORDER BY sort_order, name"
        )
    accounts = []
    asset_sum = 0.0        # teller i netto formue (inkl. kredittkort-gjeld som negativ)
    liquid_sum = 0.0       # disponibelt på konto (kredittkort holdt utenfor)
    credit_debt = 0.0      # utestående kredittkortgjeld (negativ) – trekkes fra netto likviditet
    credit_available = 0.0  # ledig kreditt (nødbuffer) – vises separat, IKKE som likvid
    for a in acc_rows:
        bal = account_current_balance(a["id"])
        has_bal = bool(db.query("SELECT 1 FROM balances WHERE account_id = ? LIMIT 1", (a["id"],)))
        if a["is_credit"]:
            credit_debt += bal  # gjeld er negativ, uansett is_asset
            av = db.query(
                "SELECT amount FROM balances WHERE account_id = ? AND balance_type = 'available'",
                (a["id"],))
            if av:
                credit_available += av[0]["amount"]           # banken gir ledig kreditt
            elif a["credit_limit"]:
                credit_available += max(0.0, a["credit_limit"] + bal)  # ramme − benyttet (bal er neg.)
        if a["is_asset"]:
            asset_sum += bal
            if not a["is_credit"]:
                liquid_sum += bal
        accounts.append(
            {
                "id": a["id"],
                "name": a["name"],
                "bank_code": a["bank_code"] or "",
                "owner": a["owner"] or "",
                "amount": round(bal),
                "amountFmt": _fmt(bal) if has_bal else "—",
                "hasBalance": has_bal,
                "is_asset": bool(a["is_asset"]),
            }
        )

    # manuelle eiendeler (bolig, fond som ikke er koblet mm.)
    manual_asset_sum = sum(float(x.get("value", 0)) for x in manual_assets)
    for x in manual_assets:
        accounts.append(
            {
                "id": "manual:" + x.get("name", ""),
                "name": x.get("name", ""),
                "bank_code": x.get("tag", "MAN"),
                "owner": x.get("owner", ""),
                "amount": round(float(x.get("value", 0))),
                "amountFmt": _fmt(float(x.get("value", 0))),
                "hasBalance": True,
                "is_asset": True,
                "note": x.get("note", ""),
                "manual": True,
            }
        )

    # --- netto likviditet + utvikling ---
    # Netto likviditet = disponibelt på konto − utestående kredittkortgjeld (eksakt nå).
    # Historikk kan IKKE rekonstrueres ærlig bakover fra transaksjoner (mange kontoer,
    # overføringer, kortbetalinger, delvis historikk gir tull). Vi lagrer i stedet et
    # daglig øyeblikksbilde og bygger grafen fra FAKTISKE målinger framover.
    current_liquid = liquid_sum + credit_debt
    # Saldoene er alltid «nå» (uavhengig av valgt måned), så øyeblikksbildet og
    # grafen forankres til dagens dato.
    now_month = current_month()
    if not filtering:
        _record_liquidity_snapshot(liquid_sum, credit_debt, current_liquid)
    liq_points = _liquidity_history(now_month)
    real = [p for p in liq_points if p["has"]]
    nets = [p["net"] for p in real]
    cashes = [p["cash"] for p in real]
    # Endring siste 3 mnd fra faktiske øyeblikksbilder (hvis vi har eldre nok data).
    ref3, change3m = None, None
    if real:
        m3 = _prev_months(now_month, 4)[0]
        prior = [p for p in real if p["month"] <= m3]
        if prior:
            ref3 = prior[-1]["net"]
            change3m = round(current_liquid - ref3)
    liquidity = {
        "current": round(current_liquid),
        "currentFmt": _fmt(current_liquid),
        "cashFmt": _fmt(liquid_sum),
        "cardDebtFmt": _fmt(-credit_debt),   # positivt tall for visning
        "hasCardDebt": credit_debt < 0,
        "creditAvailableFmt": _fmt(credit_available),
        "hasCreditInfo": credit_available > 0,
        "filtered": filtering,   # personfilter aktivt -> skjul husholdnings-trend
        "points": liq_points,
        "hasHistory": len(real) >= 2,
        "maxCash": max(cashes) if cashes else max(1, round(liquid_sum)),
        "minNet": min(nets + [0]) if nets else 0,
        "change3m": change3m if change3m is not None else 0,
        "change3mFmt": (("+" if change3m >= 0 else "−") + _fmt(abs(change3m))) if change3m is not None else "",
        "up": change3m is None or change3m >= 0,
    }

    # --- lån ---
    liability_sum = 0.0
    loans = []
    for lb in manual_liabilities:
        estimated = False
        if lb.get("auto"):
            # Estimert restgjeld via amortisering (renter + avdrag), drevet av
            # FAKTISKE betalinger der de finnes (ellers registrert terminbeløp).
            pat = (lb.get("pay_match") or "").strip().lower()
            balance, _int = _amortize(lb, month, loan_pay_map.get(pat))
            start_balance = float(lb.get("start_balance", 0) or 0)
            original = float(lb.get("original", 0) or 0) or start_balance or balance
            estimated = True
        else:
            balance = float(lb.get("balance", 0) or 0)
            original = float(lb.get("original", 0) or 0) or balance
        liability_sum += balance
        paid = 1 - (balance / original) if original else 0
        loans.append(
            {
                "name": lb.get("name", "Lån"),
                "tag": lb.get("tag", ""),
                "rate": lb.get("rate", ""),
                "balance": round(balance),
                "balanceFmt": _fmt(balance),
                "paidPct": max(0, min(100, round(paid * 100))),
                "note": lb.get("note", ""),
                "estimated": estimated,
                "monthlyPayment": round(float(lb.get("monthly_payment", 0) or 0)) if estimated else None,
                "payMatch": lb.get("pay_match", ""),
                "paidThisMonth": lb.get("paid_this_month"),
                "interest": lb.get("interest"),
                "principal": lb.get("principal"),
            }
        )

    # Samlet rente/avdrag-fordeling (hele nedbetalingen) – for grafikk i Lån-kortet.
    loan_int_total, loan_prin_total = _loan_split_total(manual_liabilities, loan_pay_map)

    net_worth = asset_sum + manual_asset_sum - liability_sum

    # --- cashflow siste 7 måneder (netto = INN − UT inkl. lånerenter, som spareraten) ---
    cashflow = []
    for m in _prev_months(month, 7):
        inc, spend = _income_spending(_month_transactions(m, persons))
        net = inc - spend - _loan_interest(manual_liabilities, m, loan_pay_map)
        cashflow.append(
            {
                "label": _month_label(m).split()[0][:3],
                "month": m,
                "net": round(net),
                "netK": round(net / 1000),
                "current": m == month,
            }
        )
    # Ekte «hittil i år»: januar → valgt måned (kalenderår), ikke bare 7 mnd.
    yr, mo = int(month[:4]), int(month[5:7])
    ytd_net = 0.0
    for mm in range(1, mo + 1):
        ym = f"{yr:04d}-{mm:02d}"
        inc, spend = _income_spending(_month_transactions(ym, persons))
        ytd_net += inc - spend - _loan_interest(manual_liabilities, ym, loan_pay_map)

    # Kombinert trend (12 mnd, forankret til i dag): sparing (flyt, stolper) +
    # netto likviditet (nivå, linje – kun der vi har øyeblikksbilde).
    snap_by_month = {p["month"]: p for p in liq_points}
    trend = []
    for m in _prev_months(now_month, 12):
        inc, spend = _income_spending(_month_transactions(m, persons))
        spend += _loan_interest(manual_liabilities, m, loan_pay_map)
        p = snap_by_month.get(m)
        trend.append({
            "label": _month_label(m).split()[0][:3],
            "month": m,
            "flow": round(inc - spend),
            "liq": (p["net"] if (p and p["has"]) else None),
            "current": m == now_month,
        })

    # --- budsjett totalt ---
    total_budget = sum(budgets.values()) if budgets else 0
    variable_expense = total_expense - fixed_expense
    savings_rate = ((income - total_expense) / income * 100) if income > 0 else 0

    # --- abonnementer ---
    subs = next((c for c in categories if c["name"] == "Abonnementer"), None)

    # --- innsikt/varsler ---
    reminders = _csv_reminders()
    summary_text = _build_summary(month, income, total_expense, total_budget, savings_rate, savings_goal, categories)
    if reminders:
        summary_text = summary_text + " ⚠ " + reminders[0]

    return {
        "month": month,
        "monthLabel": _month_label(month),
        "household": household,
        "persons": _persons_list(),
        "selectedPersons": persons,
        "kpis": {
            "netWorth": _fmt(net_worth),
            "netWorthNote": "inkl. manuelle verdier − lån"
            if (manual_asset_sum or liability_sum)
            else "sum av tilkoblede kontoer",
            "income": _fmt(income),
            "expense": _fmt(total_expense),
            "surplus": (("−" if (income - total_expense) < 0 else "") + _fmt(abs(income - total_expense))),
            "surplusNeg": (income - total_expense) < 0,
            "fixed": _fmt(fixed_expense),
            "fixedPct": round(fixed_expense / total_expense * 100) if total_expense else 0,
            "savingsRate": f"{savings_rate:.1f}".replace(".", ",") if income else "0",
            "savingsGoal": savings_goal,
        },
        "categories": categories,
        "totalExpense": round(total_expense),
        "totalExpenseFmt": _fmt(total_expense),
        "accounts": accounts,
        "loans": loans,
        "loanSplit": {
            "hasData": (loan_int_total + loan_prin_total) > 0,
            "interest": round(loan_int_total), "principal": round(loan_prin_total),
            "interestFmt": _fmt(loan_int_total), "principalFmt": _fmt(loan_prin_total),
        },
        "liquidity": liquidity,
        "cashflow": cashflow,
        "trend": trend,
        "ytdNet": _fmt(ytd_net),
        "budget": {
            "total": total_budget,
            "totalFmt": _fmt(total_budget),
            "spent": round(total_expense),
            "spentFmt": _fmt(total_expense),
            "pct": round(total_expense / total_budget * 100) if total_budget else 0,
            "fixed": round(fixed_expense),
            "fixedFmt": _fmt(fixed_expense),
            "variable": round(variable_expense),
            "variableFmt": _fmt(variable_expense),
            "remaining": round(total_budget - total_expense) if total_budget else 0,
            "remainingFmt": _fmt(max(0, total_budget - total_expense)) if total_budget else "0",
        },
        "subscriptions": subs,
        "summary": summary_text,
        "reminders": reminders,
        "txCount": len(txs),
    }


def _csv_reminders() -> list[str]:
    """Varsle dersom en CSV-importert konto (f.eks. Coop-kortet) mangler ferske tall."""
    out = []
    cur = current_month()
    prev = _prev_months(cur, 2)[0]
    for a in db.query("SELECT id, name FROM accounts WHERE institution_id = 'csv-import' AND hidden = 0"):
        row = db.query("SELECT MAX(booking_date) AS m FROM transactions WHERE account_id = ?", (a["id"],))
        lastm = (row[0]["m"] or "")[:7]
        if not lastm or lastm < prev:
            out.append(
                f"«{a['name']}» mangler ferske tall (siste: {lastm or 'ingen'}). "
                f"Husk å laste opp ny CSV så regnskapet blir riktig."
            )
    return out


def _build_summary(month, income, expense, total_budget, savings_rate, goal, categories) -> str:
    """Enkel regelbasert månedsoppsummering (ingen ekstern AI kreves)."""
    parts = []
    label = _month_label(month).split()[0]
    if total_budget:
        diff = total_budget - expense
        if diff >= 0:
            parts.append(f"{label} ligger {_fmt(diff)} kr under budsjett.")
        else:
            parts.append(f"{label} ligger {_fmt(-diff)} kr over budsjett.")
    over_cats = [c for c in categories if c["over"]]
    if over_cats:
        names = ", ".join(c["name"].lower() for c in over_cats[:3])
        parts.append(f"Over budsjett: {names}.")
    if income > 0:
        left = income - expense
        parts.append(
            f"Med {_fmt(left)} kr til overs er spareraten {savings_rate:.1f}".replace(".", ",")
            + f" %, mot målet på {goal} %."
        )
    if not parts:
        parts.append("Koble til bankene og synkroniser for å se månedens oppsummering her.")
    return " ".join(parts)


def _range_transactions(month: str, period: str) -> list[dict]:
    base = ("SELECT t.*, a.owner AS owner, a.bank_code AS bank_code, a.name AS acct_name "
            "FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.hidden = 0")
    if period == "all":
        rows = db.query(base + " ORDER BY t.booking_date DESC")
    else:
        n = {"month": 1, "3m": 3, "12m": 12}.get(period, 1)
        start = _prev_months(month, n)[0]
        rows = db.query(
            base + " AND substr(t.booking_date,1,7) >= ? AND substr(t.booking_date,1,7) <= ? "
            "ORDER BY t.booking_date DESC",
            (start, month),
        )
    return [dict(r) for r in rows]


def build_transactions(month: str | None, persons, category: str | None,
                       query: str | None, label: str | None = None, flow: str | None = None,
                       min_amount: float | None = None, max_amount: float | None = None,
                       account: str | None = None) -> dict:
    month = month or current_month()
    persons = _norm_persons(persons)
    q = (query or "").lower().strip()
    account = (account or "").strip()
    # Skarpt filter aktivt (søk / beløp / konto) → søk på tvers av ALLE måneder.
    # Ellers viser vi valgt måned (månedsnavigasjon som på forsiden).
    cross_month = bool(q) or (min_amount is not None) or (max_amount is not None) or bool(account)
    rows = _range_transactions(month, "all" if cross_month else "month")
    out = []
    for t in rows:
        if persons and (t["owner"] or "") not in persons:
            continue
        if account and t["account_id"] != account:
            continue
        if category and t["category"] != category:
            continue
        # Inn/ut: samme definisjon som KPI-ene på forsiden (overføringer holdes utenfor).
        if flow == "in" and not (t["amount"] > 0 and t["category"] != "Overføring"):
            continue
        if flow == "out" and not (t["amount"] < 0 and t["category"] != "Overføring"):
            continue
        if flow == "fixed" and not (t["amount"] < 0 and t["category"] in categorize.FIXED_CATEGORIES):
            continue
        mag = abs(t["amount"] or 0)
        if min_amount is not None and mag < min_amount:
            continue
        if max_amount is not None and mag > max_amount:
            continue
        text = f"{t['counterparty']} {t['remittance']} {t['category']}".lower()
        if q and q not in text:
            continue
        lbls = labelmod.labels_for_row(t)
        if label and label != "Alle" and label not in lbls:
            continue
        amt = t["amount"]
        cp = (t["counterparty"] or "").strip()
        rem = (t["remittance"] or "").strip()
        primary = cp or rem or "—"
        # Vis remittance som detalj-linje når den tilfører noe utover hovedteksten
        # (f.eks. «Overføring mellom egne kontoer», «Til: EUROCARD Betalt: 17.07.26»).
        sub = rem if (rem and rem.lower() != primary.lower()) else ""
        out.append(
            {
                "id": t["id"],
                "date": _short_date(t["booking_date"]),
                "desc": primary,
                "sub": sub,
                "cat": t["category"],
                "acct": t["bank_code"] or t["acct_name"] or "",
                "person": t["owner"] or "",
                "labels": lbls,
                "amount": amt,
                "amtFmt": ("+" if amt > 0 else "−") + _fmt(abs(amt)),
                "positive": amt > 0,
            }
        )
    accts = [{"id": r["id"], "name": r["name"], "bank_code": r["bank_code"] or ""}
             for r in db.query("SELECT id, name, bank_code FROM accounts WHERE hidden = 0 ORDER BY sort_order, name")]
    total = sum(t["amount"] for t in out)
    return {"rows": out, "count": len(out), "persons": _persons_list(),
            "selectedPersons": persons,
            "categories": list(categorize.CATEGORY_ORDER) + ["Inntekt", "Overføring"],
            "allLabels": labelmod.all_labels(), "label": label or "Alle",
            "month": month, "monthLabel": _month_label(month),
            "crossMonth": cross_month, "accounts": accts,
            "minAmount": min_amount, "maxAmount": max_amount, "account": account,
            "sumFmt": ("+" if total >= 0 else "−") + _fmt(abs(total)),
            "flow": flow or ""}


def _short_date(iso: str | None) -> str:
    if not iso or len(iso) < 10:
        return iso or ""
    y, m, d = iso[:10].split("-")
    return f"{d}.{m}"


# ---------- analyse / innsikt ----------

def _category_expense_map(txs: list[dict]) -> dict:
    d: dict[str, float] = defaultdict(float)
    for t in txs:
        if t["amount"] < 0 and t["category"] not in NON_EXPENSE:
            d[t["category"]] += -t["amount"]
    return d


def build_analysis(month: str | None = None, persons=None,
                   label: str | None = None) -> dict:
    month = month or current_month()
    persons = _norm_persons(persons)
    prev_month = _prev_months(month, 2)[0]
    last4 = _prev_months(month, 4)
    prior3 = last4[:3]

    def mtx(m: str) -> list[dict]:
        rows = _month_transactions(m, persons)
        if label and label != "Alle":
            rows = [t for t in rows if label in labelmod.labels_for_row(t)]
        return rows

    cur_txs = mtx(month)
    prev_txs = mtx(prev_month)
    cur_cat = _category_expense_map(cur_txs)
    prev_cat = _category_expense_map(prev_txs)

    # kostnad per label (for inneværende måned, uavhengig av valgt label)
    by_label: dict[str, float] = defaultdict(float)
    for t in _month_transactions(month, persons):
        if t["amount"] < 0 and t["category"] not in NON_EXPENSE:
            for lab in labelmod.labels_for_row(t):
                by_label[lab] += -t["amount"]
    label_breakdown = sorted(
        [{"label": k, "amountFmt": _fmt(v)} for k, v in by_label.items()],
        key=lambda x: x["label"],
    )

    avg_cat: dict[str, float] = defaultdict(float)
    for m in prior3:
        for cat, amt in _category_expense_map(mtx(m)).items():
            avg_cat[cat] += amt / 3.0

    comparison = []
    for c in set(cur_cat) | set(prev_cat) | set(avg_cat):
        cur, prev, avg = cur_cat.get(c, 0.0), prev_cat.get(c, 0.0), avg_cat.get(c, 0.0)
        delta = cur - prev
        comparison.append({
            "name": c,
            "color": categorize.CATEGORY_COLORS.get(c, "#9aa0aa"),
            "current": round(cur), "currentFmt": _fmt(cur),
            "prev": round(prev), "prevFmt": _fmt(prev),
            "delta": round(delta),
            "deltaFmt": ("+" if delta >= 0 else "−") + _fmt(abs(delta)),
            "deltaPct": round(delta / prev * 100) if prev else (100 if cur else 0),
            "up": delta > 0,
            "avgFmt": _fmt(avg),
            "vsAvgPct": round((cur - avg) / avg * 100) if avg else 0,
        })
    comparison.sort(key=lambda x: x["current"], reverse=True)

    movers = sorted(
        [c for c in comparison if abs(c["delta"]) >= 100 and (c["prev"] or c["current"])],
        key=lambda x: abs(x["delta"]), reverse=True,
    )[:4]

    # toppbutikker denne måneden
    merch: dict[str, list] = defaultdict(lambda: [0.0, 0, ""])
    for t in cur_txs:
        if t["amount"] < 0 and t["category"] not in NON_EXPENSE:
            key = t["counterparty"] or t["remittance"] or "Diverse"
            merch[key][0] += -t["amount"]
            merch[key][1] += 1
            merch[key][2] = t["category"]
    top_merchants = [
        {"name": k, "amountFmt": _fmt(v[0]), "count": v[1], "category": v[2]}
        for k, v in sorted(merch.items(), key=lambda kv: kv[1][0], reverse=True)[:8]
    ]

    # største enkeltkjøp
    exp = sorted(
        [t for t in cur_txs if t["amount"] < 0 and t["category"] not in NON_EXPENSE],
        key=lambda t: t["amount"],
    )[:6]
    biggest = [
        {"date": _short_date(t["booking_date"]),
         "desc": t["counterparty"] or t["remittance"] or "—",
         "amountFmt": _fmt(-t["amount"]), "category": t["category"],
         "person": t["owner"] or ""}
        for t in exp
    ]

    # gjentakende kjøp (i minst 3 av siste 4 måneder)
    seen: dict[str, set] = defaultdict(set)
    tot: dict[str, float] = defaultdict(float)
    meta: dict[str, tuple] = {}
    for m in last4:
        for t in mtx(m):
            if t["amount"] < 0 and t["category"] not in NON_EXPENSE:
                key = (t["counterparty"] or t["remittance"] or "").strip().lower()
                if not key:
                    continue
                seen[key].add(m)
                tot[key] += -t["amount"]
                meta[key] = (t["counterparty"] or t["remittance"], t["category"])
    recurring = sorted(
        [
            {"name": meta[k][0], "category": meta[k][1], "months": len(ms),
             "avgFmt": _fmt(tot[k] / len(ms))}
            for k, ms in seen.items() if len(ms) >= 3
        ],
        key=lambda r: r["months"], reverse=True,
    )[:8]

    inc_now, exp_now = _income_expense(cur_txs)
    inc_prev, exp_prev = _income_expense(prev_txs)
    saved_now, saved_prev = inc_now - exp_now, inc_prev - exp_prev

    # --- kategoritrend (siste 12 mnd) ---
    trend_months = _prev_months(month, 12)
    cat_month: dict[str, list] = defaultdict(lambda: [0.0] * 12)
    for idx, m in enumerate(trend_months):
        for c, amt in _category_expense_map(mtx(m)).items():
            cat_month[c][idx] += amt
    trends = sorted(
        [
            {"name": c, "color": categorize.CATEGORY_COLORS.get(c, "#9aa0aa"),
             "values": [round(v) for v in vals], "max": round(max(vals)),
             "totalFmt": _fmt(sum(vals)), "avgFmt": _fmt(round(sum(vals) / len(vals))),
             "lastFmt": _fmt(vals[-1])}
            for c, vals in cat_month.items() if sum(vals) > 0
        ],
        key=lambda t: sum(t["values"]), reverse=True,
    )

    # auto-innsikt
    insights = []
    if comparison:
        top = comparison[0]
        insights.append(f"Mest brukt denne måneden: {top['name']} med {top['currentFmt']} kr.")
    if movers:
        m0 = movers[0]
        insights.append(
            f"Størst endring: {m0['name']} – {m0['deltaFmt']} kr "
            f"{'mer' if m0['up'] else 'mindre'} enn forrige måned "
            f"({'+' if m0['up'] else '−'}{abs(m0['deltaPct'])} %)."
        )
    outliers = [c for c in comparison if c["vsAvgPct"] >= 25 and c["current"] >= 500]
    if outliers:
        o = max(outliers, key=lambda c: c["vsAvgPct"])
        insights.append(f"{o['name']} ligger {o['vsAvgPct']} % over 3-måneders snittet.")
    if inc_now > 0:
        insights.append(
            f"Du satt igjen med {_fmt(saved_now)} kr denne måneden "
            f"(mot {_fmt(saved_prev)} kr forrige)."
        )

    return {
        "month": month, "monthLabel": _month_label(month),
        "prevMonthLabel": _month_label(prev_month),
        "persons": _persons_list(), "selectedPersons": persons,
        "allLabels": labelmod.all_labels(), "label": label or "Alle",
        "labelBreakdown": label_breakdown,
        "comparison": comparison, "movers": movers,
        "topMerchants": top_merchants, "biggest": biggest, "recurring": recurring,
        "trendMonths": [_month_label(m).split()[0][:3] for m in trend_months],
        "trends": trends,
        "totals": {
            "expenseNow": _fmt(exp_now), "expensePrev": _fmt(exp_prev), "expenseUp": exp_now > exp_prev,
            "incomeNow": _fmt(inc_now), "incomePrev": _fmt(inc_prev),
            "savedNow": _fmt(saved_now), "savedPrev": _fmt(saved_prev),
        },
        "insights": insights,
    }


def build_merchant(name: str | None, persons=None, label: str | None = None) -> dict:
    """Historikk for én butikk/motpart over tid (kostnad per måned + siste kjøp)."""
    name = (name or "").strip()
    persons = _norm_persons(persons)
    if not name:
        return {"name": "", "count": 0, "series": [], "recent": []}
    # Match på counterparty ELLER remittance – mange butikker har tom counterparty
    # (navnet ligger i remittance, f.eks. «COOP MEGA KLEPP · 710»).
    rows = db.query(
        "SELECT t.*, a.owner AS owner, a.bank_code AS bank_code, a.name AS acct_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND (lower(t.counterparty) = lower(?) OR lower(t.remittance) = lower(?)) "
        "ORDER BY t.booking_date DESC",
        (name, name),
    )
    txs = [dict(r) for r in rows]
    if persons:
        txs = [t for t in txs if (t["owner"] or "") in persons]
    if label and label != "Alle":
        txs = [t for t in txs if label in labelmod.labels_for_row(t)]

    # Et sted er enten en utgift eller en inntektskilde. Velg dominerende retning
    # så inntektssteder (f.eks. ADYEN, lønn, refusjoner) ikke får tom graf/snitt.
    pos_sum = sum(t["amount"] for t in txs if t["amount"] > 0)
    neg_sum = sum(-t["amount"] for t in txs if t["amount"] < 0)
    income_mode = pos_sum > neg_sum
    relevant = [t for t in txs if (t["amount"] > 0) == income_mode and t["amount"] != 0]
    total = sum(abs(t["amount"]) for t in relevant)
    count = len(relevant)
    cat_count: dict[str, int] = defaultdict(int)
    for t in relevant:
        cat_count[t["category"]] += 1
    category = max(cat_count, key=cat_count.get) if cat_count else (txs[0]["category"] if txs else "")

    months = _prev_months(current_month(), 12)
    by_month: dict[str, float] = defaultdict(float)
    for t in relevant:
        by_month[(t["booking_date"] or "")[:7]] += abs(t["amount"])
    series = [
        {"month": m, "label": _month_label(m).split()[0][:3], "amount": round(by_month.get(m, 0.0))}
        for m in months
    ]
    active = [m for m in by_month if by_month[m] > 0]

    recent = [
        {"date": _short_date(t["booking_date"]),
         "amtFmt": ("+" if t["amount"] > 0 else "−") + _fmt(abs(t["amount"])),
         "positive": t["amount"] > 0,
         "acct": t["bank_code"] or t["acct_name"] or "", "cat": t["category"],
         "person": t["owner"] or ""}
        for t in txs[:12]
    ]
    return {
        "name": name, "category": category,
        "income": income_mode,
        "flowLabel": "Inntekt" if income_mode else "Kostnad",
        "unit": "innslag" if income_mode else "kjøp",
        "totalFmt": _fmt(total), "count": count,
        "avgFmt": _fmt(total / count) if count else "0",
        "monthlyAvgFmt": _fmt(total / len(active)) if active else "0",
        "series": series, "max": max((s["amount"] for s in series), default=0),
        "recent": recent, "months": len(active),
        "first": _short_date(txs[-1]["booking_date"]) if txs else "",
        "last": _short_date(txs[0]["booking_date"]) if txs else "",
    }


def build_loan_history(pattern: str | None, persons=None) -> dict:
    """Faktiske lånebetalinger (utgående overføringer) som matcher `pattern`
    (lånekontonr. eller tekst) i remittance/counterparty."""
    pattern = (pattern or "").strip().lower()
    persons = _norm_persons(persons)
    if not pattern:
        return {"count": 0, "series": [], "recent": [], "totalFmt": "0", "avgFmt": "0", "max": 0, "months": 0}
    like = f"%{pattern}%"
    rows = db.query(
        "SELECT t.*, a.owner AS owner, a.bank_code AS bank_code, a.name AS acct_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND "
        "(lower(t.remittance) LIKE ? OR lower(t.counterparty) LIKE ? OR lower(t.entry_reference) LIKE ?) "
        "ORDER BY t.booking_date DESC",
        (like, like, like),
    )
    txs = [dict(r) for r in rows]
    if persons:
        txs = [t for t in txs if (t["owner"] or "") in persons]
    pays = [t for t in txs if t["amount"] < 0]  # betalinger = utgående
    total = sum(-t["amount"] for t in pays)
    by_month: dict[str, float] = defaultdict(float)
    for t in pays:
        by_month[(t["booking_date"] or "")[:7]] += -t["amount"]

    # Rente/avdrag-splitt: finn lånet som matcher dette pay_match, og bruk amortiseringen
    # til å dele hver månedsbetaling i rentekostnad vs. avdrag (egenkapital).
    lb = None
    for x in db.get_setting("manual_liabilities", []) or []:
        if x.get("auto") and (x.get("pay_match") or "").strip().lower() == pattern:
            lb = x
            break
    pay_map = _loan_payment_months(pattern, persons) if lb else {}

    months = _prev_months(current_month(), 12)
    tot_int = tot_prin = 0.0
    series = []
    for m in months:
        amt = round(by_month.get(m, 0.0))
        interest = principal = None
        if lb and by_month.get(m, 0) > 0:
            _, rente = _amortize(lb, m, pay_map)
            interest = round(min(rente, by_month[m]))     # renten kan aldri overstige betalingen
            principal = round(by_month[m]) - interest
            tot_int += interest
            tot_prin += principal
        series.append({
            "month": m, "label": _month_label(m).split()[0][:3],
            "amount": amt, "interest": interest, "principal": principal,
        })
    active = [m for m in by_month if by_month[m] > 0]
    recent = [
        {"date": _short_date(t["booking_date"]),
         "amtFmt": "−" + _fmt(-t["amount"]),
         "desc": (t["counterparty"] or t["remittance"] or "")[:44],
         "acct": t["bank_code"] or t["acct_name"] or ""}
        for t in pays[:12]
    ]
    return {
        "count": len(pays), "totalFmt": _fmt(total),
        "avgFmt": _fmt(total / len(active)) if active else "0",
        "series": series, "max": max((s["amount"] for s in series), default=0),
        "recent": recent, "months": len(active),
        "hasSplit": lb is not None,
        "totalInterestFmt": _fmt(tot_int), "totalPrincipalFmt": _fmt(tot_prin),
    }


# ---------- budsjett / regnskap ----------

def _all_data_months() -> list[str]:
    rows = db.query(
        "SELECT DISTINCT substr(t.booking_date,1,7) AS m FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id WHERE a.hidden = 0 AND t.booking_date IS NOT NULL "
        "ORDER BY m"
    )
    return [r["m"] for r in rows if r["m"]]


def _category_month_actuals(year: int) -> dict:
    """{(kategori, 'YYYY-MM'): utgiftsbeløp} for ett år (positive tall)."""
    rows = db.query(
        "SELECT t.category AS cat, substr(t.booking_date,1,7) AS m, SUM(-t.amount) AS amt "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND t.amount < 0 AND substr(t.booking_date,1,4) = ? "
        "GROUP BY t.category, m",
        (str(year),),
    )
    out = {}
    for r in rows:
        if r["cat"] in NON_EXPENSE:
            continue
        out[(r["cat"], r["m"])] = r["amt"] or 0.0
    return out


def _suggestions() -> dict:
    """Smart forslag: faste = siste kjente beløp, variable = snitt siste 12 datamåneder."""
    months = _all_data_months()[-12:]
    if not months:
        return {}
    # hent alle utgifter per kategori/måned for disse månedene
    placeholders = ",".join("?" for _ in months)
    rows = db.query(
        f"SELECT t.category AS cat, substr(t.booking_date,1,7) AS m, SUM(-t.amount) AS amt "
        f"FROM transactions t JOIN accounts a ON a.id = t.account_id "
        f"WHERE a.hidden = 0 AND t.amount < 0 AND substr(t.booking_date,1,7) IN ({placeholders}) "
        f"GROUP BY t.category, m",
        months,
    )
    by_cat: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["cat"] in NON_EXPENSE:
            continue
        by_cat[r["cat"]][r["m"]] = r["amt"] or 0.0

    n = len(months)
    newest = months[-1]
    suggestions = {}
    for cat, mvals in by_cat.items():
        if cat in categorize.FIXED_CATEGORIES:
            # siste kjente beløp (nyeste måned med verdi), ellers snitt
            val = 0.0
            for m in reversed(months):
                if mvals.get(m):
                    val = mvals[m]
                    break
            if not val:
                val = sum(mvals.values()) / n
        else:
            val = sum(mvals.values()) / n  # snitt, tomme måneder teller som 0
        suggestions[cat] = int(round(val / 50.0)) * 50  # rund til nærmeste 50
    return suggestions


def build_budget_matrix(year: int | None = None) -> dict:
    year = year or date.today().year
    actuals = _category_month_actuals(year)
    months = [f"{year}-{m:02d}" for m in range(1, 13)]
    month_names = ["Jan", "Feb", "Mar", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Des"]

    # Lånerenter er modellert (ikke en transaksjon) – injiser den per måned så
    # regnskaps-matrisen blir konsistent med forsiden. Drevet av faktiske betalinger.
    manual_liabilities = db.get_setting("manual_liabilities", []) or []
    loan_pay_map: dict[str, dict] = {}
    for lb in manual_liabilities:
        pat = (lb.get("pay_match") or "").strip().lower()
        if lb.get("auto") and pat and pat not in loan_pay_map:
            loan_pay_map[pat] = _loan_payment_months(pat)
    # Kun måneder med FAKTISK lånebetaling – ikke projiser inn i tomme/framtidige måneder.
    paid_months = set()
    for mp in loan_pay_map.values():
        paid_months.update(mp.keys())
    for m in months:
        if m not in paid_months:
            continue
        li = _loan_interest(manual_liabilities, m, loan_pay_map)
        if li > 0:
            actuals[("Lånerenter", m)] = actuals.get(("Lånerenter", m), 0.0) + li

    # hvilke måneder har data i dette året
    data_months = {m for m in months if any(k[1] == m for k in actuals)}

    budgets = db.get_setting("budgets", {}) or {}
    suggestions = _suggestions()

    # kategorier: standardrekkefølge + evt. ekstra som har data
    cats = list(categorize.CATEGORY_ORDER)
    for (cat, _m) in actuals:
        if cat not in cats and cat not in NON_EXPENSE:
            cats.append(cat)

    denom = max(1, len(data_months))  # snitt over måneder med data i året
    rows = []
    col_totals = [0.0] * 12
    for cat in cats:
        monthly = []
        s = 0.0
        for i, m in enumerate(months):
            v = actuals.get((cat, m), 0.0)
            monthly.append(round(v))
            col_totals[i] += v
            if m in data_months:
                s += v
        avg = s / denom
        rows.append(
            {
                "name": cat,
                "color": categorize.CATEGORY_COLORS.get(cat, "#9aa0aa"),
                "fixed": cat in categorize.FIXED_CATEGORIES,
                "monthly": monthly,
                "monthlyFmt": [_fmt(x) if x else "" for x in monthly],
                "total": round(s),
                "totalFmt": _fmt(s),
                "avg": round(avg) if s else 0,
                "avgFmt": _fmt(avg) if s else "",
                "budget": budgets.get(cat, 0),
                "suggestion": suggestions.get(cat, 0),
            }
        )

    # inntekt per måned
    inc_rows = db.query(
        "SELECT substr(t.booking_date,1,7) AS m, SUM(t.amount) AS amt "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND t.amount > 0 AND t.category NOT IN ('Overføring') "
        "AND substr(t.booking_date,1,4) = ? GROUP BY m",
        (str(year),),
    )
    income_by_month = {r["m"]: r["amt"] or 0.0 for r in inc_rows}
    income_monthly = [round(income_by_month.get(m, 0.0)) for m in months]

    total_expense = sum(col_totals)
    total_income = sum(income_monthly)

    return {
        "year": year,
        "months": month_names,
        "monthKeys": months,
        "dataMonths": sorted(data_months),
        "rows": rows,
        "colTotals": [round(x) for x in col_totals],
        "colTotalsFmt": [_fmt(x) if x else "" for x in col_totals],
        "incomeMonthly": income_monthly,
        "incomeMonthlyFmt": [_fmt(x) if x else "" for x in income_monthly],
        "totalExpense": round(total_expense),
        "totalExpenseFmt": _fmt(total_expense),
        "totalIncome": round(total_income),
        "totalIncomeFmt": _fmt(total_income),
        "totalSaved": round(total_income - total_expense),
        "totalSavedFmt": _fmt(total_income - total_expense),
        "budgetTotal": sum(budgets.values()) if budgets else 0,
        "budgetTotalFmt": _fmt(sum(budgets.values()) if budgets else 0),
        "availableYears": _available_years(),
    }


def _available_years() -> list[int]:
    rows = db.query(
        "SELECT DISTINCT substr(t.booking_date,1,4) AS y FROM transactions t "
        "JOIN accounts a ON a.id = t.account_id WHERE a.hidden = 0 AND t.booking_date IS NOT NULL "
        "ORDER BY y DESC"
    )
    years = [int(r["y"]) for r in rows if r["y"] and r["y"].isdigit()]
    this_year = date.today().year
    if this_year not in years:
        years = [this_year] + years
    return years
