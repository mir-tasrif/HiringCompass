"""Authenticated, in-memory AssemblyAI streaming for each interview participant."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import InterviewSession, InterviewTurn
from app.db.session import SessionLocal

router = APIRouter(prefix="/interviews", tags=["interviews"])
settings = get_settings()
logger = logging.getLogger("app.api.interview_stream")


def final_turn(payload: dict) -> str | None:
    """Only finalized speech becomes evidence; ignore silence and interim revisions."""
    if payload.get("type") != "Turn" or payload.get("end_of_turn") is not True:
        return None
    text = str(payload.get("transcript") or "").strip()
    if not text or re.fullmatch(r"(?:\[[^]]+\]\s*)+", text):
        return None
    return text[:8000]


def turn_times(payload: dict, connected_at: datetime) -> tuple[datetime, datetime]:
    words = [word for word in payload.get("words", []) if isinstance(word, dict)]
    starts = [word["start"] for word in words if isinstance(word.get("start"), (int, float))]
    ends = [word["end"] for word in words if isinstance(word.get("end"), (int, float))]
    if starts and ends:
        start = connected_at + timedelta(milliseconds=min(starts))
        end = connected_at + timedelta(milliseconds=max(ends))
        if end > start:
            return start, end
    end = datetime.now(timezone.utc)
    return end - timedelta(seconds=1), end


@router.websocket("/stt")
async def interview_stt(websocket: WebSocket) -> None:
    await websocket.accept()
    if settings.stt_provider != "assemblyai" or not settings.assemblyai_api_key:
        await websocket.close(code=1013, reason="AssemblyAI is not configured")
        return
    try:
        auth = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
        claims = jwt.decode(auth.get("token", ""), settings.jwt_secret,
                            algorithms=[settings.jwt_algorithm], options={"require": ["exp", "sid", "role"]})
        if claims.get("purpose") != "interview_stt" or claims.get("role") not in {"candidate", "interviewer"}:
            raise ValueError("Invalid stream scope")
        session_id = uuid.UUID(claims["sid"])
        role = claims["role"]
        async with SessionLocal() as session:
            record = await session.get(InterviewSession, session_id)
            if (record is None or record.status != "live" or record.started_at is None or
                (role == "candidate" and (record.candidate_admitted_at is None or record.candidate_joined_at is None))):
                raise ValueError("Interview is not active for this participant")
    except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, KeyError, jwt.PyJWTError, json.JSONDecodeError):
        await websocket.close(code=1008, reason="Interview stream authorization failed")
        return

    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode({
        "sample_rate": 16000, "speech_model": settings.assemblyai_streaming_model,
    })
    try:
        async with websockets.connect(url, additional_headers={"Authorization": settings.assemblyai_api_key},
                                      open_timeout=15, max_size=1_000_000) as provider:
            connected_at = datetime.now(timezone.utc)
            provider_session_id = uuid.uuid4().hex
            await websocket.send_json({"type": "ready"})

            async def upload() -> None:
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        audio = message.get("bytes")
                        if audio is not None:
                            if not audio or len(audio) > 64_000:
                                continue
                            await provider.send(audio)
                        elif message.get("text"):
                            data = json.loads(message["text"])
                            if data.get("type") == "stop":
                                break
                except (WebSocketDisconnect, json.JSONDecodeError):
                    pass
                await provider.send(json.dumps({"type": "Terminate"}))

            async def receive() -> None:
                nonlocal provider_session_id
                async for raw in provider:
                    payload = json.loads(raw)
                    if payload.get("type") == "Begin":
                        provider_session_id = str(payload.get("id") or provider_session_id)[:50]
                        continue
                    text = final_turn(payload)
                    if text is None:
                        continue
                    order = payload.get("turn_order")
                    if not isinstance(order, int) or order < 0:
                        continue
                    chunk_id = f"aai:{provider_session_id}:{order}"[:80]
                    start, end = turn_times(payload, connected_at)
                    async with SessionLocal() as session:
                        record = await session.get(InterviewSession, session_id)
                        if record is None or (record.status != "live" and
                            not (record.ended_at and datetime.now(timezone.utc) - record.ended_at < timedelta(minutes=1))):
                            break
                        existing = await session.scalar(select(InterviewTurn.id).where(
                            InterviewTurn.session_id == session_id, InterviewTurn.role == role,
                            InterviewTurn.chunk_id == chunk_id))
                        if existing is None:
                            session.add(InterviewTurn(session_id=session_id, role=role, chunk_id=chunk_id,
                                                      started_at=start, ended_at=end, text=text,
                                                      provider="assemblyai_streaming"))
                            await session.commit()

            sender = asyncio.create_task(upload())
            receiver = asyncio.create_task(receive())
            done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if sender in done:
                try:
                    await asyncio.wait_for(receiver, timeout=4)
                except asyncio.TimeoutError:
                    receiver.cancel()
            else:
                sender.cancel()
            for task in (sender, receiver):
                if task.done() and not task.cancelled():
                    task.result()
    except Exception:
        logger.exception("AssemblyAI interview stream failed", extra={"interview_id": str(session_id), "role": role})
        try:
            await websocket.send_json({"type": "error", "message": "Transcription is temporarily unavailable."})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
