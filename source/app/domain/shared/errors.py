"""Typed errors whose public messages never expose internal exception text."""

from __future__ import annotations


class DomainError(Exception):
    code = "domain_error"
    default_public_message = "The request could not be completed."

    def __init__(self, public_message: str | None = None, *, correlation_id: str | None = None) -> None:
        self.public_message = public_message or self.default_public_message
        self.correlation_id = correlation_id
        super().__init__(self.public_message)


class NotFound(DomainError):
    code = "not_found"
    default_public_message = "The requested resource was not found."


class StaleVersion(DomainError):
    code = "stale_version"
    default_public_message = "The resource changed before this request could be applied."


class Conflict(DomainError):
    code = "conflict"
    default_public_message = "The request conflicts with existing state."


class Validation(DomainError):
    code = "validation"
    default_public_message = "The request is invalid."


class TenantScopeViolation(DomainError):
    code = "tenant_scope_violation"
    default_public_message = "The request is outside the active household."


class PersistenceError(DomainError):
    code = "persistence_error"
    default_public_message = "The request could not be persisted."


class InternalError(DomainError):
    code = "internal_error"
    default_public_message = "An internal error occurred."


class UnitOfWorkLifecycleError(DomainError):
    code = "uow_lifecycle_error"
    default_public_message = "The transaction is no longer active."
