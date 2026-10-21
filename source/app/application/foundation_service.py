"""Stage A mutations: state, audit, outbox, and replay commit together."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable
from uuid import uuid4

from app.application.command_envelope import CommandEnvelope, replay_payload_hash
from app.application.unit_of_work import UnitOfWork
from app.domain.audit import AuditEvent
from app.domain.household import Household, Member, Relationship
from app.domain.observation import Observation
from app.domain.shared.errors import Conflict, DomainError, NotFound, PersistenceError, Validation

FaultInjector = Callable[[str], None]


def _no_fault(_: str) -> None:
    return None


def _application_boundary(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Keep every public mutation, including conversion and hashing, behind one safe boundary."""

    @wraps(method)
    def wrapped(
        self: "FoundationService", envelope: CommandEnvelope, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            return method(self, envelope, *args, **kwargs)
        except DomainError as error:
            if error.correlation_id is None:
                error.correlation_id = envelope.correlation_id
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error

    return wrapped


class FoundationService:
    def __init__(self, uow_factory: Callable[[], UnitOfWork], *, fault_injector: FaultInjector = _no_fault) -> None:
        self._uow_factory = uow_factory
        self._fault = fault_injector

    @staticmethod
    def _expected(envelope: CommandEnvelope) -> int:
        if envelope.expected_version is None:
            raise Validation("expected_version is required for an update.", correlation_id=envelope.correlation_id)
        return envelope.expected_version

    @staticmethod
    def _assert_scope(envelope: CommandEnvelope, entity_household_id: str) -> None:
        if envelope.household_id != entity_household_id:
            from app.domain.shared.errors import TenantScopeViolation

            raise TenantScopeViolation(correlation_id=envelope.correlation_id)

    @staticmethod
    def _assert_route(envelope: CommandEnvelope, expected_route: str) -> None:
        if envelope.tool_or_route != expected_route:
            raise Validation("The command route does not match the requested operation.", correlation_id=envelope.correlation_id)

    @staticmethod
    def _assert_payload(envelope: CommandEnvelope, payload: Any) -> None:
        if envelope.canonical_payload_hash != replay_payload_hash(envelope.tool_or_route, payload):
            raise Conflict("The command payload does not match its envelope.", correlation_id=envelope.correlation_id)

    @staticmethod
    def _replay(
        uow: UnitOfWork,
        envelope: CommandEnvelope,
        *,
        command_type: str,
        pre_state_version: int,
    ) -> dict[str, Any] | None:
        record = uow.replays.get(envelope.household_id, envelope.command_id)
        if record is None:
            return None
        same_identity = (
            record["principal_id"] == envelope.principal_id
            and record["command_type"] == command_type
            and record["canonical_payload_hash"] == envelope.canonical_payload_hash
            and record["pre_state_version"] == pre_state_version
            and record["operation_epoch"] == f"v{pre_state_version}"
        )
        if not same_identity:
            raise Conflict("A command ID cannot be reused with different content.", correlation_id=envelope.correlation_id)
        return record["stable_result"]

    def _finish(
        self,
        uow: UnitOfWork,
        envelope: CommandEnvelope,
        *,
        command_type: str,
        entity_type: str,
        entity_id: str,
        entity_version: int,
        pre_state_version: int,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        result = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "version": entity_version,
            "correlation_id": envelope.correlation_id,
        }
        audit = AuditEvent(
            event_id=f"audit_{uuid4().hex}",
            household_id=envelope.household_id,
            actor_id=envelope.principal_id,
            event_type=command_type,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_version=entity_version,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.command_id,
            payload_hash=envelope.canonical_payload_hash,
            evidence_refs=(),
            result="committed",
            occurred_at=now,
        )
        uow.audit.append(audit)
        self._fault("after_audit")
        uow.outbox.append(
            {
                "outbox_event_id": f"outbox_{uuid4().hex}",
                "household_id": envelope.household_id,
                "event_type": command_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "entity_version": entity_version,
                "payload": result,
                "status": "PENDING",
                "correlation_id": envelope.correlation_id,
                "occurred_at": now.isoformat(),
                "created_at": now.isoformat(),
            }
        )
        self._fault("after_outbox")
        uow.replays.add(
            {
                "replay_id": f"replay_{uuid4().hex}",
                "household_id": envelope.household_id,
                "command_id": envelope.command_id,
                "principal_id": envelope.principal_id,
                "command_type": command_type,
                "canonical_payload_hash": envelope.canonical_payload_hash,
                "pre_state_version": pre_state_version,
                "operation_epoch": f"v{pre_state_version}",
                "stable_result": result,
                "status": "STABLE",
                "correlation_id": envelope.correlation_id,
                "created_at": now.isoformat(),
            }
        )
        self._fault("after_replay")
        self._fault("before_commit")
        uow.commit()
        return result

    @_application_boundary
    def create_household(self, envelope: CommandEnvelope, household: Household) -> dict[str, Any]:
        self._assert_route(envelope, "create_household")
        self._assert_scope(envelope, household.household_id)
        self._assert_payload(envelope, asdict(household))
        try:
            with self._uow_factory() as uow:
                replay = self._replay(uow, envelope, command_type="create_household", pre_state_version=0)
                if replay is not None:
                    return replay
                if uow.households.get(household.household_id) is not None:
                    raise Conflict(correlation_id=envelope.correlation_id)
                self._fault("before_business_state")
                uow.households.add(household)
                self._fault("after_business_state")
                return self._finish(
                    uow,
                    envelope,
                    command_type="create_household",
                    entity_type="household",
                    entity_id=household.household_id,
                    entity_version=household.version,
                    pre_state_version=0,
                )
        except DomainError:
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error

    @_application_boundary
    def update_household(self, envelope: CommandEnvelope, changes: dict[str, Any]) -> dict[str, Any]:
        self._assert_route(envelope, "update_household")
        expected = self._expected(envelope)
        self._assert_payload(envelope, changes)
        allowed = {"name", "locale", "timezone", "policy_set_id", "status"}
        if not changes or not set(changes) <= allowed:
            raise Validation("Unsupported household changes.", correlation_id=envelope.correlation_id)
        try:
            with self._uow_factory() as uow:
                replay = self._replay(uow, envelope, command_type="update_household", pre_state_version=expected)
                if replay is not None:
                    return replay
                current = uow.households.get(envelope.household_id)
                if current is None:
                    raise NotFound(correlation_id=envelope.correlation_id)
                updated = replace(
                    current,
                    **changes,
                    version=expected + 1,
                    updated_at=datetime.now(timezone.utc),
                )
                self._fault("before_business_state")
                uow.households.update(updated, expected)
                self._fault("after_business_state")
                return self._finish(
                    uow,
                    envelope,
                    command_type="update_household",
                    entity_type="household",
                    entity_id=current.household_id,
                    entity_version=updated.version,
                    pre_state_version=expected,
                )
        except DomainError:
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error

    @_application_boundary
    def add_member(self, envelope: CommandEnvelope, member: Member) -> dict[str, Any]:
        self._assert_route(envelope, "add_member")
        self._assert_scope(envelope, member.household_id)
        self._assert_payload(envelope, asdict(member))
        try:
            with self._uow_factory() as uow:
                replay = self._replay(uow, envelope, command_type="add_member", pre_state_version=0)
                if replay is not None:
                    return replay
                if uow.households.get(envelope.household_id) is None:
                    raise NotFound(correlation_id=envelope.correlation_id)
                if uow.members.get(envelope.household_id, member.member_id) is not None:
                    raise Conflict(correlation_id=envelope.correlation_id)
                self._fault("before_business_state")
                uow.members.add(member)
                self._fault("after_business_state")
                return self._finish(
                    uow,
                    envelope,
                    command_type="add_member",
                    entity_type="member",
                    entity_id=member.member_id,
                    entity_version=member.version,
                    pre_state_version=0,
                )
        except DomainError:
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error

    @_application_boundary
    def update_member(self, envelope: CommandEnvelope, member_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self._assert_route(envelope, "update_member")
        expected = self._expected(envelope)
        payload = {"member_id": member_id, "changes": changes}
        self._assert_payload(envelope, payload)
        allowed = {"external_ref", "display_name", "role", "age_band", "presence", "privacy_default", "status"}
        if not changes or not set(changes) <= allowed:
            raise Validation("Unsupported member changes.", correlation_id=envelope.correlation_id)
        try:
            with self._uow_factory() as uow:
                replay = self._replay(uow, envelope, command_type="update_member", pre_state_version=expected)
                if replay is not None:
                    return replay
                current = uow.members.get(envelope.household_id, member_id)
                if current is None:
                    raise NotFound(correlation_id=envelope.correlation_id)
                updated = replace(
                    current,
                    **changes,
                    version=expected + 1,
                    updated_at=datetime.now(timezone.utc),
                )
                self._fault("before_business_state")
                uow.members.update(updated, expected)
                self._fault("after_business_state")
                return self._finish(
                    uow,
                    envelope,
                    command_type="update_member",
                    entity_type="member",
                    entity_id=member_id,
                    entity_version=updated.version,
                    pre_state_version=expected,
                )
        except DomainError:
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error

    @_application_boundary
    def add_relationship(self, envelope: CommandEnvelope, relationship: Relationship) -> dict[str, Any]:
        self._assert_route(envelope, "add_relationship")
        self._assert_scope(envelope, relationship.household_id)
        self._assert_payload(envelope, asdict(relationship))
        try:
            with self._uow_factory() as uow:
                replay = self._replay(uow, envelope, command_type="add_relationship", pre_state_version=0)
                if replay is not None:
                    return replay
                for member_id in (relationship.from_member_id, relationship.to_member_id):
                    if uow.members.get(envelope.household_id, member_id) is None:
                        raise NotFound(correlation_id=envelope.correlation_id)
                if uow.relationships.get(envelope.household_id, relationship.relationship_id) is not None:
                    raise Conflict(correlation_id=envelope.correlation_id)
                self._fault("before_business_state")
                uow.relationships.add(relationship)
                self._fault("after_business_state")
                return self._finish(
                    uow,
                    envelope,
                    command_type="add_relationship",
                    entity_type="relationship",
                    entity_id=relationship.relationship_id,
                    entity_version=relationship.version,
                    pre_state_version=0,
                )
        except DomainError:
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error

    @_application_boundary
    def update_relationship(
        self,
        envelope: CommandEnvelope,
        relationship_id: str,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        self._assert_route(envelope, "update_relationship")
        expected = self._expected(envelope)
        payload = {"relationship_id": relationship_id, "changes": changes}
        self._assert_payload(envelope, payload)
        allowed = {"type", "delegation_refs", "status"}
        if not changes or not set(changes) <= allowed:
            raise Validation("Unsupported relationship changes.", correlation_id=envelope.correlation_id)
        if "delegation_refs" in changes:
            changes = {**changes, "delegation_refs": tuple(changes["delegation_refs"])}
        try:
            with self._uow_factory() as uow:
                replay = self._replay(
                    uow,
                    envelope,
                    command_type="update_relationship",
                    pre_state_version=expected,
                )
                if replay is not None:
                    return replay
                current = uow.relationships.get(envelope.household_id, relationship_id)
                if current is None:
                    raise NotFound(correlation_id=envelope.correlation_id)
                updated = replace(
                    current,
                    **changes,
                    version=expected + 1,
                    updated_at=datetime.now(timezone.utc),
                )
                self._fault("before_business_state")
                uow.relationships.update(updated, expected)
                self._fault("after_business_state")
                return self._finish(
                    uow,
                    envelope,
                    command_type="update_relationship",
                    entity_type="relationship",
                    entity_id=relationship_id,
                    entity_version=updated.version,
                    pre_state_version=expected,
                )
        except DomainError:
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error

    @_application_boundary
    def append_observation(self, envelope: CommandEnvelope, observation: Observation) -> dict[str, Any]:
        self._assert_route(envelope, "append_observation")
        self._assert_scope(envelope, observation.household_id)
        self._assert_payload(envelope, asdict(observation))
        if observation.correlation_id != envelope.correlation_id:
            raise Validation(
                "Observation correlation must match the command correlation.",
                correlation_id=envelope.correlation_id,
            )
        stored_observation = replace(observation, received_at=datetime.now(timezone.utc))
        try:
            with self._uow_factory() as uow:
                replay = self._replay(uow, envelope, command_type="append_observation", pre_state_version=0)
                if replay is not None:
                    return replay
                for member_id in stored_observation.subject_ids:
                    if uow.members.get(envelope.household_id, member_id) is None:
                        raise NotFound(correlation_id=envelope.correlation_id)
                if uow.observations.get(envelope.household_id, stored_observation.observation_id) is not None:
                    raise Conflict(correlation_id=envelope.correlation_id)
                self._fault("before_business_state")
                uow.observations.append(stored_observation)
                self._fault("after_business_state")
                return self._finish(
                    uow,
                    envelope,
                    command_type="append_observation",
                    entity_type="observation",
                    entity_id=stored_observation.observation_id,
                    entity_version=1,
                    pre_state_version=0,
                )
        except DomainError:
            raise
        except Exception as error:
            raise PersistenceError(correlation_id=envelope.correlation_id) from error
