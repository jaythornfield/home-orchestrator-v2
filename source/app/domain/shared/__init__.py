"""Shared domain primitives."""

from .errors import (
    Conflict,
    DomainError,
    InternalError,
    NotFound,
    PersistenceError,
    StaleVersion,
    TenantScopeViolation,
    UnitOfWorkLifecycleError,
    Validation,
)

__all__ = [
    "Conflict",
    "DomainError",
    "InternalError",
    "NotFound",
    "PersistenceError",
    "StaleVersion",
    "TenantScopeViolation",
    "UnitOfWorkLifecycleError",
    "Validation",
]
