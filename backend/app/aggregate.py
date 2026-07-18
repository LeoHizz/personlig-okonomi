"""Bygger dataene dashboardet viser, ut fra transaksjoner i databasen +
manuelle verdier (budsjett, boligverdi, lån) som brukeren setter i innstillinger.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from . import categorize, db

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
    for pref in ("closing", "available", "expected", "opening", "other"):
        if pref in by_type:
            return by_type[pref]
    return rows[0]["amount"]


def _month_transactions(month: str) -> list[dict]:
    rows = db.query(
        "SELECT t.*, a.owner AS owner, a.bank_code AS bank_code, a.name AS acct_name "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE a.hidden = 0 AND substr(t.booking_date,1,7) = ? "
        "ORDER BY t.booking_date DESC",
        (month,),
    )
    return [dict(r) for r in rows]


def _income_expense(txs: list[dict]) -> tuple[float, float]:
    # Inntekt = alle positive beløp unntatt interne overføringer.
    # Forbruk = alle negative beløp unntatt interne overføringer.
    income = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] != "Overføring")
    expense = sum(-t["amount"] for t in txs if t["amount"] < 0 and t["category"] != "Overføring")
    return income, expense


def build_dashboard(month: str | None = None) -> dict:
    month = month or current_month()
    txs = _month_transactions(month)
    income, expense = _income_expense(txs)

    budgets = db.get_setting("budgets", {}) or {}
    manual_assets = db.get_setting("manual_assets", []) or []
    manual_liabilities = db.get_setting("manual_liabilities", []) or []
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
    acc_rows = db.query(
        "SELECT * FROM accounts WHERE hidden = 0 ORDER BY sort_order, name"
    )
    accounts = []
    asset_sum = 0.0
    for a in acc_rows:
        bal = account_current_balance(a["id"])
        if a["is_asset"]:
            asset_sum += bal
        accounts.append(
            {
                "id": a["id"],
                "name": a["name"],
                "bank_code": a["bank_code"] or "",
                "owner": a["owner"] or "",
                "amount": round(bal),
                "amountFmt": _fmt(bal),
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
                "is_asset": True,
                "note": x.get("note", ""),
                "manual": True,
            }
        )

    # --- lån ---
    liability_sum = 0.0
    loans = []
    for lb in manual_liabilities:
        balance = float(lb.get("balance", 0))
        original = float(lb.get("original", 0)) or balance
        liability_sum += balance
        paid = 1 - (balance / original) if original else 0
        loans.append(
            {
                "name": lb.get("name", "Lån"),
                "tag": lb.get("tag", ""),
                "rate": lb.get("rate", ""),
                "balance": round(balance),
                "balanceFmt": _fmt(balance),
                "paidPct": round(paid * 100),
                "note": lb.get("note", ""),
                "paidThisMonth": lb.get("paid_this_month"),
                "interest": lb.get("interest"),
                "principal": lb.get("principal"),
            }
        )

    net_worth = asset_sum + manual_asset_sum - liability_sum

    # --- cashflow siste 7 måneder ---
    cashflow = []
    for m in _prev_months(month, 7):
        mtx = _month_transactions(m)
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

    return {
        "month": month,
        "monthLabel": _month_label(month),
        "household": household,
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
        "summary": _build_summary(month, income, total_expense, total_budget, savings_rate, savings_goal, categories),
        "txCount": len(txs),
    }


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


def build_transactions(month: str | None, person: str | None, category: str | None, query: str | None) -> dict:
    month = month or current_month()
    rows = _month_transactions(month)
    q = (query or "").lower().strip()
    out = []
    for t in rows:
        if person and person != "Alle" and (t["owner"] or "") != person:
            continue
        if category and t["category"] != category:
            continue
        text = f"{t['counterparty']} {t['remittance']} {t['category']}".lower()
        if q and q not in text:
            continue
        amt = t["amount"]
        out.append(
            {
                "id": t["id"],
                "date": _short_date(t["booking_date"]),
                "desc": t["counterparty"] or t["remittance"] or "—",
                "cat": t["category"],
                "acct": t["bank_code"] or t["acct_name"] or "",
                "person": t["owner"] or "",
                "amount": amt,
                "amtFmt": ("+" if amt > 0 else "−") + _fmt(abs(amt)),
                "positive": amt > 0,
            }
        )
    persons = ["Alle"] + [
        r["owner"] for r in db.query(
            "SELECT DISTINCT owner FROM accounts WHERE owner IS NOT NULL AND owner != '' ORDER BY owner"
        )
    ]
    return {"rows": out, "count": len(out), "persons": persons, "month": month, "monthLabel": _month_label(month)}


def _short_date(iso: str | None) -> str:
    if not iso or len(iso) < 10:
        return iso or ""
    y, m, d = iso[:10].split("-")
    return f"{d}.{m}"


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
