"""Deterministic independent-evidence rules for the bounded Stage B Routine fixture."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from app.domain.observation import Observation
from .identity import unique_evidence


@dataclass(frozen=True, slots=True)
class RoutineEvidenceAssessment:
    accepted: bool
    observation_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]


def _observation_id(reference: str) -> str | None:
    prefix = "observation:"
    if not reference.startswith(prefix):
        return None
    value = reference[len(prefix):]
    return value if value else None


def _facts_identity(observation: Observation) -> str:
    return json.dumps(observation.facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def assess_routine_evidence(
    *,
    evidence_refs: tuple[str, ...],
    observations: tuple[Observation, ...],
    household_id: str,
    subject_id: str,
    as_of: datetime,
    minimum: int,
) -> RoutineEvidenceAssessment:
    by_id = {item.observation_id: item for item in observations}
    unique_ids: list[str] = []
    seen_ids: set[str] = set()
    for reference in evidence_refs:
        observation_id = _observation_id(reference)
        if observation_id is None:
            return RoutineEvidenceAssessment(False, (), ("routine_evidence_ref_invalid",))
        if observation_id not in seen_ids:
            seen_ids.add(observation_id)
            unique_ids.append(observation_id)
    resolved: list[Observation] = []
    for observation_id in unique_ids:
        observation = by_id.get(observation_id)
        if observation is None:
            return RoutineEvidenceAssessment(False, tuple(unique_ids), ("routine_evidence_missing",))
        if observation.household_id != household_id or subject_id not in observation.subject_ids:
            return RoutineEvidenceAssessment(False, tuple(unique_ids), ("routine_evidence_scope_mismatch",))
        if observation.status != "ACTIVE" or observation.observed_at > as_of:
            return RoutineEvidenceAssessment(False, tuple(unique_ids), ("routine_evidence_not_active",))
        if observation.expires_at is not None and observation.expires_at <= as_of:
            return RoutineEvidenceAssessment(False, tuple(unique_ids), ("routine_evidence_not_active",))
        resolved.append(observation)

    independent = unique_evidence(tuple(resolved))
    if len(independent) < minimum:
        return RoutineEvidenceAssessment(False, tuple(item.observation_id for item in independent), ("routine_evidence_not_independent",))
    if len({_facts_identity(item) for item in independent}) != 1:
        return RoutineEvidenceAssessment(False, tuple(unique_ids), ("routine_evidence_incompatible",))
    return RoutineEvidenceAssessment(True, tuple(unique_ids), ("routine_evidence_verified",))

