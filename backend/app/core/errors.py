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