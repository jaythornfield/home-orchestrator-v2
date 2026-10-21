"""Governed long-term memory contracts for Stage B."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_confidence, require_positive_int, require_status

MEMORY_TYPES = frozenset(
    {"IDENTITY", "PREFERENCE", "ROUTINE", "EPISODIC", "GOAL", "AUTHORIZATION", "SENSITIVE_CONTEXT"}
)
MEMORY_STATUSES = frozenset({"ACTIVE", "UNCERTAIN", "SUPERSEDED", "EXPIRED", "REVOKED"})
PRIVACY_SCOPES = frozenset({"PRIVATE", "HOUSEHOLD_SHARED", "SUBJECT_VISIBLE"})
PRIVACY_LEVELS = frozenset({"STANDARD", "SENSITIVE"})
ROUTINE_MIN_INDEPENDENT_EVIDENCE = 3
SENSITIVE_CONTEXT_MAX_TTL = timedelta(days=30)
SENSITIVE_CONTEXT_MIN_SOURCE_RANK = 500
AUTHORIZATION_TRUSTED_SOURCES = frozenset(
    {"MEMBER_STATEMENT", "EXPLICIT_AUTHORIZATION", "HOUSEHOLD_RULE", "MEMBER_CORRECTION"}
)
MEMORY_SOURCE_RANKS = {
    "MEMBER_CORRECTION": 700,
    "HOUSEHOLD_RULE": 600,
    "EXPLICIT_AUTHORIZATION": 600,
    "MEMBER_STATEMENT": 500,
    "VERIFIED_OBSERVATION": 400,
    "REPEATED_BEHAVIOR": 300,
    "SINGLE_OBSERVATION": 200,
    "MODEL_INFERENCE": 100,
    "V1_IMPORT": 50,
}
MEMORY_GATE_ACTIONS = frozenset(
    {"STORE", "UPDATE_BY_SUPERSEDE", "REJECT", "MARK_UNCERTAIN", "REVOKE", "EXPIRE"}
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise Validation(f"{field_name} is required.")


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_id: object
    household_id: object
    subject_id: object
    memory_type: object
    memory_key: object
    value: Any
    source_type: object
    source_ref: object
    confidence: object
    scope: object
    privacy_level: object
    valid_from: object
    expires_at: object = None
    evidence_refs: object = ()
    evidence_count: object = 1
    worth_remembering: object = True


@dataclass(frozen=True, slots=True)
class MemoryGateDecision:
    action: str
    target_status: str | None
    source_rank: int | None
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        require_status(self.action, MEMORY_GATE_ACTIONS, "action")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    household_id: str
    subject_id: str
    memory_type: str
    memory_key: str
    value: Any
    source_type: str
    source_ref: str
    source_rank: int
    confidence: float
    scope: str
    privacy_level: str
    valid_from: datetime
    expires_at: datetime | None = None
    supersedes_id: str | None = None
    status: str = "ACTIVE"
    version: int = 1
    evidence_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.memory_id, "memory_id"),
            (self.household_id, "household_id"),
            (self.subject_id, "subject_id"),
            (self.memory_type, "memory_type"),
            (self.memory_key, "memory_key"),
            (self.source_type, "source_type"),
            (self.source_ref, "source_ref"),
            (self.scope, "scope"),
            (self.privacy_level, "privacy_level"),
        ):
            _required(value, name)
        require_status(self.memory_type, MEMORY_TYPES, "memory_type")
        require_status(self.source_type, MEMORY_SOURCE_RANKS, "source_type")
        require_status(self.scope, PRIVACY_SCOPES, "scope")
        require_status(self.privacy_level, PRIVACY_LEVELS, "privacy_level")
        expected_privacy_level = "SENSITIVE" if self.memory_type == "SENSITIVE_CONTEXT" else "STANDARD"
        if self.privacy_level != expected_privacy_level:
            raise Validation("privacy_level does not match memory_type.")
        require_status(self.status, MEMORY_STATUSES)
        require_positive_int(self.source_rank, "source_rank")
        if self.source_rank != MEMORY_SOURCE_RANKS[self.source_type]:
            raise Validation("source_rank does not match source_type.")
        require_confidence(self.confidence)
        require_positive_int(self.version, "version")
        require_aware_datetime(self.valid_from, "valid_from")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")
        if self.expires_at is not None:
            require_aware_datetime(self.expires_at, "expires_at")
            if self.expires_at <= self.valid_from:
                raise Validation("expires_at must be later than valid_from.")
        if not isinstance(self.evidence_refs, (tuple, list)) or any(
            not isinstance(ref, str) or not ref.strip() for ref in self.evidence_refs
        ):
            raise Validation("evidence_refs must contain only non-empty string IDs.")
        object.__setattr__(self, "evidence_refs", tuple(dict.fromkeys(ref.strip() for ref in self.evidence_refs)))
        if self.supersedes_id is not None and (
            not isinstance(self.supersedes_id, str) or not self.supersedes_id.strip()
        ):
            raise Validation("supersedes_id must be a non-empty string ID.")
        if self.supersedes_id == self.memory_id:
            raise Validation("A memory cannot supersede itself.")
        if self.memory_type == "SENSITIVE_CONTEXT":
            if self.scope != "PRIVATE" or self.privacy_level != "SENSITIVE":
                raise Validation("Sensitive Context must be private and sensitive.")
            if self.expires_at is None:
                raise Validation("Sensitive Context requires expiry.")
            if self.expires_at - self.valid_from > SENSITIVE_CONTEXT_MAX_TTL:
                raise Validation("Sensitive Context expiry exceeds policy.")
            if self.source_rank < SENSITIVE_CONTEXT_MIN_SOURCE_RANK:
                raise Validation("Sensitive Context source is below policy.")
        if self.memory_type == "AUTHORIZATION" and self.source_type not in AUTHORIZATION_TRUSTED_SOURCES:
            raise Validation("Authorization Memory requires a trusted explicit source.")
