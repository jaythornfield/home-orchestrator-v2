"""Trusted internal command identity and canonical payload contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping

from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_positive_int


_CALLER_FIELDS: dict[str, frozenset[str]] = {
    "create_household": frozenset(
        {"household_id", "name", "locale", "timezone", "policy_set_id", "status"}
    ),
    "update_household": frozenset({"name", "locale", "timezone", "policy_set_id", "status"}),
    "add_member": frozenset(
        {
            "member_id",
            "household_id",
            "external_ref",
            "display_name",
            "role",
            "age_band",
            "presence",
            "privacy_default",
            "status",
        }
    ),
    "update_member": frozenset({"member_id", "changes"}),
    "add_relationship": frozenset(
        {
            "relationship_id",
            "household_id",
            "from_member_id",
            "to_member_id",
            "type",
            "delegation_refs",
            "status",
        }
    ),
    "update_relationship": frozenset({"relationship_id", "changes"}),
    "append_observation": frozenset(
        {
            "observation_id",
            "household_id",
            "subject_ids",
            "source_type",
            "source_ref",
            "facts",
            "observed_at",
            "confidence",
            "privacy_scope",
            "expires_at",
            "status",
        }
    ),
    "store_memory": frozenset(
        {
            "memory_id",
            "household_id",
            "subject_id",
            "memory_type",
            "memory_key",
            "value",
            "source_type",
            "source_ref",
            "confidence",
            "scope",
            "privacy_level",
            "valid_from",
            "expires_at",
            "evidence_refs",
            "evidence_count",
            "worth_remembering",
        }
    ),
    "revoke_memory": frozenset({"memory_id"}),
    "expire_memory": frozenset({"memory_id", "as_of"}),
}

_SERVER_FIELDS: dict[str, frozenset[str]] = {
    "create_household": frozenset({"version", "created_at", "updated_at"}),
    "add_member": frozenset({"version", "created_at", "updated_at"}),
    "add_relationship": frozenset({"version", "created_at", "updated_at"}),
    "append_observation": frozenset({"received_at", "correlation_id"}),
}


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Unsupported canonical payload type: {type(value).__name__}")


def canonical_payload_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def canonical_payload_for_replay(command_type: str, caller_payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic, caller-semantic payload used for replay identity.

    Runtime object identity, server timestamps, generated correlations, and other
    derived fields are deliberately excluded by the per-command allowlists.
    """
    if command_type not in _CALLER_FIELDS:
        raise Validation("The command route does not define a replay contract.")
    if not isinstance(caller_payload, Mapping):
        raise Validation("The command payload must be an object.")
    caller_fields = _CALLER_FIELDS[command_type]
    server_fields = _SERVER_FIELDS.get(command_type, frozenset())
    unknown = set(caller_payload) - caller_fields - server_fields
    if unknown:
        raise Validation("The command payload contains unsupported fields.")
    return {field: caller_payload[field] for field in sorted(caller_fields) if field in caller_payload}


def replay_payload_hash(command_type: str, caller_payload: Mapping[str, Any]) -> str:
    return canonical_payload_hash(canonical_payload_for_replay(command_type, caller_payload))


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    command_id: str
    principal_id: str
    household_id: str
    correlation_id: str
    tool_or_route: str
    canonical_payload_hash: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expected_version: int | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.command_id, "command_id"),
            (self.principal_id, "principal_id"),
            (self.household_id, "household_id"),
            (self.correlation_id, "correlation_id"),
            (self.tool_or_route, "tool_or_route"),
            (self.canonical_payload_hash, "canonical_payload_hash"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise Validation(f"{name} is required.")
        require_aware_datetime(self.received_at, "received_at")
        if self.expected_version is not None:
            require_positive_int(self.expected_version, "expected_version")

    @classmethod
    def from_payload(
        cls,
        *,
        command_id: str,
        principal_id: str,
        household_id: str,
        correlation_id: str,
        tool_or_route: str,
        payload: Any,
        expected_version: int | None = None,
    ) -> "CommandEnvelope":
        return cls(
            command_id=command_id,
            principal_id=principal_id,
            household_id=household_id,
            correlation_id=correlation_id,
            tool_or_route=tool_or_route,
            canonical_payload_hash=replay_payload_hash(tool_or_route, payload),
            expected_version=expected_version,
        )
