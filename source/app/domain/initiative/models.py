"""A Stage B initiative decision is data, never an executed action."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.domain.shared.canonical import digest
from app.domain.shared.errors import Validation
from app.domain.shared.validation import require_aware_datetime, require_confidence, require_status, require_positive_int

INITIATIVE_MODES = frozenset({"SILENT", "ASK", "SUGGEST", "AUTO_ACT", "REQUEST_AUTHORIZATION"})


@dataclass(frozen=True, slots=True)
class InitiativeDecision:
    initiative_id: str
    household_id: str
    situation_id: str
    need_ids: tuple[str, ...]
    mode: str
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: float
    risk: str
    interruption_cost: str
    policy_version: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for value, name in (
            (self.initiative_id, "initiative_id"),
            (self.household_id, "household_id"),
            (self.situation_id, "situation_id"),
            (self.risk, "risk"),
            (self.interruption_cost, "interruption_cost"),
            (self.policy_version, "policy_version"),
        ):
            if not value or not value.strip():
                raise Validation(f"{name} is required.")
        require_status(self.mode, INITIATIVE_MODES, "mode")
        require_confidence(self.confidence)
        require_aware_datetime(self.created_at, "created_at")



FIVE_MODE_CONTRACT_VERSION = "stage-b-initiative-five-mode-v1"


def bound_initiative_fingerprint(*, household_id, principal_id, plan_id, plan_version, task_id, task_subject_ids, task_risk_level, capability_key, capability_risk_level, policy_version, policy_reversible, policy_automation_eligible, source_situation_id, current_situation_id, currentness_state, scope_fingerprint, admission_decision, admission_reason, authority_basis, authority_refs, as_of):
    """Canonical B1 binding: pure classification rejects a snapshot transplanted to another action."""
    return digest(dict(
        household_id=household_id, principal_id=principal_id, plan_id=plan_id, plan_version=plan_version,
        task_id=task_id, task_subject_ids=task_subject_ids, task_risk_level=task_risk_level,
        capability_key=capability_key, capability_risk_level=capability_risk_level,
        policy_version=policy_version, policy_reversible=policy_reversible,
        policy_automation_eligible=policy_automation_eligible, source_situation_id=source_situation_id,
        current_situation_id=current_situation_id, currentness_state=currentness_state,
        scope_fingerprint=scope_fingerprint, admission_decision=admission_decision,
        admission_reason=admission_reason, authority_basis=authority_basis,
        authority_refs=authority_refs, as_of=as_of,
    ))


def _bound_strings(values: object, name: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if type(values) is not tuple or (nonempty and not values):
        raise Validation(f"{name} must be a tuple of nonempty strings.")
    if any(type(value) is not str or not value.strip() for value in values):
        raise Validation(f"{name} must be a tuple of nonempty strings.")
    return values


@dataclass(frozen=True, slots=True)
class BoundInitiativeInput:
    """Immutable B1 input; D supplies governance facts and B never reads storage."""

    household_id: str
    principal_id: str
    need_id: str
    need_subject_ids: tuple[str, ...]
    need_candidate: bool
    confidence: float
    safety_flags: tuple[str, ...]
    member_conflict: bool
    plan_id: str
    plan_version: int
    task_id: str
    task_subject_ids: tuple[str, ...]
    task_risk_level: str
    capability_key: str
    capability_risk_level: str
    policy_version: str
    policy_reversible: bool
    policy_automation_eligible: bool
    source_situation_id: str
    current_situation_id: str
    currentness_state: str
    scope_fingerprint: str
    binding_fingerprint: str
    admission_decision: str
    admission_reason: str
    requestable: bool
    requestability_reason: str
    authority_basis: str
    authority_refs: tuple[str, ...]
    as_of: datetime
    contract_version: str = FIVE_MODE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for value, name in (
            (self.household_id, "household_id"), (self.principal_id, "principal_id"),
            (self.need_id, "need_id"), (self.plan_id, "plan_id"), (self.task_id, "task_id"),
            (self.capability_key, "capability_key"), (self.policy_version, "policy_version"),
            (self.source_situation_id, "source_situation_id"), (self.current_situation_id, "current_situation_id"),
            (self.scope_fingerprint, "scope_fingerprint"), (self.binding_fingerprint, "binding_fingerprint"),
            (self.admission_reason, "admission_reason"), (self.requestability_reason, "requestability_reason"),
        ):
            if type(value) is not str or not value.strip():
                raise Validation(f"{name} is required.")
        require_positive_int(self.plan_version, "plan_version")
        require_confidence(self.confidence)
        _bound_strings(self.need_subject_ids, "need_subject_ids", nonempty=True)
        _bound_strings(self.task_subject_ids, "task_subject_ids", nonempty=True)
        _bound_strings(self.safety_flags, "safety_flags")
        _bound_strings(self.authority_refs, "authority_refs")
        if not set(self.need_subject_ids) <= set(self.task_subject_ids):
            raise Validation("need subjects must be within the bound task subjects.")
        if type(self.need_candidate) is not bool or type(self.member_conflict) is not bool:
            raise Validation("candidate and conflict flags must be bool.")
        if type(self.policy_reversible) is not bool or type(self.policy_automation_eligible) is not bool:
            raise Validation("policy facts must be bool.")
        require_status(self.task_risk_level, {"LOW", "MEDIUM", "HIGH"}, "task_risk_level")
        require_status(self.capability_risk_level, {"LOW", "MEDIUM", "HIGH"}, "capability_risk_level")
        require_status(self.currentness_state, {"CURRENT", "STALE", "UNVERIFIED"}, "currentness_state")
        require_status(self.admission_decision, {"ALLOW", "DENY", "REQUIRE_AUTHORIZATION", "REQUIRE_DECISION"}, "admission_decision")
        require_status(self.authority_basis, {"NONE", "GRANT", "RULE"}, "authority_basis")
        if self.contract_version != FIVE_MODE_CONTRACT_VERSION:
            raise Validation("unsupported five-mode contract version.")
        require_aware_datetime(self.as_of, "as_of")
        expected_binding = bound_initiative_fingerprint(
            household_id=self.household_id, principal_id=self.principal_id, plan_id=self.plan_id,
            plan_version=self.plan_version, task_id=self.task_id, task_subject_ids=self.task_subject_ids,
            task_risk_level=self.task_risk_level, capability_key=self.capability_key,
            capability_risk_level=self.capability_risk_level, policy_version=self.policy_version,
            policy_reversible=self.policy_reversible, policy_automation_eligible=self.policy_automation_eligible,
            source_situation_id=self.source_situation_id, current_situation_id=self.current_situation_id,
            currentness_state=self.currentness_state, scope_fingerprint=self.scope_fingerprint,
            admission_decision=self.admission_decision, admission_reason=self.admission_reason,
            authority_basis=self.authority_basis, authority_refs=self.authority_refs, as_of=self.as_of,
        )
        if self.binding_fingerprint != expected_binding:
            raise Validation("binding fingerprint mismatch.")


@dataclass(frozen=True, slots=True)
class GovernedInitiativeDecision:
    """A classification record, never a grant, request, execution token, or write command."""

    household_id: str
    plan_id: str
    task_id: str
    binding_fingerprint: str
    mode: str | None
    error_code: str | None
    reason_codes: tuple[str, ...]
    contract_version: str = FIVE_MODE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for value, name in ((self.household_id, "household_id"), (self.plan_id, "plan_id"),
                            (self.task_id, "task_id"), (self.binding_fingerprint, "binding_fingerprint")):
            if type(value) is not str or not value.strip():
                raise Validation(f"{name} is required.")
        _bound_strings(self.reason_codes, "reason_codes", nonempty=True)
        if self.contract_version != FIVE_MODE_CONTRACT_VERSION:
            raise Validation("unsupported five-mode contract version.")
        if (self.mode is None) == (self.error_code is None):
            raise Validation("decision has either a mode or a classification error.")
        if self.mode is not None:
            require_status(self.mode, INITIATIVE_MODES, "mode")
        elif type(self.error_code) is not str or not self.error_code.strip():
            raise Validation("error_code is required for classification errors.")
