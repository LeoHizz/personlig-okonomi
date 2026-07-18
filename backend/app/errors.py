"""Felles feiltype for bankdata-leverandører."""
from __future__ import annotations

from typing import Any


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail
