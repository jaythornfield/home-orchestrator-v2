"""Need hypotheses remain explicitly uncertain and evidence-backed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_confidence, require_positive_int, require_status

NEED_STATUSES = frozenset({"CANDIDATE", "ACCEPTED", "REJECTED", "SUPERSEDED"})


@dataclass(frozen=True, slots=True)
class NeedHypothesis:
    need_id: str
    household_id: str
    situation_id: str
    subject_ids: tuple[str, ...]
    need_type: str
    statement: str
    evidence_ids: tuple[str, ...]
    confidence: float
    impact: str
    risk_if_wrong: str
    conflict_status: str
    status: str = "CANDIDATE"
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for value, name in (
            (self.need_id, "need_id"),
            (self.household_id, "household_id"),
            (self.situation_id, "situation_id"),
            (self.need_type, "need_type"),
            (self.statement, "statement"),
            (self.impact, "impact"),
            (self.risk_if_wrong, "risk_if_wrong"),
            (self.conflict_status, "conflict_status"),
        ):
            if not value or not value.strip():
                raise Validation(f"{name} is required.")
        if not self.subject_ids or not self.evidence_ids:
            raise Validation("Need hypotheses require subjects and evidence.")
        require_confidence(self.confidence)
        require_positive_int(self.version, "version")
        require_aware_datetime(self.created_at, "created_at")
        require_status(self.status, NEED_STATUSES)
