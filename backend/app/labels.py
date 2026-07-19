"""Labels/merkelapper – en dimensjon på tvers av kategoriene (Hytte, Ferie …).

To kilder til en transaksjons merkelapper:
  1. Per-transaksjon: merker DU direkte i transaksjonslista → lagres på selve
     transaksjonen (kolonnen `labels`, JSON-liste). Gjelder kun den ene.
  2. Regler (valgfritt): mønster -> label i settings ('label_rules'), for dem
     som vil auto-merke et fast sted. Opprettes manuelt i Innstillinger.

I tillegg kan brukeren lage egne merkelapp-navn ('custom_labels' i settings).
"""
from __future__ import annotations

import json

from . import db

DEFAULT_LABELS = ["Hytte", "Hjemme", "Ferie", "Jobb"]


def label_rules() -> list[dict]:
    return db.get_setting("label_rules", []) or []


def custom_labels() -> list[str]:
    return db.get_setting("custom_labels", []) or []


def all_labels() -> list[str]:
    seen = list(DEFAULT_LABELS)
    for lab in custom_labels():
        if lab and lab not in seen:
            seen.append(lab)
    for r in label_rules():
        lab = r.get("label")
        if lab and lab not in seen:
            seen.append(lab)
    return seen


# --- egne merkelapper ---

def add_custom_label(name: str) -> None:
    name = (name or "").strip()
    if not name or name in DEFAULT_LABELS:
        return
    labs = db.get_setting("custom_labels", []) or []
    if name not in labs:
        labs.append(name)
        db.set_setting("custom_labels", labs)


def remove_custom_label(name: str) -> None:
    labs = db.get_setting("custom_labels", []) or []
    new = [l for l in labs if l != name]
    if len(new) != len(labs):
        db.set_setting("custom_labels", new)


# --- regel-baserte (valgfritt) ---

def _rule_labels(counterparty: str | None, remittance: str | None) -> list[str]:
    text = f"{counterparty or ''} {remittance or ''}".lower()
    out: list[str] = []
    for r in label_rules():
        pat = (r.get("pattern") or "").strip().lower()
        lab = r.get("label")
        if pat and lab and pat in text and lab not in out:
            out.append(lab)
    return out


def labels_for(counterparty: str | None, remittance: str | None) -> list[str]:
    """Kun regel-baserte (bakoverkompatibel)."""
    return _rule_labels(counterparty, remittance)


# --- per-transaksjon ---

def _parse_own(raw) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _row_get(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def labels_for_row(row) -> list[str]:
    """Alle merkelapper for en transaksjonsrad: egne (per-tx) + regel-baserte."""
    out = list(_parse_own(_row_get(row, "labels")))
    for lab in _rule_labels(_row_get(row, "counterparty"), _row_get(row, "remittance")):
        if lab not in out:
            out.append(lab)
    return out


def tx_add_label(tx_id: str, label: str) -> list[str]:
    label = (label or "").strip()
    row = db.query("SELECT labels FROM transactions WHERE id = ?", (tx_id,))
    if not row or not label:
        return []
    own = _parse_own(row[0]["labels"])
    if label not in own:
        own.append(label)
    db.execute("UPDATE transactions SET labels = ? WHERE id = ?",
               (json.dumps(own, ensure_ascii=False), tx_id))
    return own


def tx_remove_label(tx_id: str, label: str) -> list[str]:
    row = db.query("SELECT labels FROM transactions WHERE id = ?", (tx_id,))
    if not row:
        return []
    own = [l for l in _parse_own(row[0]["labels"]) if l != label]
    db.execute("UPDATE transactions SET labels = ? WHERE id = ?",
               (json.dumps(own, ensure_ascii=False), tx_id))
    return own
