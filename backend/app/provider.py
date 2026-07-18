"""Velger bankdata-leverandør ut fra PROVIDER i .env, og videreformidler
det normaliserte grensesnittet (list_institutions, start_authorization,
finalize_authorization, get_balances, get_transactions, get_account_details,
utc_now_iso, Error).
"""
from __future__ import annotations

from . import config

if config.PROVIDER == "gocardless":
    from . import gocardless as _impl
else:
    from . import enablebanking as _impl


def __getattr__(name):  # PEP 562 – videreformidle alle attributter til valgt modul
    return getattr(_impl, name)


def name() -> str:
    return config.PROVIDER
