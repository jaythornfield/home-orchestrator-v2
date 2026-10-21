"""Small, strict validators shared by Stage A domain boundaries."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Collection

from .errors import Validation


def require_positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise Validation(f"{field_name} must be a positive integer.")
    return value


def require_aware_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise Validation(f"{field_name} must be timezone-aware.")
    return value


def require_status(value: str, allowed: Collection[str], field_name: str = "status") -> str:
    if value not in allowed:
        raise Validation(f"{field_name} is not a supported lifecycle value.")
    return value


def require_confidence(value: object) -> float | int:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise Validation("confidence must be a finite number between 0 and 1.")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise Validation("confidence must be a finite number between 0 and 1.")
    return value
