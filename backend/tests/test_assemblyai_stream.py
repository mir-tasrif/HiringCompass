from datetime import datetime, timedelta, timezone

import jwt
import uuid

from app.api.routers.interview_stream import final_turn, turn_times
from app.services import interview_stream_health as health
from app.core.config import Settings
from app.services.interviews import stt_token


def test_only_final_speech_is_saved():
    assert final_turn({"type": "Turn", "end_of_turn": False, "transcript": "partial"}) is None
    assert final_turn({"type": "Turn", "end_of_turn": True, "transcript": "[Silence]"}) is None
    assert final_turn({"type": "Turn", "end_of_turn": True, "transcript": "Hello there."}) == "Hello there."
    assert final_turn({"type": "Turn", "end_of_turn": True, "turn_is_formatted": False,
                       "transcript": "Hello from the English model"}) == "Hello from the English model"


def test_word_timestamps_are_relative_to_stream_start():
    connected = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
    start, end = turn_times({"words": [{"start": 1200, "end": 1600},
                                         {"start": 1700, "end": 2400}]}, connected)
    assert start == connected + timedelta(milliseconds=1200)
    assert end == connected + timedelta(milliseconds=2400)


def test_stream_token_is_scoped_to_interview_and_speaker():
    settings = Settings(_env_file=None, jwt_secret="test-signing-secret-long-enough")
    claims = jwt.decode(stt_token(settings, "interview-id", "candidate"), settings.jwt_secret,
                        algorithms=[settings.jwt_algorithm])
    assert claims["purpose"] == "interview_stt"
    assert claims["sid"] == "interview-id"
    assert claims["role"] == "candidate"


def test_transcription_status_distinguishes_active_audio_and_interrupted_stream():
    session_id = uuid.uuid4()
    assert health.snapshot(session_id)["candidate"] == "not_started"
    health.opened(session_id, "candidate", "first")
    assert health.snapshot(session_id)["candidate"] == "no_audio"
    health.audio_received(session_id, "candidate", "first")
    assert health.snapshot(session_id)["candidate"] == "streaming"
    health.opened(session_id, "candidate", "replacement")
    health.closed(session_id, "candidate", "first", error=True)
    assert health.snapshot(session_id)["candidate"] == "no_audio"
    health.closed(session_id, "candidate", "replacement", error=True)
    assert health.snapshot(session_id)["candidate"] == "interrupted"
