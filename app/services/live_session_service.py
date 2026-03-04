"""In-memory lifecycle manager for live voice sessions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any
import uuid


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LiveSessionRecord:
    session_id: str
    call_id: str
    status: str
    source: str
    provider_call_sid: str | None = None
    twilio_stream_sid: str | None = None
    gemini_session_id: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    started_at: datetime = field(default_factory=_utcnow)
    ended_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class LiveSessionService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, LiveSessionRecord] = {}
        self._call_to_session: dict[str, str] = {}

    def start_session(
        self,
        *,
        call_id: str,
        source: str,
        provider_call_sid: str | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if call_id in self._call_to_session:
                existing_id = self._call_to_session[call_id]
                existing = self._sessions.get(existing_id)
                if existing:
                    if provider_call_sid and not existing.provider_call_sid:
                        existing.provider_call_sid = provider_call_sid
                    if metadata:
                        existing.metadata.update(metadata)
                    existing.status = "active"
                    return _serialize(existing)

            record = LiveSessionRecord(
                session_id=session_id or str(uuid.uuid4()),
                call_id=call_id,
                status="active",
                source=source,
                provider_call_sid=provider_call_sid,
                metadata=metadata or {},
            )
            self._sessions[record.session_id] = record
            self._call_to_session[call_id] = record.session_id
            return _serialize(record)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(session_id)
            return _serialize(record) if record else None

    def find_by_call_id(self, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            session_id = self._call_to_session.get(call_id)
            if not session_id:
                return None
            record = self._sessions.get(session_id)
            return _serialize(record) if record else None

    def attach_twilio_stream(
        self,
        *,
        session_id: str,
        twilio_stream_sid: str | None,
        provider_call_sid: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            if twilio_stream_sid:
                record.twilio_stream_sid = twilio_stream_sid
            if provider_call_sid:
                record.provider_call_sid = provider_call_sid
            return _serialize(record)

    def attach_gemini_session(
        self,
        *,
        session_id: str,
        gemini_session_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            record.gemini_session_id = gemini_session_id
            return _serialize(record)

    def end_session(
        self,
        *,
        session_id: str,
        status: str = "ended",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return None
            record.status = status
            record.ended_at = _utcnow()
            if metadata:
                record.metadata.update(metadata)
            return _serialize(record)

    def cleanup_expired(self, *, max_age_seconds: int) -> int:
        now = _utcnow()
        removed = 0
        with self._lock:
            to_delete: list[str] = []
            for session_id, record in self._sessions.items():
                if record.ended_at is not None:
                    age = (now - record.ended_at).total_seconds()
                else:
                    age = (now - record.started_at).total_seconds()
                if age > max_age_seconds:
                    to_delete.append(session_id)

            for session_id in to_delete:
                call_id = self._sessions[session_id].call_id
                del self._sessions[session_id]
                self._call_to_session.pop(call_id, None)
                removed += 1

        return removed

    def shutdown(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._call_to_session.clear()


def _serialize(record: LiveSessionRecord | None) -> dict[str, Any] | None:
    if not record:
        return None
    payload = asdict(record)
    payload["created_at"] = record.created_at.isoformat()
    payload["started_at"] = record.started_at.isoformat()
    payload["ended_at"] = record.ended_at.isoformat() if record.ended_at else None
    return payload


live_session_service = LiveSessionService()
