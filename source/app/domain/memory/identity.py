"""Canonical Stage B event identity, independent of timestamps and payload facts."""

from __future__ import annotations

from typing import TypeVar

from app.domain.observation import Observation

T = TypeVar("T")


def canonical_evidence_identity(item: object) -> tuple[object, ...]:
    observation = isinstance(item, Observation)
    kind = "observation" if observation else "memory"
    identifier = item.observation_id if observation else item.memory_id
    source_ref = item.source_ref.strip()
    if not source_ref:
        return kind, identifier
    subjects = item.subject_ids if observation else (item.subject_id,)
    return item.household_id, tuple(sorted(set(subjects))), item.source_type, source_ref


def unique_evidence(items: tuple[T, ...]) -> tuple[T, ...]:
    # Earliest persisted instant is a deterministic, conservative representative;
    # timestamps select a row, but never define independent event identity.
    def order(item):
        if isinstance(item, Observation):
            return item.observed_at, item.observation_id
        return item.valid_from, item.memory_id

    representatives = {}
    for item in sorted(items, key=order):
        representatives.setdefault(canonical_evidence_identity(item), item)
    return tuple(representatives.values())
