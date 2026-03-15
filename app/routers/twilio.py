import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

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

from app.agents import voice_agent
from app.config import settings
from app.dependencies import get_supabase
from app.integrations import twilio_voice
from app.routers.rent import (
    _find_call_log,
    _form_to_string_dict,
    _resolve_landlord_id_for_call_row,
    _validate_callback_secret,
)
from app.schemas.rent import (
    CallCallbackRequest,
    CallCallbackResponse,
    LiveSessionEndRequest,
    LiveSessionEndResponse,
    LiveSessionStartRequest,
    LiveSessionStartResponse,
    TwilioStatusCallbackResponse,
)
from app.services.live_session_service import live_session_service
from app.services.session_service import get_session_service
from app.tools.call_tools import save_call_result
from app.tools.notification_tools import create_notification
from app.utils.transcript_collector import TranscriptCollector

logger = logging.getLogger(__name__)
router = APIRouter()

voice_session_service = get_session_service()
voice_runner = Runner(
    agent=voice_agent,
    app_name="propstack_rent_voice",
    session_service=voice_session_service,
    auto_create_session=True,
)


def _build_initial_greeting(call_row: dict) -> str:
    """Build dynamic greeting that asks for language preference."""
    tenant_name = call_row.get("tenant_name") or "there"
    tenant_id = call_row.get("tenant_id") or ""
    rent_amount = call_row.get("rent_amount", "0")
    property_name = call_row.get("property_name") or ""

    try:
        amount = float(rent_amount) if rent_amount else 0
        amount_str = f"Rs. {amount:,.0f}"
    except (ValueError, TypeError):
        amount_str = "the rent"

    greeting = f"""Hello {tenant_name}, this is Sara from PropStack calling regarding your outstanding balance of {amount_str}.

Context for this call:
- Tenant User ID: {tenant_id}
- Tenant Name: {tenant_name}
- Property: {property_name or "N/A"}
- Outstanding Balance: {amount_str}

First, ask: "Would you like to continue in English or Hindi?" (ask in English initially)
Then proceed in the tenant's chosen language.

If tenant asks questions you don't know, use the tools to find answers. Then respond in tenant's language."""

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


@router.post("/calls/callback", response_model=CallCallbackResponse)
async def call_callback(
    body: CallCallbackRequest,
    x_callback_secret: str = Header("", alias="X-Callback-Secret"),
) -> CallCallbackResponse:
    _validate_callback_secret(x_callback_secret)

    callback_result = await save_call_result(
        call_id=body.call_id,
        transcript=body.transcript,
        outcome=body.outcome,
        duration_seconds=body.duration_seconds,
        provider_metadata=body.provider_metadata,
    )
    if callback_result.get("status") == "error":
        raise HTTPException(
            status_code=400, detail=callback_result.get("error_message")
        )

    call_row = _find_call_log(body.call_id)
    if not call_row:
        raise HTTPException(status_code=404, detail="Call log not found")

    landlord_id = _resolve_landlord_id_for_call_row(call_row)

    notification_id = None
    if landlord_id:
        notification = await create_notification(
            user_id=landlord_id,
            title="Rent Collection Call Completed",
            message=(
                f"Call {body.call_id} outcome: {body.outcome}. "
                f"Duration: {body.duration_seconds}s"
            ),
            notification_type="rent_due",
        )
        notification_id = (notification.get("data") or {}).get("notification_id")

    return CallCallbackResponse(
        call_id=body.call_id,
        status="success",
        message="Callback saved and landlord notified",
        notification_id=notification_id,
    )


@router.post("/calls/twilio/transcription")
async def twilio_transcription_callback(
    request: Request,
) -> Response:
    """Handle Twilio's transcription callback - save transcript to call_logs."""
    form = await request.form()
    form_data = _form_to_string_dict(form)

    # Twilio sends CallSid (provider_call_sid), not our internal call_id
    call_sid = form_data.get("CallSid", "")
    transcription_text = form_data.get("TranscriptionText", "")
    transcription_status = form_data.get("TranscriptionStatus", "")

    logger.info(
        f"Received transcription for call {call_sid}: status={transcription_status}"
    )

    # Only save if transcription is complete
    if transcription_status == "completed" and transcription_text and call_sid:
        sb = get_supabase()
        # Find the call by provider_call_sid in metadata or use call_id from query
        result = sb.table("call_logs").select("id").execute()

        # Try to find by matching the call_sid in summary or metadata
        # For now, use the call_id from query param if provided
        call_id = request.query_params.get("call_id")

        if call_id:
            sb.table("call_logs").update({"transcript": transcription_text}).eq(
                "id", call_id
            ).execute()
            logger.info(f"Saved transcription for call {call_id}")
        else:
            logger.warning(
                f"No call_id found for transcription callback with CallSid {call_sid}"
            )

    return Response(content="OK", media_type="text/plain")


def _find_call_id_by_provider_sid(call_sid: str) -> str | None:
    """Resolve internal call_logs.id from Twilio CallSid using provider_call_sid column."""
    if not call_sid:
        return None
    sb = get_supabase()
    result = (
        sb.table("call_logs")
        .select("id")
        .eq("provider_call_sid", call_sid)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0].get("id")
    return None


@router.post("/calls/twilio/recording-complete")
async def twilio_recording_complete(
    request: Request,
) -> Response:
    """Handle Twilio's recording completion - for fallback recording when ADK Live not used."""
    form = await request.form()
    form_data = _form_to_string_dict(form)

    call_sid = form_data.get("CallSid", "")
    recording_url = form_data.get("RecordingUrl", "")
    recording_duration = form_data.get("RecordingDuration", "0")

    logger.info(
        "Recording complete for call %s: duration=%ss, url=%s",
        call_sid,
        recording_duration,
        recording_url,
    )

    if call_sid:
        call_id = _find_call_id_by_provider_sid(call_sid)
        if call_id:
            sb = get_supabase()
            sb.table("call_logs").update(
                {
                    "summary": f"Recording available: {recording_url}",
                    "duration_seconds": (
                        int(recording_duration) if recording_duration.isdigit() else 0
                    ),
                }
            ).eq("id", call_id).execute()
            logger.info("Updated call_logs %s with recording info", call_id)
        else:
            logger.warning(
                "No call_log found for Twilio CallSid %s; recording not persisted",
                call_sid,
            )

    return Response(content="OK", media_type="text/plain")


@router.post("/calls/twilio/status", response_model=TwilioStatusCallbackResponse)
async def twilio_status_callback(
    request: Request,
    call_id: str,
    x_twilio_signature: str = Header("", alias="X-Twilio-Signature"),
) -> TwilioStatusCallbackResponse:
    call_row = _find_call_log(call_id)
    if not call_row:
        raise HTTPException(status_code=404, detail="Call log not found")

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

    duration_raw = form_payload.get("CallDuration") or "0"
    try:
        duration_seconds = int(float(duration_raw))
    except ValueError:
        duration_seconds = 0

    sb = get_supabase()
    # Check if there's already a transcript from ADK Live session
    existing = (
        sb.table("call_logs").select("transcript").eq("id", call_id).limit(1).execute()
    )
    has_existing_transcript = existing.data and existing.data[0].get("transcript")

    # Only use status message as transcript if no real transcript exists
    if has_existing_transcript:
        transcript = None  # Don't overwrite - ADK Live will save the real transcript
    else:
        transcript = f"Twilio callback status={mapped['call_status']} sid={provider_call_sid or 'unknown'}"

    update_payload = {
        "outcome": mapped["outcome"],
        "duration_seconds": duration_seconds if mapped["is_terminal"] else 0,
    }
    if provider_call_sid:
        update_payload["provider_call_sid"] = provider_call_sid
    if transcript:
        update_payload["transcript"] = transcript

    result = sb.table("call_logs").update(update_payload).eq("id", call_id).execute()
    if not result.data:
        raise HTTPException(status_code=400, detail="Failed to update call log")

    notification_id = None
    if mapped["is_terminal"]:
        existing_live = live_session_service.find_by_call_id(call_id)
        if existing_live:
            live_session_service.end_session(
                session_id=existing_live["session_id"],
                status="ended",
                metadata={"final_provider_status": mapped["call_status"]},
            )
        landlord_id = _resolve_landlord_id_for_call_row(call_row)
        if landlord_id:
            notification = await create_notification(
                user_id=landlord_id,
                title="Rent Collection Call Status",
                message=(
                    f"Call {call_id} finished with {mapped['outcome']} "
                    f"(provider={mapped['call_status']})."
                ),
                notification_type="rent_due",
            )
            notification_id = (notification.get("data") or {}).get("notification_id")

    return TwilioStatusCallbackResponse(
        call_id=call_id,
        status="success",
        message="Twilio status callback processed",
        provider_call_sid=provider_call_sid,
        provider_status=mapped["call_status"],
        outcome=mapped["outcome"],
        is_terminal=bool(mapped["is_terminal"]),
        notification_id=notification_id,
    )


@router.post("/calls/live/session/start", response_model=LiveSessionStartResponse)
async def start_live_session(body: LiveSessionStartRequest) -> LiveSessionStartResponse:
    call_row = _find_call_log(body.call_id)
    if not call_row:
        raise HTTPException(status_code=404, detail="Call log not found")

    record = live_session_service.start_session(
        call_id=body.call_id,
        source=body.source,
        provider_call_sid=body.provider_call_sid,
        metadata=body.metadata,
    )

    start_result = await save_call_result(
        call_id=body.call_id,
        transcript="Live session started",
        outcome="in_progress",
        duration_seconds=0,
        provider_metadata={
            "provider": "twilio_voice",
            "live_session_id": record["session_id"],
            "source": body.source,
            "metadata": body.metadata or {},
        },
    )
    if start_result.get("status") == "error":
        raise HTTPException(status_code=400, detail=start_result.get("error_message"))

    return LiveSessionStartResponse(
        status="success",
        message="Live session started",
        call_id=body.call_id,
        live_session_id=record["session_id"],
        live_state=record["status"],
        provider_call_sid=record.get("provider_call_sid"),
    )


@router.post("/calls/live/session/end", response_model=LiveSessionEndResponse)
async def end_live_session(body: LiveSessionEndRequest) -> LiveSessionEndResponse:
    if not body.call_id and not body.live_session_id:
        raise HTTPException(
            status_code=400, detail="Provide call_id or live_session_id"
        )

    record = None
    if body.live_session_id:
        record = live_session_service.get_session(body.live_session_id)
    if not record and body.call_id:
        record = live_session_service.find_by_call_id(body.call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Live session not found")

    started_at = datetime.fromisoformat(record["started_at"])
    resolved_duration = body.duration_seconds
    if resolved_duration is None:
        resolved_duration = max(
            int((datetime.now(timezone.utc) - started_at).total_seconds()),
            0,
        )

    ended = live_session_service.end_session(
        session_id=record["session_id"],
        status="ended",
        metadata=body.metadata,
    )
    if not ended:
        raise HTTPException(status_code=404, detail="Live session not found")

    call_id = ended["call_id"]
    transcript = body.transcript or f"Live session ended with outcome={body.outcome}"
    save_result = await save_call_result(
        call_id=call_id,
        transcript=transcript,
        outcome=body.outcome,
        duration_seconds=resolved_duration,
        provider_metadata={
            "provider": "twilio_voice",
            "live_session_id": ended["session_id"],
            "metadata": body.metadata or {},
        },
    )
    if save_result.get("status") == "error":
        raise HTTPException(status_code=400, detail=save_result.get("error_message"))

    call_row = _find_call_log(call_id)
    if call_row:
        landlord_id = _resolve_landlord_id_for_call_row(call_row)
        if landlord_id:
            await create_notification(
                user_id=landlord_id,
                title="Live Rent Call Ended",
                message=f"Call {call_id} ended with outcome={body.outcome}.",
                notification_type="rent_due",
            )

    return LiveSessionEndResponse(
        status="success",
        message="Live session ended",
        call_id=call_id,
        live_session_id=ended["session_id"],
        live_state=ended["status"],
        outcome=body.outcome,
        duration_seconds=int(resolved_duration),
    )


@router.post("/calls/twilio/twiml/{call_id}")
async def twilio_twiml(call_id: str) -> Response:
    twiml = twilio_voice.build_twiml_bootstrap_response(call_id=call_id)
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/calls/live/twilio/media/{call_id}")
async def twilio_media_stream(websocket: WebSocket, call_id: str) -> None:
    """WebSocket for Twilio media stream with ADK Gemini Live integration."""
    call_row = _find_call_log(call_id)
    if not call_row:
        await websocket.close(code=4404, reason="Call log not found")
        return

    await websocket.accept()

    record = live_session_service.start_session(
        call_id=call_id,
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
            logger.warning("Audio transcoding unavailable. Twilio live disabled.")
            if not settings.enable_custom_bridge_fallback:
                await websocket.close(code=1011, reason="Audio transcoding unavailable")
                return

    async def _send_audio_to_twilio():
        """Forward Gemini audio to Twilio in real-time."""
        nonlocal twilio_stream_sid
        if not live_queue:
            return

        try:
            async for event in voice_runner.run_live(
                user_id=call_id,
                session_id=live_session_id,
                live_request_queue=live_queue,
                run_config=_voice_run_config(),
            ):
                # Handle errors first - per ADK docs
                if event.error_code:
                    logger.error(
                        f"ADK error call_id={call_id}: {event.error_code} - {event.error_message}"
                    )
                    if event.error_code in [
                        "SAFETY",
                        "PROHIBITED_CONTENT",
                        "BLOCKLIST",
                    ]:
                        transcript_collector.parts.append(
                            f"[Error: {event.error_code}]"
                        )
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
                    logger.info(f"User interrupted - pausing audio call_id={call_id}")
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
                    logger.debug(f"Turn complete call_id={call_id}")

        except Exception as e:
            logger.exception(f"ADK run_live error call_id={call_id}: {e}")

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
                    "Twilio stream started call_id=%s stream_sid=%s",
                    call_id,
                    twilio_stream_sid,
                )

                live_session_service.attach_twilio_stream(
                    session_id=live_session_id,
                    twilio_stream_sid=twilio_stream_sid,
                    provider_call_sid=provider_call_sid,
                )

                greeting = _build_initial_greeting(call_row)
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
        logger.exception("Twilio media websocket failed call_id=%s", call_id)
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
            await save_call_result(
                call_id=call_id,
                transcript=final_transcript_json,
                outcome="completed",
                duration_seconds=duration_seconds,
                provider_metadata={
                    "live_session_id": live_session_id,
                    "provider": "twilio_voice",
                    "source": "adk_live",
                },
            )

        live_session_service.end_session(
            session_id=live_session_id,
            status="ended",
            metadata={"reason": "socket_closed"},
        )
