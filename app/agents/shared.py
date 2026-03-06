"""Shared utilities for ADK agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.dependencies import get_supabase
from app.services import call_policy_service


def tool_envelope(
    status: str,
    message: str,
    data: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "data": data,
        "error_message": error_message,
    }


def before_tool_guardrail(
    tool,
    args: dict[str, Any] | None = None,
    tool_context=None,
    context=None,
    **_kwargs,
) -> dict | None:
    """Apply deterministic guardrails before tool execution."""
    ctx = tool_context or context
    if ctx is None:
        return tool_envelope(
            status="blocked",
            message="Call blocked: tool context is missing.",
            data={"blocked_by": "before_tool_guardrail"},
            error_message="Missing tool context",
        )
    args = args or {}

    ctx.state["temp:last_requested_tool"] = tool.name
    ctx.state["app:call_window_ist"] = (
        f"{settings.call_window_start_hour:02d}:00-{settings.call_window_end_hour:02d}:00"
    )

    if tool.name != "initiate_rent_collection_call":
        return None

    landlord_id = str(args.get("landlord_id") or "").strip()
    tenant_id = str(args.get("tenant_id") or "").strip()

    if landlord_id:
        ctx.state["user:last_landlord_id"] = landlord_id

    if not landlord_id or not tenant_id:
        return tool_envelope(
            status="blocked",
            message="Call blocked: landlord_id and tenant_id are required.",
            data={"blocked_by": "before_tool_guardrail"},
            error_message="Missing landlord_id or tenant_id",
        )

    sb = get_supabase()
    if not call_policy_service.validate_tenant_landlord_ownership(
        sb, landlord_id, tenant_id
    ):
        return tool_envelope(
            status="blocked",
            message="Call blocked: tenant does not belong to the landlord.",
            data={"blocked_by": "before_tool_guardrail"},
            error_message="Landlord/tenant ownership validation failed",
        )

    limits = call_policy_service.get_policy_limits()
    attempts_today = call_policy_service.count_call_attempts_today(
        sb,
        tenant_id=tenant_id,
        landlord_id=landlord_id,
        now_utc=datetime.now(timezone.utc),
    )

    allowed, reason = call_policy_service.evaluate_call_policy(
        attempts_today=attempts_today,
        now_utc=datetime.now(timezone.utc),
        start_hour_ist=limits["start_hour_ist"],
        end_hour_ist=limits["end_hour_ist"],
        max_attempts_per_day=limits["max_attempts_per_day"],
    )

    ctx.state["temp:last_call_attempts_today"] = attempts_today
    if not allowed:
        return tool_envelope(
            status="blocked",
            message=reason,
            data={
                "blocked_by": "before_tool_guardrail",
                "attempts_today": attempts_today,
            },
            error_message=reason,
        )

    return None


def after_tool_normalizer(
    tool,
    args: dict[str, Any] | None = None,
    tool_context=None,
    context=None,
    tool_response: dict | None = None,
    response: dict | None = None,
    **_kwargs,
) -> dict:
    """Normalize tool outputs into a consistent status/message/data envelope."""
    ctx = tool_context or context
    args = args or {}
    raw_response = tool_response if tool_response is not None else response

    if (
        isinstance(raw_response, dict)
        and "status" in raw_response
        and "message" in raw_response
        and "data" in raw_response
    ):
        normalized = {
            "status": raw_response.get("status"),
            "message": raw_response.get("message"),
            "data": raw_response.get("data"),
            "error_message": raw_response.get("error_message"),
        }
    else:
        normalized = tool_envelope(
            status="success",
            message=f"{tool.name} completed",
            data=raw_response
            if isinstance(raw_response, dict)
            else {"result": raw_response},
            error_message=None,
        )

    if ctx is not None:
        ctx.state["temp:last_tool_status"] = normalized.get("status")
        ctx.state["temp:last_tool_summary"] = f"{tool.name}:{normalized.get('status')}"

    landlord_id = args.get("landlord_id")
    if landlord_id and ctx is not None:
        ctx.state["user:last_landlord_id"] = landlord_id

    return normalized
