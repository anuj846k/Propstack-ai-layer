from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TenantRentStatus(BaseModel):
    tenant_id: str = Field(description="UUID of the tenant")
    tenant_name: str = Field(description="Full name of the tenant")
    unit_number: str = Field(description="Flat or unit number")
    property_name: str = Field(description="Name of the property")
    rent_amount: float = Field(description="Monthly rent in INR")
    days_overdue: int = Field(description="Number of days rent is overdue")
    is_overdue: bool = Field(description="Whether rent is currently overdue")


class RentCheckResponse(BaseModel):
    total_tenants: int = Field(description="Total active tenants for this landlord")
    overdue_count: int = Field(description="Number of tenants with overdue rent")
    tenants: list[TenantRentStatus] = Field(
        default_factory=list,
        description="List of tenants with their rent status",
    )


class ChatRequest(BaseModel):
    user_id: str = Field(
        default="propstack-owner",
        description="User/landlord identifier for the chat session",
    )
    session_id: str | None = Field(
        default=None,
        description="Session ID for multi-turn; generated if omitted",
    )
    message: str = Field(description="User message")
    landlord_id: str | None = Field(
        default=None,
        description=(
            "Landlord ID for context. Normally set from authentication "
            "and not asked from the user directly."
        ),
    )


class CallInitiationRequest(BaseModel):
    landlord_id: str = Field(description="UUID of the landlord requesting the call")
    tenant_id: str = Field(description="UUID of the tenant to call")
    tenant_name: str = Field(description="Name of the tenant to call")


class CallInitiationResponse(BaseModel):
    call_id: str | None = Field(
        default=None, description="UUID of the call log record for tracking"
    )
    status: str = Field(description="One of: initiated, failed, queued")
    message: str = Field(description="Human-readable status message")
    provider_status: str | None = Field(
        default=None, description="Provider dispatch status from Twilio"
    )
    provider_call_sid: str | None = Field(
        default=None, description="Twilio Call SID for provider-side tracking"
    )
    live_session_enabled: bool = Field(
        default=False, description="Whether partner live session is enabled"
    )
    live_session_id: str | None = Field(
        default=None, description="Internal live session tracking identifier"
    )


class CallOutcome(BaseModel):
    call_id: str = Field(description="UUID of the call log record")
    outcome: str = Field(
        description=(
            "One of: promised_payment, no_answer, refused, "
            "negotiated_extension, wrong_number"
        )
    )
    transcript_summary: str = Field(
        description="One-paragraph summary of the call"
    )
    next_action: str = Field(description="Recommended follow-up action")
    payment_date_promised: str | None = Field(
        default=None,
        description="Date tenant promised to pay if applicable (YYYY-MM-DD)",
    )


class SweepRequest(BaseModel):
    mode: Literal["kickoff", "daily"] = Field(
        description="Sweep mode. Kickoff is first overdue-day run, daily is follow-up."
    )
    month: str | None = Field(
        default=None,
        description="Rent period month in YYYY-MM format. Defaults to current IST month.",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, returns candidates without initiating calls.",
    )


class SweepAction(BaseModel):
    tenant_id: str = Field(description="Tenant ID evaluated in sweep")
    tenancy_id: str = Field(description="Tenancy ID evaluated in sweep")
    landlord_id: str = Field(description="Landlord ID tied to the candidate")
    action: Literal["called", "skipped", "error"] = Field(
        description="Action taken by sweep for this candidate"
    )
    reason: str = Field(description="Reason for call/skip/error")
    call_id: str | None = Field(
        default=None, description="call_logs ID when call was initiated"
    )


class SweepResponse(BaseModel):
    month: str = Field(description="Swept period month in YYYY-MM")
    mode: Literal["kickoff", "daily"] = Field(description="Sweep mode executed")
    dry_run: bool = Field(description="Whether run was dry-run")
    processed: int = Field(description="Number of candidates processed")
    called: int = Field(description="Number of calls initiated")
    skipped: int = Field(description="Number of candidates skipped")
    errors: int = Field(description="Number of candidate errors")
    actions: list[SweepAction] = Field(
        default_factory=list,
        description="Per-candidate sweep action details",
    )


class ManualCashPaymentRequest(BaseModel):
    landlord_id: str = Field(description="Landlord UUID receiving the rent")
    tenant_id: str = Field(description="Tenant UUID who paid cash")
    unit_id: str = Field(description="Unit UUID for this payment")
    amount: float = Field(gt=0, description="Amount paid in INR")
    paid_at: datetime = Field(description="Payment timestamp in ISO format")
    period_month: str = Field(description="Rent period month in YYYY-MM")
    note: str = Field(description="Manual cash log note from landlord")
    proof_url: str | None = Field(
        default=None, description="Optional receipt/proof URL"
    )


class ManualCashPaymentResponse(BaseModel):
    status: str = Field(description="success or error")
    message: str = Field(description="Human-readable result")
    payment_id: str | None = Field(
        default=None, description="payments table ID for manual cash entry"
    )
    cycle_status: str | None = Field(
        default=None, description="Updated rent cycle status after applying payment"
    )
    period_month: str = Field(description="Rent period month in YYYY-MM")


class CallCallbackRequest(BaseModel):
    call_id: str = Field(description="call_logs ID")
    outcome: str = Field(description="Call outcome from provider callback")
    transcript: str = Field(default="", description="Call transcript text")
    duration_seconds: int = Field(default=0, ge=0, description="Call duration")
    provider_metadata: dict[str, Any] | None = Field(
        default=None, description="Raw callback metadata from provider simulator"
    )


class CallCallbackResponse(BaseModel):
    call_id: str = Field(description="call_logs ID")
    status: str = Field(description="success or error")
    message: str = Field(description="Callback processing result")
    notification_id: str | None = Field(
        default=None, description="Created landlord notification ID"
    )


class TwilioStatusCallbackResponse(BaseModel):
    call_id: str = Field(description="Internal call_logs ID")
    status: str = Field(description="success or error")
    message: str = Field(description="Status callback processing result")
    provider_call_sid: str | None = Field(
        default=None, description="Twilio Call SID"
    )
    provider_status: str | None = Field(
        default=None, description="Raw Twilio CallStatus value"
    )
    outcome: str | None = Field(
        default=None, description="Mapped internal call outcome"
    )
    is_terminal: bool = Field(description="Whether this callback is terminal")
    notification_id: str | None = Field(
        default=None, description="Landlord notification for terminal updates"
    )


class LiveSessionStartRequest(BaseModel):
    call_id: str = Field(description="Internal call_logs ID")
    provider_call_sid: str | None = Field(
        default=None, description="Twilio Call SID if already known"
    )
    source: str = Field(
        default="api",
        description="Session source label (api, twilio_media_ws, browser_demo, etc.)",
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional metadata for live session tracking"
    )


class LiveSessionStartResponse(BaseModel):
    status: str = Field(description="success or error")
    message: str = Field(description="Session start result")
    call_id: str = Field(description="Internal call_logs ID")
    live_session_id: str = Field(description="Internal live session ID")
    live_state: str = Field(description="Current live session state")
    provider_call_sid: str | None = Field(default=None, description="Twilio call SID")


class LiveSessionEndRequest(BaseModel):
    call_id: str | None = Field(
        default=None,
        description="Internal call_logs ID (optional if live_session_id is supplied)",
    )
    live_session_id: str | None = Field(
        default=None,
        description="Live session ID (optional if call_id is supplied)",
    )
    outcome: str = Field(default="completed", description="Final outcome for the call")
    transcript: str = Field(
        default="",
        description="Final transcript or summary text to persist with call log",
    )
    duration_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Optional explicit duration override in seconds",
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Optional live session close metadata"
    )


class LiveSessionEndResponse(BaseModel):
    status: str = Field(description="success or error")
    message: str = Field(description="Session end result")
    call_id: str = Field(description="Internal call_logs ID")
    live_session_id: str = Field(description="Internal live session ID")
    live_state: str = Field(description="Final live session state")
    outcome: str = Field(description="Final persisted call outcome")
    duration_seconds: int = Field(description="Resolved call duration in seconds")


# Chat Message Schemas
class SendMessageRequest(BaseModel):
    conversation_id: str | None = Field(default=None, description="Existing conversation ID")
    landlord_id: str = Field(description="Landlord UUID")
    tenant_id: str | None = Field(default=None, description="Tenant UUID (for tenant-initiated)")
    message: str = Field(description="Message text")
    sender_type: Literal["landlord", "tenant", "ai"] = Field(description="Who sent the message")
    channel: Literal["chat", "whatsapp", "voice"] = Field(default="chat", description="Communication channel")


class SendMessageResponse(BaseModel):
    status: str = Field(description="success or error")
    conversation_id: str = Field(description="Conversation ID")
    message_id: str = Field(description="Saved message ID")


class GetConversationRequest(BaseModel):
    landlord_id: str = Field(description="Landlord UUID")
    tenant_id: str | None = Field(default=None, description="Filter by tenant")
    conversation_id: str | None = Field(default=None, description="Specific conversation ID")
    limit: int = Field(default=50, ge=1, le=100)


class ChatMessage(BaseModel):
    id: str
    conversation_id: str
    sender_id: str | None
    sender_type: str
    message_text: str
    metadata: dict[str, Any]
    created_at: str


class Conversation(BaseModel):
    id: str
    landlord_id: str
    tenant_id: str | None
    channel: str
    status: str
    last_message_at: str
    created_at: str
    messages: list[ChatMessage] = Field(default_factory=list)


class GetConversationResponse(BaseModel):
    status: str = Field(description="success or error")
    conversations: list[Conversation] = Field(default_factory=list)
