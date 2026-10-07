"""Application error taxonomy: drives retry vs. fail decisions in graphs and the worker."""

from __future__ import annotations


# Base class for expected application errors; `category` is safe to store and show.
class AppError(Exception):
    category = "app_error"


# Temporary failure (rate limit, network, provider outage): the node is retried.
class TransientError(AppError):
    category = "transient"


# Failure that retrying cannot fix (bad input, unsupported file): the run fails with a reason.
class PermanentError(AppError):
    category = "permanent"


# A hand-off payload between graphs/nodes did not match its Pydantic contract.
class HandoffValidationError(PermanentError):
    category = "handoff_invalid"




# An uploaded file failed validation; `code` is machine-readable, `message` is safe to show users.
class UploadRejected(PermanentError):
    category = "upload_rejected"

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message



# The model kept returning output that does not match the requested schema.
class StructuredOutputError(PermanentError):
    category = "llm_output_invalid"




# An approval request/decision conflicts with its stored state (stale, duplicate, wrong option).
class ApprovalError(PermanentError):
    category = "approval_conflict"




# Missing, invalid or expired credentials (HTTP 401).
class AuthError(PermanentError):
    category = "auth_failed"


# Authenticated, but the role may not do this (HTTP 403).
class ForbiddenError(PermanentError):
    category = "forbidden"



# The requested resource does not exist or is not visible to this user (HTTP 404).
class NotFoundError(PermanentError):
    category = "not_found"


# The request conflicts with the current state, e.g. the assistant is still busy (HTTP 409).
class ConflictError(PermanentError):
    category = "conflict"