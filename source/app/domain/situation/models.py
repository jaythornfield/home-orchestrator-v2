"""Immutable, versioned interpretations of a household context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_confidence, require_positive_int, require_status

SITUATION_STATUSES = frozenset({"CURRENT", "SUPERSEDED", "CLOSED"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SituationDiff:
    added_evidence: tuple[str, ...] = ()
    removed_or_invalid_evidence: tuple[str, ...] = ()
    changed_context_fields: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SituationRecord:
    situation_id: str
    household_id: str
    version: int
    as_of: datetime
    participant_ids: tuple[str, ...]
    presence_snapshot: dict[str, str]
    time_context: dict[str, str]
    observation_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    active_context_refs: tuple[str, ...]
    summary: str
    uncertainty: tuple[str, ...]
    confidence: float
    input_hash: str
    diff: SituationDiff
    status: str = "CURRENT"
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.situation_id, "situation_id"),
            (self.household_id, "household_id"),
            (self.summary, "summary"),
            (self.input_hash, "input_hash"),
        ):
            if not value or not value.strip():
                raise Validation(f"{name} is required.")
        require_positive_int(self.version, "version")
        require_aware_datetime(self.as_of, "as_of")
        require_aware_datetime(self.created_at, "created_at")
        require_confidence(self.confidence)
        require_status(self.status, SITUATION_STATUSES)
