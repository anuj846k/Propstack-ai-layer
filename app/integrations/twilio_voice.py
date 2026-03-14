"""Twilio voice integration helpers for outbound calls and live bootstrap."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

try:
    import audioop  # Python <=3.12 builtin or backported module.
except ImportError:  # pragma: no cover - runtime dependency varies by Python version.
    try:
        import audioop_lts as audioop  # type: ignore[import-not-found,assignment]
    except ImportError:
        audioop = None  # type: ignore[assignment]

try:
    from twilio.request_validator import RequestValidator
    from twilio.rest import Client
    from twilio.twiml.voice_response import VoiceResponse
except ImportError as exc:  # pragma: no cover - covered by runtime config checks.
    RequestValidator = None  # type: ignore[assignment]
    Client = Any  # type: ignore[misc,assignment]
    VoiceResponse = Any  # type: ignore[misc,assignment]
    _TWILIO_IMPORT_ERROR = exc
else:
    _TWILIO_IMPORT_ERROR = None

from app.config import settings

TERMINAL_STATUSES = {"completed", "busy", "no-answer", "failed", "canceled"}


def has_audio_transcoding_support() -> bool:
    return audioop is not None


def get_client() -> Client:
    if _TWILIO_IMPORT_ERROR is not None:
        raise RuntimeError(
            "twilio SDK is not installed. Run `uv sync` to install project dependencies."
        ) from _TWILIO_IMPORT_ERROR
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def _base_url() -> str:
    return settings.public_base_url.rstrip("/")


def _ws_base_url() -> str:
    parsed = urlparse(_base_url())
    scheme = parsed.scheme.lower()
    if scheme == "https":
        ws_scheme = "wss"
    elif scheme == "http":
        ws_scheme = "ws"
    elif scheme in {"ws", "wss"}:
        ws_scheme = scheme
    else:
        ws_scheme = "wss"
    return urlunparse((ws_scheme, parsed.netloc, parsed.path, "", "", "")).rstrip("/")


def status_callback_url(call_id: str) -> str:
    query = urlencode({"call_id": call_id})
    return f"{_base_url()}/api/v1/calls/twilio/status?{query}"


def twiml_url(call_id: str) -> str:
    return f"{_base_url()}/api/v1/calls/twilio/twiml/{call_id}"


def twilio_media_stream_url(call_id: str) -> str:
    return f"{_ws_base_url()}/api/v1/calls/live/twilio/media/{call_id}"


def create_outbound_call(
    *,
    to_number: str,
    call_id: str,
    twiml_url_override: str | None = None,
    status_callback_override: str | None = None,
    enable_recording: bool = True,
) -> dict:
    call_params = dict(
        to=to_number,
        from_=settings.twilio_voice_from_number,
        url=twiml_url_override or twiml_url(call_id),
        method="POST",
        status_callback=status_callback_override or status_callback_url(call_id),
        status_callback_method="POST",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        timeout=settings.twilio_call_timeout_seconds,
    )

    # Enable recording and transcription for all calls
    if enable_recording:
        call_params.update(
            {
                "record": "record-from-ringing-dual",
                "transcription_callback_url": f"{_base_url()}/api/v1/calls/twilio/transcription?call_id={call_id}",
            }
        )

    call = get_client().calls.create(**call_params)
    return {
        "provider_call_sid": call.sid,
        "provider_status": call.status,
    }


def validate_signature(
    *, url: str, params: dict[str, str], signature: str | None
) -> bool:
    if not settings.twilio_validate_webhook_signature:
        return True
    if not signature:
        return False
    if _TWILIO_IMPORT_ERROR is not None:
        raise RuntimeError(
            "twilio SDK is not installed. Run `uv sync` to install project dependencies."
        ) from _TWILIO_IMPORT_ERROR
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(url, params, signature)


def map_status(call_status: str) -> dict:
    status = (call_status or "").strip().lower()
    if status == "completed":
        outcome = "completed"
    elif status in {"busy", "no-answer"}:
        outcome = "no_answer"
    elif status in {"failed", "canceled"}:
        outcome = "failed"
    else:
        outcome = "in_progress"

    return {
        "call_status": status,
        "outcome": outcome,
        "is_terminal": status in TERMINAL_STATUSES,
    }


def build_twiml_bootstrap_response(*, call_id: str) -> str:
    if _TWILIO_IMPORT_ERROR is not None:
        raise RuntimeError(
            "twilio SDK is not installed. Run `uv sync` to install project dependencies."
        ) from _TWILIO_IMPORT_ERROR

    response = VoiceResponse()

    # Check if ADK Live is enabled
    if settings.enable_partner_twilio_live:
        connect = response.connect()
        # Bidirectional <Connect><Stream> supports inbound track only.
        connect.stream(url=twilio_media_stream_url(call_id), track="inbound_track")
    else:
        # Fallback: simple greeting and record for transcription
        response.say(
            "Hello, this is a call from PropStack. Please hold while we connect you."
        )
        response.record(action="/api/v1/calls/twilio/recording-complete", method="POST")

    return str(response)


def build_simple_twiml_response(*, call_id: str) -> str:
    """Build a simple TwiML response that works without ADK Live - records the call."""
    if _TWILIO_IMPORT_ERROR is not None:
        raise RuntimeError(
            "twilio SDK is not installed. Run `uv sync` to install project dependencies."
        ) from _TWILIO_IMPORT_ERROR

    response = VoiceResponse()
    # Simple approach: just record the call for transcription
    response.say("Hello, this is PropStack calling regarding your rent. Please hold.")
    response.record(
        action="/api/v1/calls/twilio/recording-complete",
        method="POST",
        max_length=300,  # 5 minutes max
        play_beep=True,
    )
    return str(response)


def twilio_payload_to_pcm16(payload_b64: str) -> bytes:
    """Convert Twilio base64 mulaw payload to PCM16 at configured Live input rate."""
    if audioop is None:
        raise RuntimeError(
            "Audio transcoding backend missing. Install dependencies (`uv sync`) to get audioop/audioop-lts."
        )
    ulaw_bytes = base64.b64decode(payload_b64)
    pcm_8k = audioop.ulaw2lin(ulaw_bytes, 2)
    if settings.twilio_stream_sample_rate_hz == settings.live_input_sample_rate_hz:
        return pcm_8k
    pcm_target, _ = audioop.ratecv(
        pcm_8k,
        2,
        1,
        settings.twilio_stream_sample_rate_hz,
        settings.live_input_sample_rate_hz,
        None,
    )
    return pcm_target


def pcm16_to_twilio_payload(pcm_bytes: bytes) -> str:
    """Convert PCM16 bytes to Twilio base64 mulaw payload at 8k."""
    if audioop is None:
        raise RuntimeError(
            "Audio transcoding backend missing. Install dependencies (`uv sync`) to get audioop/audioop-lts."
        )
    source_rate = settings.live_output_sample_rate_hz
    if source_rate != settings.twilio_stream_sample_rate_hz:
        pcm_bytes, _ = audioop.ratecv(
            pcm_bytes,
            2,
            1,
            source_rate,
            settings.twilio_stream_sample_rate_hz,
            None,
        )
    ulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)
    return base64.b64encode(ulaw_bytes).decode("ascii")
