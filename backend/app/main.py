"""FastAPI-app: API mot GoCardless + servering av dashboardet."""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import aggregate, categorize, config, db, provider as gc, importer, sync

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


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
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
    }


@app.get("/api/dashboard")
def dashboard(month: str | None = None):
    return aggregate.build_dashboard(month)


@app.get("/api/transactions")
def transactions(month: str | None = None, person: str | None = None,
                 category: str | None = None, q: str | None = None):
    return aggregate.build_transactions(month, person, category, q)


@app.get("/api/budget")
def budget(year: int | None = None):
    return aggregate.build_budget_matrix(year)


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
        "categories": categorize.CATEGORY_ORDER,
        "accounts": [dict(r) for r in db.query("SELECT * FROM accounts ORDER BY sort_order, name")],
    }


@app.post("/api/settings")
async def save_settings(request: Request):
    body = await request.json()
    for key in ("household_name", "savings_goal_pct", "budgets",
                "manual_assets", "manual_liabilities", "category_rules"):
        if key in body:
            db.set_setting(key, body[key])
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


@app.post("/api/transactions/{tx_id}/category")
async def set_category(tx_id: str, request: Request):
    body = await request.json()
    category = body.get("category")
    if not category:
        return JSONResponse({"error": "category mangler"}, status_code=400)
    db.execute(
        "UPDATE transactions SET category = ?, category_source = 'manual' WHERE id = ?",
        (category, tx_id),
    )
    return {"ok": True}


# --- frontend ---

@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
