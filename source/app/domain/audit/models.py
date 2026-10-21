"""Audit event recorded atomically with each Stage A mutation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_positive_int


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    household_id: str
    actor_id: str
    event_type: str
    entity_type: str
    entity_id: str
    entity_version: int
    correlation_id: str
    causation_id: str | None
    payload_hash: str
    evidence_refs: tuple[str, ...]
    result: str
    occurred_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_id, "event_id"),
            (self.household_id, "household_id"),
            (self.actor_id, "actor_id"),
            (self.event_type, "event_type"),
            (self.entity_type, "entity_type"),
            (self.entity_id, "entity_id"),
            (self.correlation_id, "correlation_id"),
            (self.payload_hash, "payload_hash"),
            (self.result, "result"),
        ):
            if not value or not value.strip():
                raise Validation(f"{name} is required.")
        require_positive_int(self.entity_version, "entity_version")
        require_aware_datetime(self.occurred_at, "occurred_at")
