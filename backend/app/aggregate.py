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
    return ["Alle"] + [
        r["owner"] for r in db.query(
            "SELECT DISTINCT owner FROM accounts "
            "WHERE owner IS NOT NULL AND owner != '' AND hidden = 0 ORDER BY owner"
        )
    ]


def _income_expense(txs: list[dict]) -> tuple[float, float]:
    # Inntekt = alle positive beløp unntatt interne overføringer.
    # Forbruk = alle negative beløp unntatt interne overføringer.
    income = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] != "Overføring")
    expense = sum(-t["amount"] for t in txs if t["amount"] < 0 and t["category"] != "Overføring")
    return income, expense


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
        manual_assets = [x for x in manual_assets if (x.get("owner") or "") in persons]
        manual_liabilities = [x for x in manual_liabilities if (x.get("owner") or "") in persons]
    household = db.get_setting("household_name", "Min økonomi")
    savings_goal = db.get_setting("savings_goal_pct", 20)

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
    asset_sum = 0.0      # teller i netto formue (inkl. kredittkort-gjeld som negativ)
    liquid_sum = 0.0     # disponibel likviditet – kredittkort holdes UTENFOR
    for a in acc_rows:
        bal = account_current_balance(a["id"])
        has_bal = bool(db.query("SELECT 1 FROM balances WHERE account_id = ? LIMIT 1", (a["id"],)))
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

    # --- likviditet (disponibelt) + utvikling ---
    # Disponibelt = sum av tilkoblede bankkontoers saldo (is_asset). Utviklingen
    # rekonstrueres bakover fra dagens saldo + transaksjonene på disse kontoene.
    liquid_ids = [a["id"] for a in acc_rows if a["is_asset"] and not a["is_credit"]]
    current_liquid = liquid_sum
    net_by_month: dict[str, float] = {}
    if liquid_ids:
        ph = ",".join("?" for _ in liquid_ids)
        for r in db.query(
            f"SELECT substr(booking_date,1,7) AS m, SUM(amount) AS net FROM transactions "
            f"WHERE account_id IN ({ph}) AND booking_date IS NOT NULL GROUP BY m",
            liquid_ids,
        ):
            net_by_month[r["m"]] = r["net"] or 0.0
    bal = float(current_liquid)
    liq_points = []
    for m in reversed(_prev_months(month, 12)):
        liq_points.append({"month": m, "label": _month_label(m).split()[0][:3],
                           "value": round(bal), "current": m == month})
        bal -= net_by_month.get(m, 0.0)
    liq_points.reverse()
    liq_vals = [p["value"] for p in liq_points]
    ref3 = liq_points[-4]["value"] if len(liq_points) >= 4 else (liq_points[0]["value"] if liq_points else 0)
    change3m = round(current_liquid - ref3)
    liquidity = {
        "current": round(current_liquid),
        "currentFmt": _fmt(current_liquid),
        "points": liq_points,
        "min": min(liq_vals) if liq_vals else 0,
        "max": max(liq_vals) if liq_vals else 0,
        "change3m": change3m,
        "change3mFmt": ("+" if change3m >= 0 else "−") + _fmt(abs(change3m)),
        "up": change3m >= 0,
    }

    # --- lån ---
    liability_sum = 0.0
    loans = []
    for lb in manual_liabilities:
        estimated = False
        if lb.get("auto"):
            # Estimert restgjeld via amortisering: hver måned er en del av terminbeløpet
            # renter (saldo × rente/12) og resten avdrag. Uten rente: lineær nedbetaling.
            start_balance = float(lb.get("start_balance", 0) or 0)
            monthly = float(lb.get("monthly_payment", 0) or 0)
            elapsed = max(0, _months_between(lb.get("start_date", ""), month))
            rate = _parse_rate(lb.get("rate"))
            bal = start_balance
            if rate > 0:
                r = rate / 12.0
                for _ in range(elapsed):
                    principal = monthly - bal * r  # avdrag = terminbeløp − renter
                    bal -= principal
                    if bal <= 0:
                        bal = 0.0
                        break
            else:
                bal = start_balance - monthly * elapsed
            balance = max(0.0, bal)
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
                "paidThisMonth": lb.get("paid_this_month"),
                "interest": lb.get("interest"),
                "principal": lb.get("principal"),
            }
        )

    net_worth = asset_sum + manual_asset_sum - liability_sum

    # --- cashflow siste 7 måneder ---
    cashflow = []
    for m in _prev_months(month, 7):
        mtx = _month_transactions(m, persons)
        inc, exp = _income_expense(mtx)
        net = inc - exp
        cashflow.append(
            {
                "label": _month_label(m).split()[0][:3],
                "month": m,
                "net": round(net),
                "netK": round(net / 1000),
                "current": m == month,
            }
        )
    ytd_net = sum(c["net"] for c in cashflow)

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
        "liquidity": liquidity,
        "cashflow": cashflow,
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


_PERIOD_LABELS = {"month": "Denne måneden", "3m": "Siste 3 mnd", "12m": "Siste 12 mnd", "all": "Alt"}


def build_transactions(month: str | None, persons, category: str | None,
                       query: str | None, period: str | None = None,
                       label: str | None = None) -> dict:
    month = month or current_month()
    persons = _norm_persons(persons)
    period = period if period in _PERIOD_LABELS else "month"
    rows = _range_transactions(month, period)
    q = (query or "").lower().strip()
    out = []
    for t in rows:
        if persons and (t["owner"] or "") not in persons:
            continue
        if category and t["category"] != category:
            continue
        text = f"{t['counterparty']} {t['remittance']} {t['category']}".lower()
        if q and q not in text:
            continue
        lbls = labelmod.labels_for(t["counterparty"], t["remittance"])
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
    return {"rows": out, "count": len(out), "persons": _persons_list(),
            "selectedPersons": persons,
            "categories": list(categorize.CATEGORY_ORDER) + ["Inntekt", "Overføring"],
            "allLabels": labelmod.all_labels(), "label": label or "Alle",
            "month": month, "monthLabel": _month_label(month),
            "period": period, "periodLabel": _PERIOD_LABELS[period]}


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
            rows = [t for t in rows if label in labelmod.labels_for(t["counterparty"], t["remittance"])]
        return rows

    cur_txs = mtx(month)
    prev_txs = mtx(prev_month)
    cur_cat = _category_expense_map(cur_txs)
    prev_cat = _category_expense_map(prev_txs)

    # kostnad per label (for inneværende måned, uavhengig av valgt label)
    by_label: dict[str, float] = defaultdict(float)
    for t in _month_transactions(month, persons):
        if t["amount"] < 0 and t["category"] not in NON_EXPENSE:
            for lab in labelmod.labels_for(t["counterparty"], t["remittance"]):
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
             "totalFmt": _fmt(sum(vals)), "lastFmt": _fmt(vals[-1])}
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
    rows = db.query(
        "SELECT t.*, a.owner AS owner, a.bank_code AS bank_code, a.name AS acct_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND lower(t.counterparty) = lower(?) "
        "ORDER BY t.booking_date DESC",
        (name,),
    )
    txs = [dict(r) for r in rows]
    if persons:
        txs = [t for t in txs if (t["owner"] or "") in persons]
    if label and label != "Alle":
        txs = [t for t in txs if label in labelmod.labels_for(t["counterparty"], t["remittance"])]

    expenses = [t for t in txs if t["amount"] < 0]
    total = sum(-t["amount"] for t in expenses)
    count = len(expenses)
    cat_count: dict[str, int] = defaultdict(int)
    for t in expenses:
        cat_count[t["category"]] += 1
    category = max(cat_count, key=cat_count.get) if cat_count else (txs[0]["category"] if txs else "")

    months = _prev_months(current_month(), 12)
    by_month: dict[str, float] = defaultdict(float)
    for t in expenses:
        by_month[(t["booking_date"] or "")[:7]] += -t["amount"]
    series = [
        {"month": m, "label": _month_label(m).split()[0][:3], "amount": round(by_month.get(m, 0.0))}
        for m in months
    ]
    active = [m for m in by_month if by_month[m] > 0]

    recent = [
        {"date": _short_date(t["booking_date"]),
         "amtFmt": ("+" if t["amount"] > 0 else "−") + _fmt(abs(t["amount"])),
         "positive": t["amount"] > 0,
         "acct": t["bank_code"] or t["acct_name"] or "", "cat": t["category"]}
        for t in txs[:12]
    ]
    return {
        "name": name, "category": category,
        "totalFmt": _fmt(total), "count": count,
        "avgFmt": _fmt(total / count) if count else "0",
        "monthlyAvgFmt": _fmt(total / len(active)) if active else "0",
        "series": series, "max": max((s["amount"] for s in series), default=0),
        "recent": recent, "months": len(active),
        "first": _short_date(txs[-1]["booking_date"]) if txs else "",
        "last": _short_date(txs[0]["booking_date"]) if txs else "",
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
