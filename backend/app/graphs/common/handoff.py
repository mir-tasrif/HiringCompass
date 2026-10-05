"""Typed hand-offs: graph state stores plain dicts; contracts are validated at every boundary."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import HandoffValidationError

T = TypeVar("T", bound=BaseModel)


# Serialise a validated contract into a JSON-safe dict for graph state / checkpoints.
def to_state(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


# Validate a state payload against its contract; error text lists field names only (no values/PII).
def from_state(model_cls: type[T], payload: dict[str, Any] | None) -> T:
    try:
        return model_cls.model_validate(payload or {})
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "<root>" for e in exc.errors()})
        raise HandoffValidationError(f"{model_cls.__name__} invalid fields: {', '.join(fields)}") from exc