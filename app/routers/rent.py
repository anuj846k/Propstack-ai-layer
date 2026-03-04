import asyncio
import contextlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import StreamingResponse
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.genai import types

from app.agents.rent_collection.agent import root_agent, voice_agent
from app.config import settings
from app.dependencies import get_supabase, verify_internal_request
from app.integrations import twilio_voice
from app.schemas.rent import (
    CallCallbackRequest,
    CallCallbackResponse,
    CallInitiationRequest,
    CallInitiationResponse,
    ChatRequest,
    LiveSessionEndRequest,
    LiveSessionEndResponse,
    LiveSessionStartRequest,
    LiveSessionStartResponse,
    SweepAction,
    SweepRequest,
    SweepResponse,
    TwilioStatusCallbackResponse,
)
from app.services import call_policy_service, rent_cycle_service
from app.services.live_session_service import live_session_service
from app.tools.call_tools import initiate_rent_collection_call, save_call_result
from app.tools.notification_tools import create_notification
from app.tools.rent_tools import (
    get_tenant_collection_history,
    get_tenant_payment_history,
    get_tenants_with_rent_status,
)

logger = logging.getLogger(__name__)

router = APIRouter()

session_service = InMemorySessionService()
runner = Runner(
    agent=root_agent,
    app_name="propstack_rent",
    session_service=session_service,
    auto_create_session=True,
)

voice_session_service = InMemorySessionService()
voice_runner = Runner(
    agent=voice_agent,
    app_name="propstack_rent_voice",
    session_service=voice_session_service,
    auto_create_session=True,
)


def _run_config(streaming_mode: StreamingMode = StreamingMode.NONE) -> RunConfig:
    return RunConfig(
        streaming_mode=streaming_mode,
        max_llm_calls=settings.adk_max_llm_calls,
        support_cfc=False,
    )


async def _stream_agent(
    user_id: str, session_id: str, prompt: str, landlord_id: str | None
):
    """Run the rent collection agent and stream text deltas."""
    full_prompt = prompt
    if landlord_id:
        # Provide landlord_id as context to the agent so it doesn't need to ask for it.
        full_prompt = (
            f"{prompt}\n\n"
            f"[Context: The authenticated landlord ID for this conversation is {landlord_id}. "
            f"Do NOT ask the user to provide their landlord ID. "
            f"Use this landlord_id when calling tools that require it.]"
        )
    message = types.Content(
        role="user",
        parts=[types.Part(text=full_prompt)],
    )
    event_count = 0
    partial_count = 0
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=message,
        run_config=_run_config(streaming_mode=StreamingMode.SSE),
    ):
        event_count += 1
        if event.content and event.content.parts:
            has_text = any(p.text for p in event.content.parts)
            has_fc = any(p.function_call for p in event.content.parts)
            if has_text and not has_fc:
                text = "".join(p.text or "" for p in event.content.parts)
                if event.partial:
                    partial_count += 1
                    yield text
                else:
                    final_text = text
    if partial_count == 0 and final_text:
        yield final_text
    logger.debug(
        "Stream complete: events=%d partial=%d",
        event_count,
        partial_count,
    )


async def _run_agent(user_id: str, prompt: str) -> str:
    """Run the rent collection agent with a prompt and return the final text."""
    session = await session_service.create_session(
        app_name="propstack_rent",
        user_id=user_id,
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=prompt)],
    )

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=message,
        run_config=_run_config(),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text or ""

    return final_text


def _resolve_landlord_id(landlord_id: str) -> str:
    """Normalize landlord_id.

    Demo landlord aliasing has been removed; always use the explicit landlord_id.
    """
    return landlord_id


def _chunk_text_for_streaming(text: str, chunk_size: int = 4) -> list[str]:
    if not text:
        return []
    words = re.split(r"(\s+)", text)
    chunks: list[str] = []
    current: list[str] = []
    char_count = 0
    for w in words:
        current.append(w)
        char_count += len(w)
        if char_count >= chunk_size or (w.strip() and len(current) >= 2):
            chunks.append("".join(current))
            current = []
            char_count = 0
    if current:
        chunks.append("".join(current))
    return chunks


def _extract_landlord_id_from_initiated_by(initiated_by: str | None) -> str | None:
    if not initiated_by:
        return None
    if initiated_by.startswith("agent:"):
        return initiated_by.split(":", 1)[1]
    return None


async def _chat_stream(request: ChatRequest, landlord_id: str | None = None):
    session_id = request.session_id or str(uuid.uuid4())
    async for chunk in _stream_agent(
        user_id=request.user_id,
        session_id=session_id,
        prompt=request.message,
        landlord_id=landlord_id,
    ):
        if not chunk:
            continue
        subs = _chunk_text_for_streaming(chunk)
        if len(subs) > 3:
            for sub in subs:
                yield sub
                await asyncio.sleep(0.02)
        else:
            yield chunk


def _extract_call_result_data(result: dict) -> tuple[str | None, str | None, str, str]:
    data = result.get("data") or {}
    call_id = data.get("call_id") or result.get("call_id")
    provider_status = data.get("provider_status") or result.get("provider_status")
    status = result.get("status") or "error"
    message = result.get("message") or "Call failed"
    if status == "error" and result.get("error_message"):
        message = f"{message}: {result.get('error_message')}"
    return call_id, provider_status, status, message


def _extract_provider_call_sid(result: dict) -> str | None:
    data = result.get("data") or {}
    return data.get("provider_call_sid")


def _extract_live_session_details(result: dict) -> tuple[bool, str | None]:
    data = result.get("data") or {}
    return bool(data.get("live_session_enabled")), data.get("live_session_id")


def _validate_scheduler_token(token: str) -> None:
    if not settings.internal_scheduler_token:
        raise HTTPException(
            status_code=500,
            detail="INTERNAL_SCHEDULER_TOKEN is not configured",
        )
    if token != settings.internal_scheduler_token:
        raise HTTPException(status_code=401, detail="Invalid internal scheduler token")


def _validate_callback_secret(secret: str) -> None:
    if not settings.callback_shared_secret:
        return
    if secret != settings.callback_shared_secret:
        raise HTTPException(status_code=401, detail="Invalid callback secret")


def _find_landlord_name(landlord_id: str) -> str:
    sb = get_supabase()
    result = (
        sb.table("users")
        .select("name")
        .eq("id", landlord_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0].get("name") or "Landlord"
    return "Landlord"


def _find_call_log(call_id: str) -> dict | None:
    sb = get_supabase()
    call_row = (
        sb.table("call_logs")
        .select("id, tenant_id, landlord_id, initiated_by")
        .eq("id", call_id)
        .limit(1)
        .execute()
    )
    if not call_row.data:
        return None
    
    data = call_row.data[0]
    
    # Fetch tenant details for dynamic greeting
    # tenant_id in call_logs is actually a user_id (tenant)
    if data.get("tenant_id"):
        # Find the active tenancy for this tenant
        tenancy = (
            sb.table("tenancies")
            .select("""
                id,
                users!tenancies_tenant_id_fkey(name, phone),
                units!tenancies_unit_id_fkey(rent_amount, properties!units_property_id_fkey(name))
            """)
            .eq("tenant_id", data["tenant_id"])
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if tenancy.data and tenancy.data[0]:
            tenant_data = tenancy.data[0]
            user_data = tenant_data.get("users") or {}
            unit_data = tenant_data.get("units") or {}
            property_data = unit_data.get("properties") or {}
            
            data["tenant_name"] = user_data.get("name")
            data["tenant_phone"] = user_data.get("phone")
            data["rent_amount"] = unit_data.get("rent_amount")
            data["property_name"] = property_data.get("name")
    
    return data


def _resolve_landlord_id_for_call_row(call_row: dict) -> str | None:
    sb = get_supabase()
    landlord_id = call_row.get("landlord_id")
    tenant_id = call_row.get("tenant_id")
    if not landlord_id:
        landlord_id = _extract_landlord_id_from_initiated_by(call_row.get("initiated_by"))

    if not landlord_id and tenant_id:
        tenancy = (
            sb.table("tenancies")
            .select("units(properties(landlord_id))")
            .eq("tenant_id", tenant_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if tenancy.data:
            unit = tenancy.data[0].get("units") or {}
            prop = unit.get("properties") or {}
            landlord_id = prop.get("landlord_id")

    return landlord_id


class TranscriptCollector:
    """Collects conversation transcript in JSON format for call logging."""

    def __init__(self):
        self.parts = []
        self._finalized_texts = set()  # Track finalized texts to avoid duplicates

    def _is_duplicate(self, text: str) -> bool:
        """Check if this text was already saved as a finalized entry."""
        return text in self._finalized_texts

    def _mark_finalized(self, text: str):
        """Mark text as finalized so we don't add duplicates."""
        self._finalized_texts.add(text)

    def add_user_speech(self, text: str, is_final: bool = True):
        # Only save finalized (complete) transcriptions
        if text and text.strip() and is_final:
            # Check for duplicates
            if self._is_duplicate(text.strip()):
                return
            self._mark_finalized(text.strip())
            self.parts.append({
                "speaker": "user",
                "text": text.strip(),
                "is_final": is_final
            })

    def add_ai_speech(self, text: str, is_final: bool = True):
        # Only save finalized (complete) transcriptions
        if text and text.strip() and is_final:
            # Check for duplicates
            if self._is_duplicate(text.strip()):
                return
            self._mark_finalized(text.strip())
            self.parts.append({
                "speaker": "sara",
                "text": text.strip(),
                "is_final": is_final
            })

    def add_interruption(self):
        self.parts.append({
            "speaker": "system",
            "text": "[User interrupted]",
            "is_final": True
        })

    def add_error(self, error: str):
        self.parts.append({
            "speaker": "system",
            "text": f"[Error: {error}]",
            "is_final": True
        })

    def get_transcript_json(self) -> str:
        import json
        return json.dumps(self.parts, ensure_ascii=False, indent=2)

    def get_transcript_text(self) -> str:
        lines = []
        for part in self.parts:
            speaker = part.get("speaker", "unknown")
            text = part.get("text", "")
            if speaker == "user":
                lines.append(f"User: {text}")
            elif speaker == "sara":
                lines.append(f"Sara: {text}")
            else:
                lines.append(text)
        return "\n".join(lines)

    def get_transcript(self) -> str:
        return self.get_transcript_text()


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


def _form_to_string_dict(form_data) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key, value in form_data.items():
        payload[str(key)] = str(value)
    return payload


@router.post("/chat")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    x_internal_secret: str = Depends(verify_internal_request),
    x_landlord_id: str | None = Header(None, alias="x-landlord-id"),
):
    landlord_id = x_landlord_id or request.landlord_id

    if landlord_id:
        landlord_id = _resolve_landlord_id(landlord_id)
    
    if not landlord_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please log in through the app."
        )
    
    return StreamingResponse(
        _chat_stream(request, landlord_id),
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/check-rent")
async def check_rent(landlord_id: str) -> dict:
    landlord_id = _resolve_landlord_id(landlord_id)
    result = await _run_agent(
        user_id=landlord_id,
        prompt=(
            f"Check which tenants owe rent. "
            f"My landlord ID is {landlord_id}"
        ),
    )
    return {"result": result}


@router.post("/initiate-call", response_model=CallInitiationResponse)
async def initiate_rent_call(body: CallInitiationRequest) -> CallInitiationResponse:
    landlord_id = _resolve_landlord_id(body.landlord_id)

    tenants_result = await get_tenants_with_rent_status(landlord_id)
    if tenants_result.get("status") == "error":
        raise HTTPException(status_code=500, detail=tenants_result.get("error_message"))

    tenant = next(
        (t for t in tenants_result.get("tenants", []) if t.get("tenant_id") == body.tenant_id),
        None,
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found for landlord")

    if not tenant.get("is_overdue"):
        return CallInitiationResponse(
            call_id=None,
            status="failed",
            message="Tenant is not overdue; call was not initiated.",
            provider_status=None,
        )

    sb = get_supabase()
    if not call_policy_service.validate_tenant_landlord_ownership(sb, landlord_id, body.tenant_id):
        raise HTTPException(status_code=403, detail="Tenant does not belong to landlord")

    limits = call_policy_service.get_policy_limits()
    attempts_today = call_policy_service.count_call_attempts_today(
        sb,
        tenant_id=body.tenant_id,
        landlord_id=landlord_id,
    )
    allowed, reason = call_policy_service.evaluate_call_policy(
        attempts_today=attempts_today,
        now_utc=datetime.now(timezone.utc),
        start_hour_ist=limits["start_hour_ist"],
        end_hour_ist=limits["end_hour_ist"],
        max_attempts_per_day=limits["max_attempts_per_day"],
    )
    if not allowed:
        return CallInitiationResponse(
            call_id=None,
            status="failed",
            message=reason,
            provider_status=None,
        )

    # Deterministic pre-call context fetches used for summary and policy traceability.
    payment_history = await get_tenant_payment_history(body.tenant_id)
    collection_history = await get_tenant_collection_history(body.tenant_id)
    logger.info(
        "Pre-call context tenant=%s payments=%s past_calls=%s",
        body.tenant_id,
        payment_history.get("total_payments"),
        collection_history.get("total_past_calls"),
    )

    call_result = await initiate_rent_collection_call(
        landlord_id=landlord_id,
        tenant_id=body.tenant_id,
        tenant_name=tenant.get("tenant_name") or body.tenant_name,
        tenant_phone=tenant.get("tenant_phone") or "",
        language=tenant.get("preferred_language") or "english",
        rent_amount=str(tenant.get("rent_amount") or "0"),
        days_overdue=str(tenant.get("days_overdue") or "0"),
        property_name=tenant.get("property_name") or "",
        unit_number=tenant.get("unit_number") or "",
        landlord_name=_find_landlord_name(landlord_id),
    )

    call_id, provider_status, status, message = _extract_call_result_data(call_result)
    provider_call_sid = _extract_provider_call_sid(call_result)
    live_session_enabled, live_session_id = _extract_live_session_details(call_result)
    return CallInitiationResponse(
        call_id=call_id,
        status=status,
        message=message,
        provider_status=provider_status,
        provider_call_sid=provider_call_sid,
        live_session_enabled=live_session_enabled,
        live_session_id=live_session_id,
    )


@router.post("/rent/sweep", response_model=SweepResponse)
async def run_rent_sweep(
    body: SweepRequest,
    x_internal_token: str = Header("", alias="X-Internal-Token"),
) -> SweepResponse:
    _validate_scheduler_token(x_internal_token)

    month = body.month or rent_cycle_service.period_month_for_date()
    try:
        rent_cycle_service.build_rent_timeline(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sb = get_supabase()
    candidates = rent_cycle_service.list_overdue_candidates(sb, month)

    processed = 0
    called = 0
    skipped = 0
    errors = 0
    actions: list[SweepAction] = []

    limits = call_policy_service.get_policy_limits()

    for candidate in candidates:
        processed += 1
        tenant_id = candidate["tenant_id"]
        tenancy_id = candidate["tenancy_id"]
        landlord_id = candidate["landlord_id"]

        overdue_update = rent_cycle_service.mark_candidate_cycle_overdue(sb, candidate, month)
        if overdue_update["status"] == "error":
            errors += 1
            actions.append(
                SweepAction(
                    tenant_id=tenant_id,
                    tenancy_id=tenancy_id,
                    landlord_id=landlord_id,
                    action="error",
                    reason=overdue_update.get("error_message") or "Failed to mark overdue",
                    call_id=None,
                )
            )
            continue

        attempts_today = call_policy_service.count_call_attempts_today(
            sb,
            tenant_id=tenant_id,
            landlord_id=landlord_id,
        )
        allowed, reason = call_policy_service.evaluate_call_policy(
            attempts_today=attempts_today,
            now_utc=datetime.now(timezone.utc),
            start_hour_ist=limits["start_hour_ist"],
            end_hour_ist=limits["end_hour_ist"],
            max_attempts_per_day=limits["max_attempts_per_day"],
        )

        if not allowed:
            skipped += 1
            actions.append(
                SweepAction(
                    tenant_id=tenant_id,
                    tenancy_id=tenancy_id,
                    landlord_id=landlord_id,
                    action="skipped",
                    reason=reason,
                    call_id=None,
                )
            )
            continue

        # Collection operation path: fetch payment + call history before a call.
        payment_history = await get_tenant_payment_history(tenant_id)
        call_history = await get_tenant_collection_history(tenant_id)

        if body.dry_run:
            skipped += 1
            actions.append(
                SweepAction(
                    tenant_id=tenant_id,
                    tenancy_id=tenancy_id,
                    landlord_id=landlord_id,
                    action="skipped",
                    reason=(
                        "dry_run: candidate ready for call "
                        f"(payments={payment_history.get('total_payments', 0)}, "
                        f"past_calls={call_history.get('total_past_calls', 0)})"
                    ),
                    call_id=None,
                )
            )
            continue

        call_result = await initiate_rent_collection_call(
            landlord_id=landlord_id,
            tenant_id=tenant_id,
            tenant_name=candidate.get("tenant_name") or "Tenant",
            tenant_phone=candidate.get("tenant_phone") or "",
            language=candidate.get("preferred_language") or "english",
            rent_amount=str(candidate.get("amount_outstanding") or 0),
            days_overdue=str(candidate.get("days_overdue") or 0),
            property_name=candidate.get("property_name") or "",
            unit_number=candidate.get("unit_number") or "",
            landlord_name=candidate.get("landlord_name") or "Landlord",
        )

        call_id, _, call_status, call_message = _extract_call_result_data(call_result)
        if call_status in {"queued", "initiated", "success"}:
            called += 1
            notification = await create_notification(
                user_id=landlord_id,
                title="Rent Collection Call Queued",
                message=(
                    f"{candidate.get('tenant_name')} ({candidate.get('unit_number')}) "
                    f"overdue for {candidate.get('period_month')}. "
                    f"Call status: {call_status}."
                ),
                notification_type="rent_due",
            )
            if notification.get("status") != "success":
                logger.warning(
                    "Failed landlord notification for call_id=%s landlord=%s",
                    call_id,
                    landlord_id,
                )

            actions.append(
                SweepAction(
                    tenant_id=tenant_id,
                    tenancy_id=tenancy_id,
                    landlord_id=landlord_id,
                    action="called",
                    reason=call_message,
                    call_id=call_id,
                )
            )
        else:
            errors += 1
            actions.append(
                SweepAction(
                    tenant_id=tenant_id,
                    tenancy_id=tenancy_id,
                    landlord_id=landlord_id,
                    action="error",
                    reason=call_message,
                    call_id=call_id,
                )
            )

    return SweepResponse(
        month=month,
        mode=body.mode,
        dry_run=body.dry_run,
        processed=processed,
        called=called,
        skipped=skipped,
        errors=errors,
        actions=actions,
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
        raise HTTPException(status_code=400, detail=callback_result.get("error_message"))

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

    transcript = (
        f"Twilio callback status={mapped['call_status']} sid={provider_call_sid or 'unknown'}"
    )
    result = await save_call_result(
        call_id=call_id,
        transcript=transcript,
        outcome=mapped["outcome"],
        duration_seconds=duration_seconds if mapped["is_terminal"] else 0,
        provider_metadata={
            "provider": "twilio_voice",
            "twilio": form_payload,
        },
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error_message"))

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
        raise HTTPException(status_code=400, detail="Provide call_id or live_session_id")

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


@router.websocket("/live/browser/{session_id}")
async def browser_live_stream(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready", "session_id": session_id})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"message": raw}

            message = str(payload.get("message") or "").strip()
            if not message:
                await websocket.send_json({"type": "error", "message": "message is required"})
                continue

            user_id = str(payload.get("user_id") or "live-browser-user")
            landlord_id = payload.get("landlord_id")

            async for chunk in _stream_agent(
                user_id=user_id,
                session_id=session_id,
                prompt=message,
                landlord_id=landlord_id,
            ):
                if chunk:
                    await websocket.send_json({"type": "delta", "text": chunk})

            await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        return


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
                    logger.error(f"ADK error call_id={call_id}: {event.error_code} - {event.error_message}")
                    if event.error_code in ["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"]:
                        transcript_collector.parts.append(f"[Error: {event.error_code}]")
                        break
                    continue

                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.inline_data and twilio_stream_sid:
                            payload = twilio_voice.pcm16_to_twilio_payload(
                                part.inline_data.data
                            )
                            await websocket.send_text(json.dumps({
                                "event": "media",
                                "streamSid": twilio_stream_sid,
                                "media": {"payload": payload}
                            }))

                if event.input_transcription:
                    user_text = event.input_transcription.text
                    is_finished = getattr(event.input_transcription, 'finished', True)
                    if user_text and user_text.strip():
                        transcript_collector.add_user_speech(user_text, is_finished)

                if event.output_transcription:
                    ai_text = event.output_transcription.text
                    is_finished = getattr(event.output_transcription, 'finished', True)
                    if ai_text and ai_text.strip():
                        transcript_collector.add_ai_speech(ai_text, is_finished)

                if event.interrupted:
                    logger.info(f"User interrupted - pausing audio call_id={call_id}")
                    transcript_collector.add_interruption()
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
                    call_id, twilio_stream_sid
                )

                live_session_service.attach_twilio_stream(
                    session_id=live_session_id,
                    twilio_stream_sid=twilio_stream_sid,
                    provider_call_sid=provider_call_sid,
                )

                greeting = _build_initial_greeting(call_row)
                live_queue.send_content(types.Content(
                    role="user",
                    parts=[types.Part(text=greeting)]
                ))
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
                    mime_type="audio/pcm;rate=16000",
                    data=pcm_chunk
                )
                live_queue.send_realtime(audio_blob)
                continue

            if event_type in {"stop", "closed"}:
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
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
            duration_seconds = int((datetime.now(timezone.utc) - call_start_time).total_seconds())

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
