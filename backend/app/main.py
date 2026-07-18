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


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    _migrate_categories()
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


@app.get("/api/transactions")
def transactions(month: str | None = None, persons: str | None = None,
                 category: str | None = None, q: str | None = None,
                 period: str | None = None, label: str | None = None):
    return aggregate.build_transactions(month, persons, category, q, period, label)


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
        try:
            sync.sync_all(force=True)
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
        "labels": labels.all_labels(),
        "categories": categorize.CATEGORY_ORDER,
        "accounts": [dict(r) for r in db.query("SELECT * FROM accounts ORDER BY sort_order, name")],
    }


@app.post("/api/settings")
async def save_settings(request: Request):
    body = await request.json()
    for key in ("household_name", "savings_goal_pct", "budgets",
                "manual_assets", "manual_liabilities", "category_rules", "label_rules"):
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
    allowed = {"name", "owner", "bank_code", "is_asset", "hidden", "sort_order"}
    fields = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        return JSONResponse({"error": "ingen gyldige felt"}, status_code=400)
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE accounts SET {sets} WHERE id = ?", [*fields.values(), account_id])
    return {"ok": True}


@app.post("/api/accounts/{account_id}/refresh")
def refresh_account(account_id: str):
    """Hent kontonavn/IBAN/produkt fra banken igjen (letter identifisering)."""
    try:
        d = gc.get_account_details(account_id)
    except gc.Error as e:
        return JSONResponse({"error": str(e), "detail": e.detail}, status_code=e.status or 500)
    updates = {"iban": d.get("iban", ""), "product": d.get("product", "")}
    cur = db.query("SELECT name FROM accounts WHERE id = ?", (account_id,))
    if cur and (not cur[0]["name"] or cur[0]["name"] in ("Konto", "")):
        updates["name"] = d.get("name", "Konto")
    sets = ", ".join(f"{k} = ?" for k in updates)
    db.execute(f"UPDATE accounts SET {sets} WHERE id = ?", [*updates.values(), account_id])
    return {"ok": True, "bankName": d.get("name", ""), "iban": d.get("iban", ""), "product": d.get("product", "")}


@app.post("/api/accounts/refresh-all")
def refresh_all_accounts():
    """Hent navn/IBAN/produkt for alle tilkoblede kontoer (slipper å klikke hver)."""
    rows = db.query(
        "SELECT id, name FROM accounts WHERE institution_id NOT IN ('csv-import','demo') AND hidden = 0"
    )
    updated, errors = 0, 0
    for r in rows:
        try:
            d = gc.get_account_details(r["id"])
            fields = {"iban": d.get("iban", ""), "product": d.get("product", "")}
            if not r["name"] or r["name"] in ("Konto", ""):
                fields["name"] = d.get("name", "Konto")
            sets = ", ".join(f"{k} = ?" for k in fields)
            db.execute(f"UPDATE accounts SET {sets} WHERE id = ?", [*fields.values(), r["id"]])
            updated += 1
        except Exception:  # noqa: BLE001
            errors += 1
    return {"updated": updated, "errors": errors}


@app.post("/api/accounts/dedupe")
def dedupe_accounts():
    """Finn kontoer med samme kontonummer (IBAN) og deaktiver dublettene –
    beholder den med flest transaksjoner aktiv. Fjerner også dobbelttelling."""
    rows = db.query("SELECT id, iban FROM accounts WHERE iban IS NOT NULL AND iban != '' AND hidden = 0")
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r["iban"]].append(r["id"])
    hidden = 0
    for iban, ids in groups.items():
        if len(ids) < 2:
            continue
        counts = {
            aid: db.query("SELECT COUNT(*) AS n FROM transactions WHERE account_id = ?", (aid,))[0]["n"]
            for aid in ids
        }
        keep = max(counts, key=counts.get)
        for aid in ids:
            if aid != keep:
                db.execute("UPDATE accounts SET hidden = 1 WHERE id = ?", (aid,))
                hidden += 1
    return {"hidden": hidden}


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
    if body.get("learn", True) and row:
        # Lær butikknavn -> kategori, og bruk det bare på liknende linjer (samme sted).
        cp = row[0]["counterparty"]
        categorize.learn_rule(cp, category)
        learned = categorize.apply_pattern_to_existing(cp, category)
    return {"ok": True, "learned": learned}


@app.post("/api/transactions/{tx_id}/label")
async def set_label(tx_id: str, request: Request):
    body = await request.json()
    lab = (body.get("label") or "").strip()
    row = db.query("SELECT counterparty FROM transactions WHERE id = ?", (tx_id,))
    if not lab or not row:
        return JSONResponse({"error": "label eller transaksjon mangler"}, status_code=400)
    # Å merke en transaksjon lager/fjerner en label-regel for samme sted.
    if body.get("remove"):
        labels.remove_label_rule(row[0]["counterparty"], lab)
    else:
        labels.learn_label_rule(row[0]["counterparty"], lab)
    return {"ok": True, "labels": labels.labels_for(row[0]["counterparty"], "")}


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
