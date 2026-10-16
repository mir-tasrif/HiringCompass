"""Track ephemeral per-speaker STT stream health without storing audio or transcript text."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class StreamHealth:
    connection_id: str
    connected: bool
    last_audio_at: datetime | None = None
    last_turn_at: datetime | None = None
    error: bool = False


# Interview streaming is process-local; this status is diagnostic and resets on restart.
_streams: dict[tuple[uuid.UUID, str], StreamHealth] = {}


def opened(session_id: uuid.UUID, role: str, connection_id: str) -> None:
    _streams[(session_id, role)] = StreamHealth(connection_id=connection_id, connected=True)


def audio_received(session_id: uuid.UUID, role: str, connection_id: str) -> None:
    state = _streams.get((session_id, role))
    if state and state.connection_id == connection_id:
        state.last_audio_at = datetime.now(timezone.utc)


def turn_received(session_id: uuid.UUID, role: str, connection_id: str) -> None:
    state = _streams.get((session_id, role))
    if state and state.connection_id == connection_id:
        state.last_turn_at = datetime.now(timezone.utc)


def closed(session_id: uuid.UUID, role: str, connection_id: str, *, error: bool) -> None:
    state = _streams.get((session_id, role))
    if state and state.connection_id == connection_id:
        state.connected = False
        state.error = error


def snapshot(session_id: uuid.UUID) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    result = {}
    for role in ("interviewer", "candidate"):
        state = _streams.get((session_id, role))
        if state is None:
            result[role] = "not_started"
        elif not state.connected:
            result[role] = "interrupted" if state.error else "stopped"
        elif state.last_audio_at is None or (now - state.last_audio_at).total_seconds() > 8:
            result[role] = "no_audio"
        else:
            result[role] = "streaming"
    return result
