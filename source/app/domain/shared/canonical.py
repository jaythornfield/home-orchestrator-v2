"""Shared canonical value normalization for domain-level semantic bindings."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime


def primitive(value):
    """Return the stable, JSON-compatible form used by semantic digests."""
    if is_dataclass(value):
        return primitive(asdict(value))
    if isinstance(value, datetime):
        return require_aware_datetime(value, "time").astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise Validation()
        return {key: primitive(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [primitive(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise Validation()


def digest(value):
    """Compute the existing upper-case SHA-256 semantic digest contract."""
    payload = json.dumps(primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest().upper()
