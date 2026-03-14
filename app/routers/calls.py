"""Rent call list, detail, and AI analysis endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_supabase
from app.routers.properties import get_landlord_id_from_request
from app.services.call_analysis_service import generate_call_analysis

router = APIRouter()

logger = logging.getLogger(__name__)


class CallListItem(BaseModel):
    id: str
    tenant_name: str
    unit_number: str
    property_name: str
    outcome: str | None
    created_at: str | None
    duration_seconds: int | None
    language_used: str | None


class CallListResponse(BaseModel):
    calls: list[CallListItem]


class CallDetailResponse(BaseModel):
    id: str
    tenant_id: str
    tenant_name: str
    unit_number: str
    property_name: str
    outcome: str | None
    summary: str | None
    transcript: str | None
    created_at: str | None
    duration_seconds: int | None
    language_used: str | None
    # Stored AI analysis (from call_logs; null until analysis has been run once)
    ai_summary: str | None = None
    promise_amount: str | None = None
    promise_date: str | None = None
    sentiment: str | None = None


class AnalysisRequestBody(BaseModel):
    event_data: dict[str, Any] | None = Field(None, description="Optional ADK-style event payload for context")


class AnalysisResponse(BaseModel):
    summary: str
    promise_amount: str | None
    promise_date: str | None
    sentiment: str


def _enrich_calls_with_tenant_and_unit(sb, calls: list[dict], landlord_id: str) -> list[CallListItem]:
    if not calls:
        return []
    tenant_ids = list({c.get("tenant_id") for c in calls if c.get("tenant_id")})
    users_res = sb.table("users").select("id, name").in_("id", tenant_ids).execute()
    users = {u["id"]: (u.get("name") or "Tenant") for u in (users_res.data or [])}

    tenancies_res = (
        sb.table("tenancies")
        .select("tenant_id, units(unit_number, properties(name))")
        .eq("status", "active")
        .in_("tenant_id", tenant_ids)
        .execute()
    )
    tenant_to_unit_prop: dict[str, tuple[str, str]] = {}
    for t in tenancies_res.data or []:
        tid = t.get("tenant_id")
        units = t.get("units")
        if isinstance(units, list) and units:
            units = units[0]
        if isinstance(units, dict):
            unit_number = units.get("unit_number") or ""
            prop = units.get("properties")
            if isinstance(prop, list) and prop:
                prop = prop[0]
            prop_name = (prop.get("name") or "") if isinstance(prop, dict) else ""
        else:
            unit_number = ""
            prop_name = ""
        if tid:
            tenant_to_unit_prop[tid] = (unit_number, prop_name)

    out: list[CallListItem] = []
    for c in calls:
        tid = c.get("tenant_id") or ""
        unit_number, property_name = tenant_to_unit_prop.get(tid, ("", ""))
        out.append(
            CallListItem(
                id=c.get("id") or "",
                tenant_name=users.get(tid, "Tenant"),
                unit_number=unit_number,
                property_name=property_name,
                outcome=c.get("outcome"),
                created_at=str(c["created_at"]) if c.get("created_at") is not None else None,
                duration_seconds=c.get("duration_seconds"),
                language_used=c.get("language_used"),
            )
        )
    return out


@router.get("/calls", response_model=CallListResponse)
async def list_calls(
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> CallListResponse:
    """List rent collection calls for the authenticated landlord."""
    sb = get_supabase()
    result = (
        sb.table("call_logs")
        .select("*")
        .eq("landlord_id", landlord_id)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    calls = result.data or []
    items = _enrich_calls_with_tenant_and_unit(sb, calls, landlord_id)
    return CallListResponse(calls=items)


@router.get("/calls/{call_id}", response_model=CallDetailResponse)
async def get_call(
    call_id: str,
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> CallDetailResponse:
    """Get a single call's detail (transcript, summary, tenant, unit, property)."""
    sb = get_supabase()
    row = (
        sb.table("call_logs")
        .select("*")
        .eq("id", call_id)
        .eq("landlord_id", landlord_id)
        .limit(1)
        .execute()
    )
    if not row.data:
        raise HTTPException(status_code=404, detail="Call not found")
    c = row.data[0]
    tenant_id = c.get("tenant_id") or ""

    tenant_name = "Tenant"
    unit_number = ""
    property_name = ""
    if tenant_id:
        user_res = sb.table("users").select("name").eq("id", tenant_id).limit(1).execute()
        if user_res.data:
            tenant_name = user_res.data[0].get("name") or "Tenant"
        ten_res = (
            sb.table("tenancies")
            .select("units(unit_number, properties(name))")
            .eq("tenant_id", tenant_id)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if ten_res.data:
            units = ten_res.data[0].get("units")
            if isinstance(units, list) and units:
                units = units[0]
            if isinstance(units, dict):
                unit_number = units.get("unit_number") or ""
                prop = units.get("properties")
                if isinstance(prop, list) and prop:
                    prop = prop[0]
                property_name = (prop.get("name") or "") if isinstance(prop, dict) else ""

    return CallDetailResponse(
        id=c.get("id") or "",
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        unit_number=unit_number,
        property_name=property_name,
        outcome=c.get("outcome"),
        summary=c.get("summary"),
        transcript=c.get("transcript"),
        created_at=str(c["created_at"]) if c.get("created_at") is not None else None,
        duration_seconds=c.get("duration_seconds"),
        language_used=c.get("language_used"),
        ai_summary=c.get("ai_summary"),
        promise_amount=c.get("promise_amount"),
        promise_date=c.get("promise_date"),
        sentiment=c.get("sentiment"),
    )


def _analysis_error_message(exc: Exception) -> str:
    """Return a clear, safe error message for analysis failures."""
    msg = str(exc).lower()
    if "column" in msg and "does not exist" in msg:
        return (
            "Database migration required: add ai_summary, promise_amount, promise_date, sentiment to call_logs. "
            "Run propstack-ai/supabase/migrations/20260313000000_add_call_analysis_columns.sql in Supabase SQL Editor."
        )
    if "api_key" in msg or "google" in msg or "401" in msg:
        return "Google API key missing or invalid. Set GOOGLE_API_KEY in propstack-ai/.env"
    return str(exc)


@router.post("/calls/{call_id}/analysis", response_model=AnalysisResponse)
async def get_call_analysis(
    call_id: str,
    body: AnalysisRequestBody | None = None,
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> AnalysisResponse:
    """Return stored Sara's analysis from DB; generate and save once if not yet present."""
    sb = get_supabase()
    try:
        row = (
            sb.table("call_logs")
            .select("transcript, ai_summary, promise_amount, promise_date, sentiment")
            .eq("id", call_id)
            .eq("landlord_id", landlord_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=_analysis_error_message(e),
        ) from e

    if not row.data:
        raise HTTPException(status_code=404, detail="Call not found")
    c = row.data[0]
    stored_summary = c.get("ai_summary")

    if stored_summary and stored_summary.strip():
        return AnalysisResponse(
            summary=stored_summary.strip(),
            promise_amount=c.get("promise_amount"),
            promise_date=c.get("promise_date"),
            sentiment=(c.get("sentiment") or "neutral").strip(),
        )

    transcript = c.get("transcript")
    event_data = (body.event_data if body else None) or None
    try:
        result = await generate_call_analysis(transcript=transcript, event_data=event_data)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=_analysis_error_message(e),
        ) from e

    # Only store successful results; errors will be regenerated on next request
    if result.get("status") == "success":
        try:
            sb.table("call_logs").update(
                {
                    "ai_summary": result["summary"],
                    "promise_amount": result.get("promiseAmount"),
                    "promise_date": result.get("promiseDate"),
                    "sentiment": result.get("sentiment") or "neutral",
                }
            ).eq("id", call_id).eq("landlord_id", landlord_id).execute()
        except Exception as e:
            logger.warning("Failed to store call analysis: %s", e)

    return AnalysisResponse(
        summary=result["summary"],
        promise_amount=result.get("promiseAmount"),
        promise_date=result.get("promiseDate"),
        sentiment=result.get("sentiment") or "neutral",
    )
