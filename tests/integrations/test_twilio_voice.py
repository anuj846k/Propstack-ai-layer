import base64
from array import array
import pytest

from app.integrations import twilio_voice


def test_trial_allowlist_pass_fail(monkeypatch) -> None:
    monkeypatch.setattr(twilio_voice.settings, "twilio_trial_mode", True)
    monkeypatch.setattr(
        twilio_voice.settings,
        "twilio_trial_allowed_to_numbers",
        "+911234567890, +918888888888",
    )

    allowed, reason = twilio_voice.check_trial_number_allowed("+911234567890")
    assert allowed is True
    assert reason is None

    blocked, blocked_reason = twilio_voice.check_trial_number_allowed("+919999999999")
    assert blocked is False
    assert "verified numbers" in (blocked_reason or "")


def test_validate_signature_uses_request_validator(monkeypatch) -> None:
    monkeypatch.setattr(twilio_voice.settings, "twilio_validate_webhook_signature", True)
    monkeypatch.setattr(twilio_voice.settings, "twilio_auth_token", "token")
    monkeypatch.setattr(twilio_voice, "_TWILIO_IMPORT_ERROR", None)

    class _FakeValidator:
        def __init__(self, token: str):
            self.token = token

        def validate(self, url: str, params: dict[str, str], signature: str) -> bool:
            return self.token == "token" and signature == "valid-signature"

    monkeypatch.setattr(twilio_voice, "RequestValidator", _FakeValidator)

    assert (
        twilio_voice.validate_signature(
            url="https://example.com/callback",
            params={"CallSid": "CA123", "CallStatus": "ringing"},
            signature="valid-signature",
        )
        is True
    )
    assert (
        twilio_voice.validate_signature(
            url="https://example.com/callback",
            params={"CallSid": "CA123", "CallStatus": "ringing"},
            signature="invalid",
        )
        is False
    )


def test_twilio_status_mapping() -> None:
    assert twilio_voice.map_status("queued") == {
        "call_status": "queued",
        "outcome": "in_progress",
        "is_terminal": False,
    }
    assert twilio_voice.map_status("initiated") == {
        "call_status": "initiated",
        "outcome": "in_progress",
        "is_terminal": False,
    }
    assert twilio_voice.map_status("ringing") == {
        "call_status": "ringing",
        "outcome": "in_progress",
        "is_terminal": False,
    }
    assert twilio_voice.map_status("in-progress") == {
        "call_status": "in-progress",
        "outcome": "in_progress",
        "is_terminal": False,
    }
    assert twilio_voice.map_status("completed") == {
        "call_status": "completed",
        "outcome": "completed",
        "is_terminal": True,
    }
    assert twilio_voice.map_status("busy") == {
        "call_status": "busy",
        "outcome": "no_answer",
        "is_terminal": True,
    }
    assert twilio_voice.map_status("no-answer") == {
        "call_status": "no-answer",
        "outcome": "no_answer",
        "is_terminal": True,
    }
    assert twilio_voice.map_status("failed") == {
        "call_status": "failed",
        "outcome": "failed",
        "is_terminal": True,
    }
    assert twilio_voice.map_status("canceled") == {
        "call_status": "canceled",
        "outcome": "failed",
        "is_terminal": True,
    }


def test_twilio_media_stream_url_scheme() -> None:
    original = twilio_voice.settings.public_base_url
    try:
        twilio_voice.settings.public_base_url = "https://demo.example.com"
        assert twilio_voice.twilio_media_stream_url("c1").startswith(
            "wss://demo.example.com/"
        )

        twilio_voice.settings.public_base_url = "http://localhost:8001"
        assert twilio_voice.twilio_media_stream_url("c1").startswith(
            "ws://localhost:8001/"
        )
    finally:
        twilio_voice.settings.public_base_url = original


def test_transcoding_requires_audio_backend(monkeypatch) -> None:
    monkeypatch.setattr(twilio_voice, "audioop", None)
    with pytest.raises(RuntimeError):
        twilio_voice.pcm16_to_twilio_payload(array("h", [0, 1, -1]).tobytes())
    with pytest.raises(RuntimeError):
        twilio_voice.twilio_payload_to_pcm16(base64.b64encode(b"\xff\x7f").decode("ascii"))
