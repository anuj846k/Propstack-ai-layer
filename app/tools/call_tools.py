"""Call lifecycle tools for rent collection."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.dependencies import get_supabase
from app.integrations import twilio_voice
from app.services.live_session_service import live_session_service


def _envelope(
    status: str,
    message: str,
    data: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict:
    return {
        "status": status,
        "message": message,
        "data": data,
        "error_message": error_message,
    }


def _require_twilio_config() -> tuple[bool, str | None]:
    if not settings.twilio_account_sid:
        return False, "TWILIO_ACCOUNT_SID is not configured"
    if not settings.twilio_auth_token:
        return False, "TWILIO_AUTH_TOKEN is not configured"
    if not settings.twilio_voice_from_number:
        return False, "TWILIO_VOICE_FROM_NUMBER is not configured"
    return True, None


def _insert_call_log(
    *,
    sb,
    landlord_id: str,
    tenant_id: str,
    language: str,
    rent_amount: str,
    days_overdue: str,
    property_name: str,
    unit_number: str,
) -> str | None:
    call_log = (
        sb.table("call_logs")
        .insert(
            {
                "tenant_id": tenant_id,
                "landlord_id": landlord_id,
                "initiated_by": "agent",
                "language_used": language,
                "outcome": "initiated",
            }
        )
        .execute()
    )
    return call_log.data[0]["id"] if call_log.data else None


def _update_call_log_summary(
    sb, call_id: str, summary: str, outcome: str | None = None
) -> None:
    payload: dict[str, Any] = {
        "summary": summary,
    }
    if outcome:
        payload["outcome"] = outcome

    (sb.table("call_logs").update(payload).eq("id", call_id).execute())


def _create_call_log(
    landlord_id: str,
    tenant_id: str,
    tenant_name: str,
    tenant_phone: str,
    language: str,
    rent_amount: str,
    days_overdue: str,
    property_name: str,
    unit_number: str,
    landlord_name: str,
) -> dict:
    sb = get_supabase()

    call_id = _insert_call_log(
        sb=sb,
        landlord_id=landlord_id,
        tenant_id=tenant_id,
        language=language,
        rent_amount=rent_amount,
        days_overdue=days_overdue,
        property_name=property_name,
        unit_number=unit_number,
    )
    if not call_id:
        return _envelope(
            status="error",
            message="Failed to create call log",
            error_message="call_logs insert returned no id",
        )

    is_valid_config, config_error = _require_twilio_config()
    if not is_valid_config:
        _update_call_log_summary(
            sb,
            call_id,
            f"Twilio config error: {config_error}",
            outcome="failed",
        )
        return _envelope(
            status="failed",
            message=config_error or "Twilio is not configured",
            data={
                "call_id": call_id,
                "provider": "twilio_voice",
                "provider_status": "config_error",
                "live_session_enabled": bool(settings.enable_partner_twilio_live),
                "live_session_id": None,
            },
            error_message=config_error,
        )

    try:
        provider = twilio_voice.create_outbound_call(
            to_number=tenant_phone,
            call_id=call_id,
        )
        provider_call_sid = provider["provider_call_sid"]
        provider_status = provider["provider_status"]
        live_session_id = None

        if settings.enable_partner_twilio_live:
            live_record = live_session_service.start_session(
                call_id=call_id,
                source="twilio_outbound",
                provider_call_sid=provider_call_sid,
                metadata={
                    "tenant_id": tenant_id,
                    "tenant_phone": tenant_phone,
                    "landlord_id": landlord_id,
                },
            )
            live_session_id = live_record["session_id"]

        _update_call_log_summary(
            sb,
            call_id,
            (
                "Twilio call created "
                f"sid={provider_call_sid} status={provider_status} "
                f"live_session={live_session_id or 'disabled'} "
                f"for {tenant_name} at {tenant_phone} "
                f"on behalf of {landlord_name}"
            ),
            outcome="initiated",
        )

        return _envelope(
            status="queued",
            message=(
                f"Call queued to {tenant_name} at {tenant_phone} in {language} "
                f"for Rs.{rent_amount} on behalf of {landlord_name}."
            ),
            data={
                "call_id": call_id,
                "provider": "twilio_voice",
                "provider_call_sid": provider_call_sid,
                "provider_status": provider_status,
                "live_session_enabled": bool(settings.enable_partner_twilio_live),
                "live_session_id": live_session_id,
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        error_message = str(exc)
        _update_call_log_summary(
            sb,
            call_id,
            f"Twilio call creation failed: {error_message}",
            outcome="failed",
        )
        return _envelope(
            status="error",
            message="Failed to queue rent collection call",
            data={
                "call_id": call_id,
                "provider": "twilio_voice",
                "provider_status": "failed",
                "live_session_enabled": bool(settings.enable_partner_twilio_live),
                "live_session_id": None,
            },
            error_message=error_message,
        )


async def initiate_rent_collection_call(
    landlord_id: str,
    tenant_id: str,
    tenant_name: str,
    tenant_phone: str,
    language: str,
    rent_amount: str,
    days_overdue: str,
    property_name: str,
    unit_number: str,
    landlord_name: str,
) -> dict:
    """Queue an outbound rent collection call and persist a call log.

    Args:
        landlord_id: UUID of landlord owning the tenancy.
        tenant_id: UUID of the tenant being called.
        tenant_name: Tenant full name.
        tenant_phone: Tenant phone with country code.
        language: Preferred conversation language.
        rent_amount: Amount due as string.
        days_overdue: Days overdue as string.
        property_name: Property context.
        unit_number: Unit context.
        landlord_name: Landlord name used in intro.

    Returns:
        Normalized envelope with status, message, data, error_message.
    """
    try:
        return await asyncio.to_thread(
            _create_call_log,
            landlord_id,
            tenant_id,
            tenant_name,
            tenant_phone,
            language,
            rent_amount,
            days_overdue,
            property_name,
            unit_number,
            landlord_name,
        )
    except Exception as exc:
        return _envelope(
            status="error",
            message="Failed to queue rent collection call",
            error_message=str(exc),
        )


def _update_call_result(
    call_id: str,
    transcript: str,
    outcome: str,
    duration_seconds: int,
    provider_metadata: dict[str, Any] | None,
) -> dict:
    sb = get_supabase()

    metadata_snippet = ""
    if provider_metadata:
        compact = json.dumps(provider_metadata, ensure_ascii=True)
        metadata_snippet = f" metadata={compact[:220]}"

    outcome_value = (outcome or "").strip().lower()
    if outcome_value in {"in_progress", "initiated", "ringing", "queued"}:
        summary = f"Call status updated: {outcome_value}.{metadata_snippet}"
    else:
        summary = f"Call completed: {outcome_value or outcome}.{metadata_snippet}"

    result = (
        sb.table("call_logs")
        .update(
            {
                "transcript": transcript,
                "outcome": outcome,
                "duration_seconds": int(duration_seconds),
                "summary": summary,
            }
        )
        .eq("id", call_id)
        .execute()
    )

    return _envelope(
        status="success",
        message="Call result saved",
        data={
            "call_record": (result.data or [None])[0],
        },
    )


async def save_call_result(
    call_id: str,
    transcript: str,
    outcome: str,
    duration_seconds: int,
    provider_metadata: dict[str, Any] | None = None,
) -> dict:
    """Persist call outcome for a previously created call log."""
    try:
        return await asyncio.to_thread(
            _update_call_result,
            call_id,
            transcript,
            outcome,
            int(duration_seconds),
            provider_metadata,
        )
    except Exception as exc:
        return _envelope(
            status="error",
            message="Failed to save call result",
            error_message=str(exc),
        )


async def save_call_result_from_agent(
    call_id: str,
    transcript: str,
    outcome: str,
    duration_seconds: int,
) -> dict:
    """Persist call outcome for a previously created call log (agent-facing; no provider_metadata).

    Use this when the LLM needs to save a call result. Backend code (Twilio, live session)
    should call save_call_result() directly with provider_metadata.
    """
    return await save_call_result(
        call_id=call_id,
        transcript=transcript,
        outcome=outcome,
        duration_seconds=duration_seconds,
        provider_metadata=None,
    )


ONGOING_OUTCOMES = frozenset({"initiated", "ringing", "in_progress", "queued"})


def _get_call_status_sync(
    landlord_id: str,
    tenant_id: str | None,
    call_id: str | None,
) -> dict:
    sb = get_supabase()
    if call_id and call_id.strip():
        row = (
            sb.table("call_logs")
            .select("*")
            .eq("id", call_id.strip())
            .eq("landlord_id", landlord_id)
            .limit(1)
            .execute()
        )
        calls = row.data or []
    elif tenant_id and tenant_id.strip():
        row = (
            sb.table("call_logs")
            .select("*")
            .eq("tenant_id", tenant_id.strip())
            .eq("landlord_id", landlord_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        calls = row.data or []
    else:
        return {
            "status": "error",
            "message": "Provide either tenant_id (to check latest call for that tenant) or call_id (to check a specific call).",
            "call": None,
            "is_ongoing": False,
        }

    if not calls:
        return {
            "status": "success",
            "message": "No matching call found.",
            "call": None,
            "is_ongoing": False,
        }

    c = calls[0]
    outcome = (c.get("outcome") or "").strip().lower()
    is_ongoing = outcome in ONGOING_OUTCOMES

    tenant_name = "Tenant"
    tid = c.get("tenant_id")
    if tid:
        u = sb.table("users").select("name").eq("id", tid).limit(1).execute()
        if u.data:
            tenant_name = (u.data[0].get("name") or "Tenant").strip()

    return {
        "status": "success",
        "message": "Call found.",
        "call": {
            "call_id": c.get("id"),
            "tenant_id": tid,
            "tenant_name": tenant_name,
            "outcome": outcome or "unknown",
            "is_ongoing": is_ongoing,
            "summary": c.get("summary"),
            "created_at": str(c["created_at"]) if c.get("created_at") is not None else None,
            "duration_seconds": c.get("duration_seconds"),
            "ai_summary": c.get("ai_summary"),
        },
        "is_ongoing": is_ongoing,
    }


async def get_call_status(
    landlord_id: str,
    tenant_id: str = "",
    call_id: str = "",
) -> dict:
    """Check the current status of a rent collection call.

    Use this when the landlord asks for an update on a call (e.g. "How's the call going?",
    "Any update on the call to Anuj Kumar?", "Has the call finished?"). Pass the tenant_id
    if they refer to a tenant by name (use find_tenant_by_name first to get tenant_id), or
    pass call_id if you have the specific call id from context.

    Args:
        landlord_id: UUID of the landlord (from context).
        tenant_id: Optional. UUID of the tenant — returns the latest call for this tenant.
        call_id: Optional. UUID of the call — returns status for this specific call.
        Exactly one of tenant_id or call_id should be provided.

    Returns:
        dict: status, message, call (with call_id, tenant_name, outcome, is_ongoing, summary,
        created_at, duration_seconds), and is_ongoing. If is_ongoing is true, the call is
        still in progress (ringing, in progress, etc.). If false, the call has ended.
    """
    return await asyncio.to_thread(
        _get_call_status_sync,
        landlord_id,
        tenant_id.strip() or None,
        call_id.strip() or None,
    )
