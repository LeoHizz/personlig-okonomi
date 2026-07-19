"""Labels/merkelapper – en dimensjon på tvers av kategoriene (Hytte, Ferie …).

En transaksjon kan ha flere labels. Labels utledes fra regler (mønster -> label)
som lagres i settings ('label_rules'), på samme måte som kategori-reglene. Å merke
én transaksjon lager en regel, så samme sted merkes automatisk framover.
"""
from __future__ import annotations

from . import db

DEFAULT_LABELS = ["Hytte", "Hjemme", "Ferie", "Jobb"]


def label_rules() -> list[dict]:
    return db.get_setting("label_rules", []) or []


def all_labels() -> list[str]:
    seen = list(DEFAULT_LABELS)
    for r in label_rules():
        lab = r.get("label")
        if lab and lab not in seen:
            seen.append(lab)
    return seen


def labels_for(counterparty: str | None, remittance: str | None) -> list[str]:
    text = f"{counterparty or ''} {remittance or ''}".lower()
    out: list[str] = []
    for r in label_rules():
        pat = (r.get("pattern") or "").strip().lower()
        lab = r.get("label")
        if pat and lab and pat in text and lab not in out:
            out.append(lab)
    return out


def learn_label_rule(counterparty: str | None, label: str) -> None:
    pattern = " ".join((counterparty or "").split()).lower()
    if not pattern or not label:
        return
    rules = db.get_setting("label_rules", []) or []
    for r in rules:
        if (r.get("pattern") or "").strip().lower() == pattern and r.get("label") == label:
            return
    rules.insert(0, {"pattern": pattern, "label": label})
    db.set_setting("label_rules", rules)


def remove_label_rule(counterparty: str | None, label: str) -> None:
    pattern = " ".join((counterparty or "").split()).lower()
    rules = db.get_setting("label_rules", []) or []
    new = [
        r for r in rules
        if not ((r.get("pattern") or "").strip().lower() == pattern and r.get("label") == label)
    ]
    if len(new) != len(rules):
        db.set_setting("label_rules", new)
