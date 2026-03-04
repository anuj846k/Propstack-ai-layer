from datetime import datetime, timedelta, timezone

from app.services.live_session_service import LiveSessionService


def test_live_session_state_transitions() -> None:
    service = LiveSessionService()
    started = service.start_session(call_id="c1", source="api", provider_call_sid="CA1")
    session_id = started["session_id"]

    attached = service.attach_twilio_stream(
        session_id=session_id,
        twilio_stream_sid="MZ1",
        provider_call_sid="CA1",
    )
    assert attached is not None
    assert attached["twilio_stream_sid"] == "MZ1"

    gemini_attached = service.attach_gemini_session(
        session_id=session_id,
        gemini_session_id="g1",
    )
    assert gemini_attached is not None
    assert gemini_attached["gemini_session_id"] == "g1"

    ended = service.end_session(session_id=session_id, status="ended")
    assert ended is not None
    assert ended["status"] == "ended"
    assert ended["ended_at"] is not None


def test_cleanup_expired_sessions() -> None:
    service = LiveSessionService()
    started = service.start_session(call_id="c2", source="api")
    session_id = started["session_id"]
    service.end_session(session_id=session_id, status="ended")

    # Force the session to look old.
    record = service._sessions[session_id]  # noqa: SLF001 - internal state is intentional in this test.
    record.ended_at = datetime.now(timezone.utc) - timedelta(seconds=120)

    removed = service.cleanup_expired(max_age_seconds=30)
    assert removed == 1
    assert service.get_session(session_id) is None
