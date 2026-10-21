"""Immutable source observations; no Situation or Need inference lives here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_confidence, require_status

ALLOWED_CLOCK_SKEW = timedelta(minutes=5)
OBSERVATION_STATUSES = frozenset({"ACTIVE", "SUPERSEDED", "EXPIRED", "REVOKED"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    household_id: str
    subject_ids: tuple[str, ...]
    source_type: str
    source_ref: str
    facts: dict[str, Any]
    observed_at: datetime
    received_at: datetime = field(default_factory=utc_now)
    confidence: float = 1.0
    privacy_scope: str = "HOUSEHOLD"
    expires_at: datetime | None = None
    status: str = "ACTIVE"
    correlation_id: str = ""

    def __post_init__(self) -> None:
        required = (
            (self.observation_id, "observation_id"),
            (self.household_id, "household_id"),
            (self.source_type, "source_type"),
            (self.source_ref, "source_ref"),
            (self.correlation_id, "correlation_id"),
        )
        for value, name in required:
            if not value or not value.strip():
                raise Validation(f"{name} is required.")
        if not self.subject_ids:
            raise Validation("subject_ids must contain at least one member.")
        require_confidence(self.confidence)
        require_status(self.status, OBSERVATION_STATUSES)
        require_aware_datetime(self.observed_at, "observed_at")
        require_aware_datetime(self.received_at, "received_at")
        if self.expires_at is not None:
            require_aware_datetime(self.expires_at, "expires_at")
            if self.expires_at <= self.observed_at:
                raise Validation("expires_at must be later than observed_at.")
        if self.observed_at > self.received_at + ALLOWED_CLOCK_SKEW:
            raise Validation("observed_at exceeds the allowed clock skew.")
