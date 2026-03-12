import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, urlunparse

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.genai import types

from app.agents.maintenance.voice_dispatch_agent import vendor_dispatch_agent
from app.config import settings
from app.dependencies import get_supabase
from app.integrations import twilio_voice
from app.routers.rent import _form_to_string_dict
from app.services.live_session_service import live_session_service
from app.services.session_service import get_session_service
from app.utils.transcript_collector import TranscriptCollector

logger = logging.getLogger(__name__)
router = APIRouter()

voice_session_service = get_session_service()
voice_runner = Runner(
    agent=vendor_dispatch_agent,
    app_name="propstack_maintenance_voice",
    session_service=voice_session_service,
    auto_create_session=True,
)


def _find_vendor_dispatch_log(dispatch_log_id: str):
    sb = get_supabase()
    res = (
        sb.table("vendor_dispatch_logs").select("*").eq("id", dispatch_log_id).execute()
    )
    return res.data[0] if res.data else None


def _get_ticket_details(ticket_id: str):
    sb = get_supabase()
    res = sb.table("maintenance_tickets").select("*").eq("id", ticket_id).execute()
    return res.data[0] if res.data else None


def _get_vendor_details(vendor_id: str):
    sb = get_supabase()
    res = sb.table("vendors").select("*").eq("id", vendor_id).execute()
    return res.data[0] if res.data else None


def _build_initial_greeting(vendor_name: str, ticket: dict) -> str:
    """Build dynamic greeting for the vendor with language preference."""
    issue = ticket.get("issue_description") or ticket.get("ai_summary") or "an issue"
    category = ticket.get("issue_category", "maintenance")
    priority = ticket.get("priority", "medium")

    greeting = f"""Hello {vendor_name}, this is Sara from the PropStack property management office calling.

Context for this call:
- You are speaking to the vendor: {vendor_name}.
- Ticket category: {category}
- Priority: {priority}
- Issue description: {issue}

First, ask: "Would you like to continue in English or Hindi?" (ask in English initially)
Then proceed in the vendor's chosen language.

After language is established, explain the issue and ask about their availability.
"""
    return greeting


def _voice_run_config() -> RunConfig:
    """Create RunConfig for human-like voice conversation."""
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
    )


def _base_url() -> str:
    return settings.public_base_url.rstrip("/")


def twiml_url(dispatch_log_id: str) -> str:
    return f"{_base_url()}/api/v1/maintenance/calls/twilio/twiml/{dispatch_log_id}"


def status_callback_url(dispatch_log_id: str) -> str:
    query = urlencode({"call_id": dispatch_log_id})
    return f"{_base_url()}/api/v1/maintenance/calls/twilio/status?{query}"


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


def twilio_media_stream_url(dispatch_log_id: str) -> str:
    return (
        f"{_ws_base_url()}/api/v1/maintenance/calls/live/twilio/media/{dispatch_log_id}"
    )


@router.post("/maintenance/calls/twilio/status")
async def twilio_status_callback(
    request: Request,
    call_id: str,
    x_twilio_signature: str = Header("", alias="X-Twilio-Signature"),
) -> dict:
    log_row = _find_vendor_dispatch_log(call_id)
    if not log_row:
        raise HTTPException(status_code=404, detail="Dispatch log not found")

    form = await request.form()
    form_payload = _form_to_string_dict(form)
    is_valid = twilio_voice.validate_signature(
        url=str(request.url),
        params=form_payload,
        signature=x_twilio_signature,
    )
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid Twilio signature")

    provider_call_sid = form_payload.get("CallSid")
    mapped = twilio_voice.map_status(form_payload.get("CallStatus") or "")

    sb = get_supabase()

    # Save call outcome
    sb.table("vendor_dispatch_logs").update(
        {"status": mapped["outcome"], "provider_call_sid": provider_call_sid}
    ).eq("id", call_id).execute()

    if mapped["is_terminal"]:
        existing_live = live_session_service.find_by_call_id(call_id)
        if existing_live:
            live_session_service.end_session(
                session_id=existing_live["session_id"],
                status="ended",
                metadata={"final_provider_status": mapped["call_status"]},
            )

        # Optional: Auto-queue next vendor if this vendor didn't accept and the call is over
        # If the status in vendor_dispatch_logs wasn't set to "accepted" by the agent tool, maybe try next.
        # But for now we rely on the generic system.

    return {
        "status": "success",
        "message": "Status callback processed",
        "outcome": mapped["outcome"],
    }


@router.post("/maintenance/calls/twilio/twiml/{dispatch_log_id}")
async def twilio_twiml(dispatch_log_id: str) -> Response:
    from twilio.twiml.voice_response import VoiceResponse

    response = VoiceResponse()
    connect = response.connect()
    connect.stream(url=twilio_media_stream_url(dispatch_log_id), track="inbound_track")
    return Response(content=str(response), media_type="application/xml")


@router.websocket("/maintenance/calls/live/twilio/media/{dispatch_log_id}")
async def twilio_media_stream(websocket: WebSocket, dispatch_log_id: str) -> None:
    log_row = _find_vendor_dispatch_log(dispatch_log_id)
    if not log_row:
        await websocket.close(code=4404, reason="Dispatch log not found")
        return

    ticket = _get_ticket_details(log_row["ticket_id"])
    vendor = _get_vendor_details(log_row["vendor_id"])

    if not ticket or not vendor:
        await websocket.close(code=4404, reason="Ticket or Vendor not found")
        return

    await websocket.accept()

    record = live_session_service.start_session(
        call_id=dispatch_log_id,
        source="twilio_media_ws",
        metadata={"transport": "twilio_media_stream"},
    )
    live_session_id = record["session_id"]
    twilio_stream_sid: str | None = None
    call_start_time: datetime | None = None

    transcript_collector = TranscriptCollector()
    outbound_audio_task: asyncio.Task[None] | None = None
    live_queue: LiveRequestQueue | None = None

    if settings.enable_partner_twilio_live:
        if not twilio_voice.has_audio_transcoding_support():
            logger.warning("Audio transcoding unavailable.")
            if not settings.enable_custom_bridge_fallback:
                await websocket.close(code=1011, reason="Audio transcoding unavailable")
                return

    async def _send_audio_to_twilio():
        nonlocal twilio_stream_sid
        if not live_queue:
            return

        try:
            async for event in voice_runner.run_live(
                user_id=dispatch_log_id,
                session_id=live_session_id,
                live_request_queue=live_queue,
                run_config=_voice_run_config(),
            ):
                if event.error_code:
                    logger.error(
                        "ADK error dispatch_log_id=%s: %s - %s",
                        dispatch_log_id,
                        event.error_code,
                        event.error_message,
                    )
                    if event.error_code in [
                        "SAFETY",
                        "PROHIBITED_CONTENT",
                        "BLOCKLIST",
                    ]:
                        transcript_collector.add_error(event.error_code)
                        break
                    continue

                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.inline_data and twilio_stream_sid:
                            payload = twilio_voice.pcm16_to_twilio_payload(
                                part.inline_data.data
                            )
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "media",
                                        "streamSid": twilio_stream_sid,
                                        "media": {"payload": payload},
                                    }
                                )
                            )

                if event.input_transcription:
                    user_text = event.input_transcription.text
                    is_finished = getattr(event.input_transcription, "finished", True)
                    if user_text and user_text.strip():
                        transcript_collector.add_user_speech(user_text, is_finished)

                if event.output_transcription:
                    ai_text = event.output_transcription.text
                    is_finished = getattr(event.output_transcription, "finished", True)
                    if ai_text and ai_text.strip():
                        transcript_collector.add_ai_speech(ai_text, is_finished)

                if event.interrupted:
                    logger.info(
                        "Vendor interrupted dispatch_log_id=%s", dispatch_log_id
                    )
                    transcript_collector.add_interruption()
                    if twilio_stream_sid:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "clear",
                                    "streamSid": twilio_stream_sid,
                                }
                            )
                        )
                    continue

                if event.turn_complete:
                    logger.debug("Turn complete dispatch_log_id=%s", dispatch_log_id)

        except Exception as e:
            logger.exception(
                "ADK run_live error dispatch_log_id=%s: %s", dispatch_log_id, e
            )

    try:
        live_queue = LiveRequestQueue()
        outbound_audio_task = asyncio.create_task(_send_audio_to_twilio())

        while True:
            payload = await websocket.receive_text()
            event = json.loads(payload)
            event_type = (event.get("event") or "").lower()

            if event_type == "start":
                start_payload = event.get("start") or {}
                twilio_stream_sid = start_payload.get("streamSid")
                provider_call_sid = start_payload.get("callSid")
                call_start_time = datetime.now(timezone.utc)

                logger.info(
                    "Twilio stream started dispatch_log_id=%s stream_sid=%s",
                    dispatch_log_id,
                    twilio_stream_sid,
                )

                live_session_service.attach_twilio_stream(
                    session_id=live_session_id,
                    twilio_stream_sid=twilio_stream_sid,
                    provider_call_sid=provider_call_sid,
                )

                greeting = _build_initial_greeting(vendor.get("name", "Vendor"), ticket)
                live_queue.send_content(
                    types.Content(role="user", parts=[types.Part(text=greeting)])
                )
                continue

            if event_type == "media":
                media = event.get("media") or {}
                media_payload = media.get("payload")
                media_track = (media.get("track") or "").lower()

                if not media_payload or not live_queue:
                    continue
                if media_track and media_track != "inbound":
                    continue

                pcm_chunk = twilio_voice.twilio_payload_to_pcm16(media_payload)
                audio_blob = types.Blob(
                    mime_type="audio/pcm;rate=16000", data=pcm_chunk
                )
                live_queue.send_realtime(audio_blob)
                continue

            if event_type in {"stop", "closed"}:
                break

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "Twilio media websocket failed dispatch_log_id=%s", dispatch_log_id
        )
    finally:
        if outbound_audio_task:
            outbound_audio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await outbound_audio_task

        if live_queue:
            live_queue.close()

        duration_seconds = 0
        if call_start_time:
            duration_seconds = int(
                (datetime.now(timezone.utc) - call_start_time).total_seconds()
            )

        final_transcript_json = transcript_collector.get_transcript_json()

        if final_transcript_json:
            try:
                sb = get_supabase()
                sb.table("vendor_dispatch_logs").update(
                    {
                        "transcript": final_transcript_json,
                        "duration_seconds": duration_seconds,
                    }
                ).eq("id", dispatch_log_id).execute()
                logger.info(
                    "Saved transcript for dispatch_log_id=%s duration=%ds",
                    dispatch_log_id,
                    duration_seconds,
                )
            except Exception:
                logger.exception(
                    "Failed to save transcript for dispatch_log_id=%s", dispatch_log_id
                )

        live_session_service.end_session(
            session_id=live_session_id,
            status="ended",
            metadata={"reason": "socket_closed"},
        )
