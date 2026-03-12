import logging

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel
from twilio.twiml.messaging_response import MessagingResponse

from app.agents.maintenance.triage_agent import triage_agent
from app.config import settings
from app.dependencies import get_supabase
from app.integrations import twilio_voice
from app.services.session_service import get_session_service
from app.tools.maintenance_tools import _dispatch_vendor_for_ticket

logger = logging.getLogger(__name__)
router = APIRouter()
session_service = get_session_service()


def _form_to_string_dict(form_data) -> dict[str, str]:
    return {str(k): str(v) for k, v in form_data.items()}


def _extract_user_facing_tool_message(tool_response: object) -> str:
    """
    Extract the best user-facing message from an ADK tool response.

    We prefer nested `data.message` (tool-specific user response), then `message`.
    """
    def _sanitize(msg: str) -> str:
        cleaned = msg.strip()
        cleaned = cleaned.replace("Tell the user this explicitly.", "").strip()
        # Normalize any leftover double spaces after removal.
        while "  " in cleaned:
            cleaned = cleaned.replace("  ", " ")
        return cleaned

    if not isinstance(tool_response, dict):
        return ""
    data = tool_response.get("data")
    if isinstance(data, dict):
        msg = data.get("message")
        if isinstance(msg, str) and msg.strip():
            return _sanitize(msg)
    msg = tool_response.get("message")
    if isinstance(msg, str) and msg.strip():
        return _sanitize(msg)
    return ""


@router.post("/twilio-whatsapp-incoming")
async def twilio_whatsapp_incoming(
    request: Request,
    x_twilio_signature: str = Header("", alias="X-Twilio-Signature"),
):
    """
    Twilio-compatible WhatsApp inbound webhook.
    Receives form-encoded POST from Twilio, runs the triage agent, returns TwiML.
    """
    form = await request.form()
    form_payload = _form_to_string_dict(form)

    is_valid = twilio_voice.validate_signature(
        url=str(request.url),
        params=form_payload,
        signature=x_twilio_signature,
    )
    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid Twilio signature")

    body = form_payload.get("Body", "").strip()
    from_number = form_payload.get("From", "")
    num_media = int(form_payload.get("NumMedia", "0"))
    media_url = form_payload.get("MediaUrl0") if num_media > 0 else None

    phone = from_number.replace("whatsapp:", "").strip()

    sb = get_supabase()
    user_res = (
        sb.table("users")
        .select("id")
        .eq("phone", phone)
        .eq("role", "tenant")
        .limit(1)
        .execute()
    )

    if not user_res.data:
        twiml = MessagingResponse()
        twiml.message(
            "Sorry, we couldn't find a tenant account linked to this phone number. "
            "Please contact your property manager to register."
        )
        return Response(content=str(twiml), media_type="application/xml")

    tenant_id = user_res.data[0]["id"]

    system_ctx = f"[System: tenant_id={tenant_id}]"
    if media_url:
        system_ctx += f"\n[System: image_url={media_url}]"

    parts = [
        types.Part.from_text(text=system_ctx),
        types.Part.from_text(text=body or "[No message text]"),
    ]

    if media_url:
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                img_resp = await client.get(
                    media_url,
                    auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                )
                img_resp.raise_for_status()
                mime_type = img_resp.headers.get("content-type", "image/jpeg")
                parts.append(
                    types.Part.from_bytes(data=img_resp.content, mime_type=mime_type)
                )
                parts.append(
                    types.Part.from_text(
                        text="[System: The tenant attached an image. Analyze it visually.]"
                    )
                )
        except Exception as e:
            logger.warning("Failed to download WhatsApp media: %s", e)
            parts.append(
                types.Part.from_text(
                    text=f"[System: Tenant attached media but download failed: {e}]"
                )
            )

    session_id = f"wa_triage_{phone}"

    runner = Runner(
        agent=triage_agent,
        app_name="propstack_maintenance",
        session_service=session_service,
        auto_create_session=True,
    )

    final_text = ""
    tool_msg = ""  # Message from tool
    content = types.Content(role="user", parts=parts)

    try:
        async for event in runner.run_async(
            user_id=tenant_id,
            session_id=session_id,
            new_message=content,
        ):
            # Log all events for debugging
            logger.info(f"Event author: {event.author}")
            
            # Extract message from tool response
            func_responses = event.get_function_responses()
            if func_responses:
                logger.info(f"Found {len(func_responses)} function responses")
                for resp in func_responses:
                    logger.info(f"  resp: {resp}")
                    logger.info(f"  resp.response type: {type(resp.response)}")
                    logger.info(f"  resp.response: {resp.response}")
                    
                    if hasattr(resp, 'response'):
                        extracted = _extract_user_facing_tool_message(resp.response)
                        # Only accept meaningful messages (avoid generic "tool completed").
                        if extracted and "completed" not in extracted.lower():
                            tool_msg = extracted
                            logger.info(f"Tool message found: {extracted}")
                        else:
                            # Try to get message from nested structure
                            logger.info(f"Response is not dict, checking other formats")
            
            # Get agent text
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text and part.text.strip():
                        final_text = part.text.strip()
                        logger.info(f"Agent text: {final_text[:100]}")
                        
    except Exception as e:
        logger.error(f"Error: {e}")

    # Use tool message if available, else use agent text
    if tool_msg:
        final_text = tool_msg
        logger.info(f"Using tool message: {final_text}")
    elif not final_text:
        final_text = "I've received your request. A vendor will contact you soon."
        logger.info("Using fallback message")

    logger.info(f"Sending WhatsApp: {final_text}")

    twiml = MessagingResponse()
    twiml.message(final_text)
    return Response(content=str(twiml), media_type="application/xml")


class TriggerDispatchRequest(BaseModel):
    ticket_id: str
    specialty: str


@router.post("/trigger-vendor-call")
async def trigger_vendor_call(request: TriggerDispatchRequest):
    """
    Kicks off the autonomous voice dispatch loop.
    Finds the next available vendor and initiates a Twilio outbound call.
    """
    result = await _dispatch_vendor_for_ticket(request.ticket_id, request.specialty)

    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result.get("message"))

    return result
