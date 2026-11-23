"""The one error the apps package raises for things it cannot accept or find. Safe to show."""
from __future__ import annotations


class AppError(ValueError):
    """Something the apps cannot accept or find. Safe to show."""
