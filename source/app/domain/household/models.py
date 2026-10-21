"""Household-scoped entities with explicit optimistic-lock versions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_positive_int, require_status

HOUSEHOLD_STATUSES = frozenset({"ACTIVE", "SUSPENDED", "CLOSED"})
MEMBER_STATUSES = frozenset({"ACTIVE", "GUEST", "TEMPORARY", "REVOKED"})
RELATIONSHIP_STATUSES = frozenset({"ACTIVE", "EXPIRED", "REVOKED"})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise Validation(f"{field_name} is required.")


@dataclass(frozen=True, slots=True)
class Household:
    household_id: str
    name: str
    locale: str = "en-US"
    timezone: str = "UTC"
    policy_set_id: str = "default"
    status: str = "ACTIVE"
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in ((self.household_id, "household_id"), (self.name, "name")):
            _required(value, name)
        require_status(self.status, HOUSEHOLD_STATUSES)
        require_positive_int(self.version, "version")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class Member:
    member_id: str
    household_id: str
    external_ref: str
    display_name: str
    role: str
    age_band: str = "ADULT"
    presence: str = "UNKNOWN"
    privacy_default: str = "HOUSEHOLD"
    status: str = "ACTIVE"
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.member_id, "member_id"),
            (self.household_id, "household_id"),
            (self.external_ref, "external_ref"),
            (self.display_name, "display_name"),
            (self.role, "role"),
        ):
            _required(value, name)
        require_status(self.status, MEMBER_STATUSES)
        require_positive_int(self.version, "version")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class Relationship:
    relationship_id: str
    household_id: str
    from_member_id: str
    to_member_id: str
    type: str
    delegation_refs: tuple[str, ...] = ()
    status: str = "ACTIVE"
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for value, name in (
            (self.relationship_id, "relationship_id"),
            (self.household_id, "household_id"),
            (self.from_member_id, "from_member_id"),
            (self.to_member_id, "to_member_id"),
            (self.type, "type"),
        ):
            _required(value, name)
        if self.from_member_id == self.to_member_id:
            raise Validation("A relationship must connect two different members.")
        require_status(self.status, RELATIONSHIP_STATUSES)
        require_positive_int(self.version, "version")
        require_aware_datetime(self.created_at, "created_at")
        require_aware_datetime(self.updated_at, "updated_at")
