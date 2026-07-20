"""FastAPI-app: API mot GoCardless + servering av dashboardet."""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import aggregate, categorize, config, db, demo, provider as gc, importer, labels, sync

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
log = logging.getLogger("okonomi")

app = FastAPI(title="Personlig økonomi")


def _seconds_until_hour(hour: int) -> float:
    now = datetime.now()
    nxt = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


async def _auto_sync_loop() -> None:
    """Kjører sync.sync_all() én gang i døgnet på fastsatt time."""
    while True:
        await asyncio.sleep(_seconds_until_hour(config.AUTO_SYNC_HOUR))
        if not config.provider_configured():
            continue
        try:
            res = await asyncio.to_thread(sync.sync_all)
            log.info("Auto-synk fullført: %s konto(er)", len(res.get("synced", [])))
        except Exception as e:  # noqa: BLE001 – jobben skal aldri kunne dø
            log.warning("Auto-synk feilet: %s", e)


def _migrate_categories() -> None:
    """Engangsmigrering: del «Barn og forsikring»/«Barn og fritid» i Barn/Forsikring/Fritid."""
    if db.get_setting("migr_cat_forsikring"):
        return
    remap = {"Barn og forsikring": "Barn", "Barn og fritid": "Fritid"}
    budgets = db.get_setting("budgets", {}) or {}
    nb = {remap.get(k, k): v for k, v in budgets.items()}
    if nb != budgets:
        db.set_setting("budgets", nb)
    rules = db.get_setting("category_rules", []) or []
    changed = False
    for r in rules:
        if r.get("category") in remap:
            r["category"] = remap[r["category"]]
            changed = True
    if changed:
        db.set_setting("category_rules", rules)
    # Manuelt satte linjer omdøpes etter navn; ikke-manuelle re-kategoriseres av reglene.
    for old, new in remap.items():
        db.execute(
            "UPDATE transactions SET category = ? WHERE category = ? AND category_source = 'manual'",
            (new, old),
        )
    categorize.apply_rules_to_existing()
    db.set_setting("migr_cat_forsikring", True)


def _namespace_tx_ids() -> None:
    """Engangs: prefiks bank-transaksjons-ID (bare entry_reference) med konto-ID,
    så de matcher nytt skjema (account_id:ref) og re-synk ikke lager duplikater.
    Hopper over hash-ID (h_), csv-ID (csv_) og allerede namespacede (inneholder ':')."""
    if db.get_setting("migr_tx_namespace"):
        return
    db.execute(
        "UPDATE transactions SET id = account_id || ':' || id "
        "WHERE id IS NOT NULL AND instr(id, ':') = 0 "
        "AND id NOT LIKE 'h/_%' ESCAPE '/' AND id NOT LIKE 'csv/_%' ESCAPE '/'"
    )
    db.set_setting("migr_tx_namespace", True)


def _rename_category(old: str, new: str) -> None:
    """Ren omdøping av en kategori – flytter ALLE transaksjoner (auto + manuelle),
    budsjett-nøkkel og brukerregler fra gammelt til nytt navn. Idempotent."""
    db.execute("UPDATE transactions SET category = ? WHERE category = ?", (new, old))
    budgets = db.get_setting("budgets", {}) or {}
    if old in budgets:
        budgets[new] = budgets.pop(old)
        db.set_setting("budgets", budgets)
    rules = db.get_setting("category_rules", []) or []
    changed = False
    for r in rules:
        if r.get("category") == old:
            r["category"] = new
            changed = True
    if changed:
        db.set_setting("category_rules", rules)


def _ensure_column(table: str, col: str, decl: str) -> None:
    cols = [r["name"] for r in db.query(f"PRAGMA table_info({table})")]
    if col not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    _ensure_column("accounts", "bban", "TEXT")
    _ensure_column("accounts", "provider_ref", "TEXT")
    _ensure_column("accounts", "is_credit", "INTEGER DEFAULT 0")
    _ensure_column("accounts", "credit_limit", "REAL")  # manuell kredittramme (nødbuffer-utregning)
    _ensure_column("transactions", "labels", "TEXT")  # per-transaksjon-merkelapper (JSON)
    _migrate_categories()
    _rename_category("Bolig og lån", "Boliglån og husleie")
    _namespace_tx_ids()
    # Re-kategoriser eksisterende (ikke-manuelle) linjer når reglene er endret.
    if db.get_setting("rules_version") != categorize.RULES_VERSION:
        categorize.apply_rules_to_existing()
        db.set_setting("rules_version", categorize.RULES_VERSION)
    if config.AUTO_SYNC:
        asyncio.create_task(_auto_sync_loop())


# --- valgfri enkel tilgangsbeskyttelse (HTTP Basic) ---
@app.middleware("http")
async def _auth(request: Request, call_next):
    if config.APP_PASSWORD:
        # Slipp igjennom bankens redirect-callback uten prompt.
        if request.url.path != "/api/callback":
            header = request.headers.get("Authorization", "")
            if not _check_basic(header):
                return Response(
                    "Autentisering kreves",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Personlig økonomi"'},
                )
    return await call_next(request)


def _check_basic(header: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        user, _, pw = decoded.partition(":")
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(user, config.APP_USER) and secrets.compare_digest(
        pw, config.APP_PASSWORD
    )


# --- API ---

@app.get("/api/status")
def status():
    accounts = db.query("SELECT COUNT(*) AS n FROM accounts WHERE hidden = 0")
    n = accounts[0]["n"] if accounts else 0
    return {
        "configured": config.provider_configured(),
        "provider": config.PROVIDER,
        "connected_accounts": n,
        "needs_setup": n == 0,
        "last_sync_at": db.get_setting("last_sync_at"),
        "country": config.COUNTRY,
        "app_base_url": config.APP_BASE_URL,
        "demo": db.is_demo(),
    }


@app.post("/api/demo")
async def demo_toggle(request: Request):
    """Bytt til/fra demo-tall. Ekte data røres ikke, og modus nullstilles ved omstart."""
    body = await request.json()
    on = bool(body.get("on"))
    db.set_demo(on)
    if on:
        db.init_db()
        demo.seed_if_empty()
    return {"demo": db.is_demo()}


@app.get("/api/dashboard")
def dashboard(month: str | None = None, persons: str | None = None):
    return aggregate.build_dashboard(month, persons)


@app.get("/api/loan-history")
def loan_history(pattern: str, persons: str | None = None):
    return aggregate.build_loan_history(pattern, persons)


@app.get("/api/transactions")
def transactions(month: str | None = None, persons: str | None = None,
                 category: str | None = None, q: str | None = None,
                 period: str | None = None, label: str | None = None,
                 flow: str | None = None):
    return aggregate.build_transactions(month, persons, category, q, period, label, flow)


@app.get("/api/budget")
def budget(year: int | None = None):
    return aggregate.build_budget_matrix(year)


@app.get("/api/analysis")
def analysis(month: str | None = None, persons: str | None = None, label: str | None = None):
    return aggregate.build_analysis(month, persons, label)


@app.get("/api/merchant")
def merchant(name: str, persons: str | None = None, label: str | None = None):
    return aggregate.build_merchant(name, persons, label)


@app.post("/api/import/csv")
async def import_csv(request: Request):
    body = await request.json()
    text = body.get("text", "")
    if not text.strip():
        return JSONResponse({"error": "Ingen filinnhold mottatt."}, status_code=400)
    parsed, warnings = importer.parse_csv(text)
    if not parsed:
        return JSONResponse(
            {"error": warnings[0] if warnings else "Kunne ikke tolke filen.", "warnings": warnings},
            status_code=400,
        )
    account_id = body.get("account_id")
    if not account_id:
        account_id = importer.ensure_import_account(
            body.get("name", "Import").strip() or "Import",
            body.get("bank_code", "CSV"),
            body.get("owner", "Felles"),
        )
    count = importer.import_transactions(account_id, parsed)
    return {"imported": count, "parsed": len(parsed), "account_id": account_id, "warnings": warnings}


@app.get("/api/institutions")
def institutions():
    try:
        data = gc.list_institutions()
    except gc.Error as e:
        return JSONResponse({"error": str(e), "detail": e.detail}, status_code=e.status or 500)
    banks = [
        {"id": b["id"], "name": b.get("name", b["id"]), "logo": b.get("logo", "")}
        for b in data
    ]
    banks.sort(key=lambda x: x["name"].lower())
    return {"institutions": banks}


@app.post("/api/connect")
async def connect(request: Request):
    body = await request.json()
    institution_id = body.get("institution_id")
    if not institution_id:
        return JSONResponse({"error": "institution_id mangler"}, status_code=400)
    reference = f"okonomi-{uuid.uuid4().hex[:12]}"
    try:
        auth = gc.start_authorization(institution_id, reference)
    except gc.Error as e:
        return JSONResponse({"error": str(e), "detail": e.detail}, status_code=e.status or 500)
    db.upsert(
        "requisitions",
        {
            "id": auth["id"],
            "institution_id": institution_id,
            "institution_name": institution_id,
            "reference": reference,
            "status": "CR",
            "link": auth["url"],
            "created_at": gc.utc_now_iso(),
        },
    )
    return {"link": auth["url"], "requisition_id": auth["id"]}


@app.get("/api/callback")
def callback(request: Request):
    """Banken sender brukeren tilbake hit etter samtykke."""
    params = dict(request.query_params)
    if params.get("error"):
        return RedirectResponse(url=f"/?connect=error&msg={params.get('error')}")
    try:
        ids = sync.register_accounts(params)
    except gc.Error:
        return RedirectResponse(url="/?connect=error&msg=api")
    if ids:
        # Synk KUN de nytilkoblede kontoene – aldri de andre. En ny tilkobling
        # skal ikke røre eksisterende kontoer (unngår at de mister saldo/data).
        for aid in ids:
            try:
                sync.sync_account(aid, force=True)
            except gc.Error:
                pass
        return RedirectResponse(url="/?connect=ok")
    return RedirectResponse(url="/?connect=pending")


@app.post("/api/sync")
def do_sync(force: bool = False):
    try:
        return sync.sync_all(force=force)
    except gc.Error as e:
        return JSONResponse({"error": str(e), "detail": e.detail}, status_code=e.status or 500)


# --- likviditet-historikk (manuelle øyeblikksbilder brukeren kjenner) ---

@app.post("/api/liquidity-snapshot")
async def add_liquidity_snapshot(request: Request):
    body = await request.json()
    d = (body.get("date") or "").strip()
    if len(d) == 7:
        d += "-01"
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        return JSONResponse({"error": "ugyldig dato"}, status_code=400)
    try:
        net = float(str(body.get("net", "")).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return JSONResponse({"error": "ugyldig beløp"}, status_code=400)
    # Manuelt punkt: vi kjenner kun netto -> lagres som netto (ingen kort-oppdeling).
    db.execute(
        "INSERT INTO liquidity_snapshots(date, cash, debt, net) VALUES(?,?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET cash=excluded.cash, debt=excluded.debt, net=excluded.net",
        (d, net, 0.0, net),
    )
    return {"ok": True}


@app.post("/api/liquidity-snapshot/delete")
async def del_liquidity_snapshot(request: Request):
    body = await request.json()
    db.execute("DELETE FROM liquidity_snapshots WHERE date = ?", ((body.get("date") or "").strip(),))
    return {"ok": True}


# --- innstillinger ---

@app.get("/api/settings")
def get_settings():
    return {
        "household_name": db.get_setting("household_name", "Min økonomi"),
        "savings_goal_pct": db.get_setting("savings_goal_pct", 20),
        "budgets": db.get_setting("budgets", {}),
        "manual_assets": db.get_setting("manual_assets", []),
        "manual_liabilities": db.get_setting("manual_liabilities", []),
        "category_rules": db.get_setting("category_rules", []),
        "label_rules": db.get_setting("label_rules", []),
        "custom_labels": labels.custom_labels(),
        "labels": labels.all_labels(),
        "categories": categorize.CATEGORY_ORDER,
        "accounts": _accounts_with_balance(),
        "liquidity_history": [dict(r) for r in db.query(
            "SELECT date, net FROM liquidity_snapshots ORDER BY date DESC")],
    }


def _accounts_with_balance() -> list[dict]:
    out = []
    for r in db.query("SELECT * FROM accounts ORDER BY sort_order, name"):
        a = dict(r)
        has = db.query("SELECT 1 FROM balances WHERE account_id = ? LIMIT 1", (a["id"],))
        a["balanceFmt"] = aggregate._fmt(aggregate.account_current_balance(a["id"])) if has else "—"
        mb = db.query(
            "SELECT amount FROM balances WHERE account_id = ? AND balance_type = 'manual'", (a["id"],)
        )
        a["manualBalance"] = mb[0]["amount"] if mb else None
        out.append(a)
    return out


@app.post("/api/settings")
async def save_settings(request: Request):
    body = await request.json()
    for key in ("household_name", "savings_goal_pct", "budgets",
                "manual_assets", "manual_liabilities", "category_rules", "label_rules", "custom_labels"):
        if key in body:
            db.set_setting(key, body[key])
    if "category_rules" in body:
        # Nye/endrede regler skal slå igjennom på eksisterende linjer også.
        applied = categorize.apply_rules_to_existing()
        return {"ok": True, "recategorized": applied}
    return {"ok": True}


@app.post("/api/accounts/{account_id}")
async def update_account(account_id: str, request: Request):
    body = await request.json()

    # Manuell saldo (f.eks. «disponibelt» på Coop-kortet som ikke kommer fra bank).
    # Lagres som egen balanserad av typen 'manual'. Tomt felt = fjern.
    if "manual_balance" in body:
        raw = str(body.get("manual_balance", "")).strip().replace(" ", "").replace(",", ".")
        db.execute("DELETE FROM balances WHERE account_id = ? AND balance_type = 'manual'", (account_id,))
        if raw not in ("", "-"):
            try:
                amt = float(raw)
                db.execute(
                    "INSERT OR REPLACE INTO balances(account_id, balance_type, amount, currency, reference_date) "
                    "VALUES(?,?,?,?,?)",
                    (account_id, "manual", amt, "NOK", ""),
                )
            except ValueError:
                pass

    # Manuell kredittramme (for kort banken ikke gir ramme på, f.eks. Coop).
    if "credit_limit" in body:
        raw = str(body.get("credit_limit", "")).strip().replace(" ", "").replace(",", ".")
        val = None
        if raw not in ("", "-"):
            try:
                val = float(raw)
            except ValueError:
                val = None
        db.execute("UPDATE accounts SET credit_limit = ? WHERE id = ?", (val, account_id))

    allowed = {"name", "owner", "bank_code", "is_asset", "is_credit", "hidden", "sort_order"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE accounts SET {sets} WHERE id = ?", [*fields.values(), account_id])
    return {"ok": True}


def _refresh_details(account_id: str, provider_ref: str, cur_name: str) -> dict:
    """Oppdater kontonavn/IBAN/produkt (letter identifisering). Kan feile stille."""
    ref = provider_ref or account_id
    d = gc.get_account_details(ref)
    fields = {"iban": d.get("iban", ""), "bban": d.get("bban", ""), "product": d.get("product", "")}
    if not cur_name or cur_name in ("Konto", ""):
        fields["name"] = d.get("name", "Konto")
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE accounts SET {sets} WHERE id = ?", [*fields.values(), account_id])
    return d


@app.post("/api/accounts/{account_id}/refresh")
def refresh_account(account_id: str):
    """Hent alt på nytt fra banken: navn/IBAN, saldo OG nye transaksjoner."""
    row = db.query("SELECT name, provider_ref FROM accounts WHERE id = ?", (account_id,))
    ref = row[0]["provider_ref"] if row and row[0]["provider_ref"] else account_id
    try:
        d = _refresh_details(account_id, ref, row[0]["name"] if row else "")
        res = sync.sync_account(account_id, force=True)
    except gc.Error as e:
        return JSONResponse({"error": str(e), "detail": e.detail}, status_code=e.status or 500)
    return {
        "ok": True,
        "bankName": d.get("name", ""),
        "iban": d.get("iban", ""),
        "product": d.get("product", ""),
        "transactions": res.get("transactions", 0),
    }


@app.post("/api/accounts-refresh-all")
def refresh_all_accounts():
    """Hent saldo + transaksjoner (og navn/IBAN) for alle tilkoblede kontoer."""
    rows = db.query(
        "SELECT id, name, provider_ref FROM accounts WHERE institution_id NOT IN ('csv-import','demo') AND hidden = 0"
    )
    updated, errors, tx_total = 0, 0, 0
    for r in rows:
        try:
            _refresh_details(r["id"], r["provider_ref"], r["name"])
        except Exception:  # noqa: BLE001
            pass  # navn/IBAN er «best effort» – la synken avgjøre feil
        try:
            res = sync.sync_account(r["id"], force=True)
            tx_total += res.get("transactions", 0)
            updated += 1
        except Exception:  # noqa: BLE001
            errors += 1
    return {"updated": updated, "errors": errors, "transactions": tx_total}


@app.post("/api/accounts-dedupe")
def dedupe_accounts():
    """Slå sammen kontoer med samme kontonummer (IBAN eller BBAN): flytt
    transaksjonene til den beste kopien (har saldo, deretter flest transaksjoner),
    fjern dobbeltførte rader, og SLETT dublett-kontoene. Gyldige kontoer med eget
    kontonummer røres aldri; dem skjuler du med «Deaktiver»."""
    rows = db.query("SELECT id, iban, bban FROM accounts WHERE institution_id NOT IN ('csv-import','demo')")
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        no = db.account_number(r["iban"], r["bban"])
        if no:
            groups[no].append(r["id"])

    def score(aid: str):
        has_bal = 1 if db.query("SELECT 1 FROM balances WHERE account_id = ? LIMIT 1", (aid,)) else 0
        n = db.query("SELECT COUNT(*) AS n FROM transactions WHERE account_id = ?", (aid,))[0]["n"]
        return (has_bal, n)

    deleted = 0
    for no, ids in groups.items():
        if len(ids) < 2:
            continue
        keep = max(ids, key=score)
        for aid in ids:
            if aid == keep:
                continue
            # flytt transaksjonene til den vi beholder
            db.execute("UPDATE transactions SET account_id = ? WHERE account_id = ?", (keep, aid))
            db.execute("DELETE FROM balances WHERE account_id = ?", (aid,))
            db.execute("DELETE FROM accounts WHERE id = ?", (aid,))
            deleted += 1
        # fjern dobbeltførte transaksjoner på den beholdte kontoen
        db.execute(
            "DELETE FROM transactions WHERE account_id = ? AND rowid NOT IN ("
            "  SELECT MIN(rowid) FROM transactions WHERE account_id = ? "
            "  GROUP BY booking_date, amount, counterparty, remittance)",
            (keep, keep),
        )
        db.execute("UPDATE accounts SET hidden = 0 WHERE id = ?", (keep,))
    return {"deleted": deleted}


@app.post("/api/accounts-reset")
def reset_bank_accounts():
    """Nystart: slett ALLE bank-synkede kontoer + deres transaksjoner, saldo og
    samtykker. Beholder CSV-import (f.eks. Coop) og demo. Deretter kobler man til
    bankene på nytt for et rent oppsett."""
    ids = [r["id"] for r in db.query(
        "SELECT id FROM accounts WHERE institution_id NOT IN ('csv-import','demo')"
    )]
    for aid in ids:
        db.execute("DELETE FROM transactions WHERE account_id = ?", (aid,))
        db.execute("DELETE FROM balances WHERE account_id = ?", (aid,))
        db.execute("DELETE FROM accounts WHERE id = ?", (aid,))
    db.execute("DELETE FROM requisitions")
    return {"deleted": len(ids)}


@app.post("/api/transactions/{tx_id}/category")
async def set_category(tx_id: str, request: Request):
    body = await request.json()
    category = body.get("category")
    if not category:
        return JSONResponse({"error": "category mangler"}, status_code=400)
    row = db.query("SELECT counterparty FROM transactions WHERE id = ?", (tx_id,))
    db.execute(
        "UPDATE transactions SET category = ?, category_source = 'manual' WHERE id = ?",
        (category, tx_id),
    )
    learned = 0
    conflicts = 0
    if body.get("learn", True) and row:
        # Lær butikknavn -> kategori, og bruk det bare på liknende linjer (samme sted).
        cp = row[0]["counterparty"]
        categorize.learn_rule(cp, category)
        learned = categorize.apply_pattern_to_existing(cp, category)
        # Manuelt satte linjer fra samme sted røres ikke automatisk – tell dem så
        # frontenden kan spørre om de også skal oppdateres.
        conflicts = len(categorize.find_similar_manual(cp, category, exclude_id=tx_id))
    return {"ok": True, "learned": learned, "conflicts": conflicts}


@app.post("/api/transactions/{tx_id}/apply-similar")
async def apply_category_similar(tx_id: str, request: Request):
    """Sett samme kategori på ALLE linjer fra samme sted – også manuelt satte.
    Kalles kun etter at brukeren har bekreftet det i UI."""
    body = await request.json()
    category = body.get("category")
    row = db.query("SELECT counterparty FROM transactions WHERE id = ?", (tx_id,))
    if not category or not row:
        return JSONResponse({"error": "category eller transaksjon mangler"}, status_code=400)
    updated = categorize.apply_pattern_override(row[0]["counterparty"], category, exclude_id=tx_id)
    return {"ok": True, "updated": updated}


@app.post("/api/transactions/{tx_id}/label")
async def set_label(tx_id: str, request: Request):
    body = await request.json()
    lab = (body.get("label") or "").strip()
    row = db.query("SELECT id FROM transactions WHERE id = ?", (tx_id,))
    if not lab or not row:
        return JSONResponse({"error": "label eller transaksjon mangler"}, status_code=400)
    # Per-transaksjon: merker KUN denne ene (ingen auto-regel). Faste steder styres
    # med valgfrie regler i Innstillinger → Merkelapper.
    if body.get("remove"):
        labels.tx_remove_label(tx_id, lab)
    else:
        labels.tx_add_label(tx_id, lab)
    full = db.query("SELECT counterparty, remittance, labels FROM transactions WHERE id = ?", (tx_id,))
    return {"ok": True, "labels": labels.labels_for_row(full[0])}


# --- frontend ---

@app.get("/")
def index():
    # Injiser et versjonsmerke (basert på filenes endringstid) på app.js/styles.css
    # slik at nettleser/Cloudflare henter ny versjon automatisk etter en oppdatering.
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    try:
        v = int(max(
            (FRONTEND_DIR / "app.js").stat().st_mtime,
            (FRONTEND_DIR / "styles.css").stat().st_mtime,
        ))
    except OSError:
        v = 1
    html = (
        html.replace("/static/app.js", f"/static/app.js?v={v}")
        .replace("/static/styles.css", f"/static/styles.css?v={v}")
    )
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
