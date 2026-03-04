"""Rent collection ADK agent with tool guardrails and normalized tool envelopes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from google.adk.agents import LlmAgent
from google.genai import types

from app.config import settings
from app.dependencies import get_supabase
from app.services import call_policy_service
from app.tools.call_tools import initiate_rent_collection_call, save_call_result
from app.tools.notification_tools import create_notification
from app.tools.rent_tools import (
    get_tenant_collection_history,
    get_tenant_payment_history,
    get_tenants_with_rent_status,
    list_units_for_landlord,
)
from app.tools.tenant_tools import find_tenant_by_name, find_tenant_by_phone
from app.tools.voice_tools import get_tenant_details


def _tool_envelope(
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


def _before_tool_guardrail(
    tool,
    args: dict[str, Any] | None = None,
    tool_context=None,
    context=None,
    **_kwargs,
) -> dict | None:
    """Apply deterministic guardrails before tool execution."""
    ctx = tool_context or context
    if ctx is None:
        return _tool_envelope(
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
        return _tool_envelope(
            status="blocked",
            message="Call blocked: landlord_id and tenant_id are required.",
            data={"blocked_by": "before_tool_guardrail"},
            error_message="Missing landlord_id or tenant_id",
        )

    sb = get_supabase()
    if not call_policy_service.validate_tenant_landlord_ownership(sb, landlord_id, tenant_id):
        return _tool_envelope(
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
        return _tool_envelope(
            status="blocked",
            message=reason,
            data={
                "blocked_by": "before_tool_guardrail",
                "attempts_today": attempts_today,
            },
            error_message=reason,
        )

    return None


def _after_tool_normalizer(
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
        normalized = _tool_envelope(
            status="success",
            message=f"{tool.name} completed",
            data=raw_response if isinstance(raw_response, dict) else {"result": raw_response},
            error_message=None,
        )

    if ctx is not None:
        ctx.state["temp:last_tool_status"] = normalized.get("status")
        ctx.state["temp:last_tool_summary"] = f"{tool.name}:{normalized.get('status')}"

    landlord_id = args.get("landlord_id")
    if landlord_id and ctx is not None:
        ctx.state["user:last_landlord_id"] = landlord_id

    return normalized


root_agent = LlmAgent(
    name="rent_collection_agent",
    model=settings.gemini_model,
    description=(
        "Manages rent collection by checking payment status, "
        "calling overdue tenants, and logging outcomes for the landlord dashboard."
    ),
    instruction="""
# Identity
You are Sara, Rent Collection Coordinator at PropStack.

# Mission
Handle rent collections while following deterministic guardrails and tool protocols.

# IMPORTANT - Conversation Rules
- If the user just says hi, hello, hey, or any casual greeting, respond with a friendly greeting and ask how you can help.
- Do NOT automatically fetch tenant data, payment history, or call history unless explicitly asked.
- Do NOT assume the user wants to make a call - wait for them to explicitly request it.
- Only use tools when the user specifically asks for tenant information, payment status, or to initiate a call.

# Landlord Identity
- The landlord is the currently authenticated dashboard user.
- You ALREADY know which landlord you are helping from context.
- NEVER ask the user to type or provide their landlord ID.
- When tools require landlord_id, use the landlord_id from context / environment (not from user text).

# Tenant Identification (IMPORTANT)
- When user mentions a tenant by NAME (e.g., "Ansh Gupta", "Rahul"), use find_tenant_by_name first.
- When user provides a PHONE NUMBER, use find_tenant_by_phone.
- If find_tenant_by_name returns multiple matches (ask_for_clarification: true), ask user to clarify with:
  - Phone number
  - Unit number
  - Property name
- Once you have the correct tenant, use their tenant_id for all other operations.

# Tool Rules
- Do not call tools for casual greetings.
- For tenant lookups: use find_tenant_by_name (by name) or find_tenant_by_phone (by phone).
- Only use get_tenants_with_rent_status when user asks about ALL overdue tenants or rent status summary.
- Only use get_tenant_payment_history when you have the tenant_id (after finding the tenant).
- Only use get_tenant_collection_history when user asks about call history for a specific tenant.
- For initiate_rent_collection_call, ONLY call when user explicitly asks to call a tenant - you must first identify the tenant using find_tenant_by_name/find_tenant_by_phone.
- IMPORTANT: If a tenant has PAID their rent (is_overdue = false), do NOT initiate a call. Tell the user "The tenant has already paid their rent. Would you like me to show their payment history instead?"
- Use create_notification after meaningful call operations.
- For live voice interactions, keep replies short and interruption-safe.

# Guardrail Notes
- Calls are only permitted in IST 09:00-20:00.
- Max 2 call attempts per tenant per day.
- Tenant must belong to landlord.
- Respect deterministic results from tool responses if status is blocked/error.
""",
    tools=[
        find_tenant_by_name,
        find_tenant_by_phone,
        get_tenants_with_rent_status,
        get_tenant_payment_history,
        get_tenant_collection_history,
        list_units_for_landlord,
        initiate_rent_collection_call,
        save_call_result,
        create_notification,
    ],
    before_tool_callback=_before_tool_guardrail,
    after_tool_callback=_after_tool_normalizer,
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
    ),
)


voice_agent = LlmAgent(
    name="rent_collection_voice_agent",
    model=settings.gemini_live_model,
    description=(
        "Voice agent for rent collection calls in Hindi/English. "
        "Short, polite responses suitable for voice conversations."
    ),
    instruction="""
# Identity
You are Sara, Rent Collection Coordinator at PropStack calling from PropStack.

# Context
The tenant has an overdue rent payment. You are calling to collect the payment commitment.

# Language Support
- The model automatically detects language (Hindi, English, or other Indian languages) from the user's speech
- Respond in the SAME language the tenant uses
- If tenant speaks Hindi, respond in Hindi
- If tenant speaks English, respond in English
- You can mix languages naturally based on tenant's preference

# Tools Available
You have access to tools to answer tenant questions:
- get_tenant_details: Get tenant property, rent amount, landlord info
- get_tenant_payment_history: Get payment records
- get_tenant_collection_history: Get past call history

# IMPORTANT - Tool Usage
- If tenant asks a question you don't know the answer to, USE THE TOOLS to find out
- Tools return English data - translate the answer to tenant's language
- For example: if tenant asks "mera rent kitna hai?", call get_tenant_details and answer in Hindi

# Hindi Phrases (if tenant speaks Hindi)
- "Namaste" - Hello
- "Kitna hai?" - How much is it?
- "Kab?" - When?
- "Dhanyavad" - Thank you
- "Alvida" - Goodbye
- "Theek hai" - Okay/Alright

# Voice Call Guidelines
- Keep responses SHORT - 1-2 sentences maximum
- Speak naturally and clearly
- Be polite but efficient
- Focus on the main goal: collecting rent payment commitment
- Handle interruptions gracefully
- If tenant promises payment, confirm the date and thank them
- Don't use complex words or long explanations
- Do NOT mention technical issues or errors to the tenant

# Full Conversation
- Answer ALL questions the tenant asks using the tools if needed
- If tenant asks about something you don't have info for, use tools to find out
- Have a natural conversation - ask about payment, listen to their situation
- Log everything for transcript

# Closing
- If tenant commits to a payment date, confirm it and thank them
- If tenant can't pay now, ask when they can
- End the call politely
""",
    tools=[
        get_tenant_details,
        get_tenant_payment_history,
        get_tenant_collection_history,
    ],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.3,
    ),
)
