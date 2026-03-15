import asyncio
import logging
from datetime import datetime, timezone
from app.dependencies import get_supabase
from app.integrations import twilio_voice
from app.config import settings
from app.services.live_session_service import live_session_service

logger = logging.getLogger(__name__)

def _create_maintenance_ticket(
    tenant_id: str,
    issue_category: str,
    issue_description: str,
    ai_severity_score: int,
    ai_summary: str,
    image_url: str = "",
) -> dict:
    sb = get_supabase()

    tenancy_res = (
        sb.table("tenancies")
        .select("unit_id")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not tenancy_res.data:
        return {"status": "error", "message": "Could not find active tenancy for this tenant."}

    unit_id = tenancy_res.data[0]["unit_id"]

    row: dict = {
        "tenant_id": tenant_id,
        "unit_id": unit_id,
        "title": f"{issue_category.capitalize()} Issue",
        "issue_category": issue_category,
        "issue_description": issue_description,
        "priority": "high" if ai_severity_score > 70 else "medium" if ai_severity_score > 40 else "low",
        "status": "open",
        "ai_severity_score": ai_severity_score,
        "ai_summary": ai_summary,
    }
    if image_url:
        row["image_url"] = image_url

    try:
        res = sb.table("maintenance_tickets").insert(row).execute()
        return {"status": "success", "message": "Created maintenance ticket successfully.", "ticket": res.data[0]}
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


async def create_maintenance_ticket(
    tenant_id: str,
    issue_category: str,
    issue_description: str,
    ai_severity_score: int,
    ai_summary: str,
    image_url: str = "",
) -> dict:
    """Creates a new maintenance ticket in the database.

    Args:
        tenant_id (str): UUID of the tenant reporting the issue.
        issue_category (str): The category of the issue (e.g., 'plumbing', 'electrical', 'carpentry').
        issue_description (str): Full text description of the problem.
        ai_severity_score (int): 1-100 severity score.
        ai_summary (str): Short summary of the issue.
        image_url (str): Optional URL of an image the tenant attached.
    Returns:
        dict: Result containing the successfully created ticket.
    """
    result = await asyncio.to_thread(
        _create_maintenance_ticket, tenant_id, issue_category, issue_description, ai_severity_score, ai_summary, image_url
    )
    if result.get("status") == "success":
        ticket_id = result["ticket"]["id"]
        logger.info(f"Attempting vendor dispatch for ticket {ticket_id}, specialty: {issue_category}")
        dispatch_result = await _dispatch_vendor_for_ticket(ticket_id, issue_category)
        logger.info(f"Vendor dispatch result: {dispatch_result}")
        result["ticket_id"] = ticket_id
        result["category"] = issue_category
        result["created_at"] = result["ticket"].get("created_at")
    return result


def _find_next_available_vendor(ticket_id: str, specialty: str) -> dict:
    sb = get_supabase()
    
    vendors_res = (
        sb.table("vendors")
        .select("id, name, phone, specialty")
        .eq("specialty", specialty)
        .eq("is_active", True)
        .execute()
    )
    
    if not vendors_res.data:
        return {"status": "error", "message": f"No active vendors found for specialty: {specialty}"}
        
    all_vendors = vendors_res.data
    
    # Get all vendors already called/rejected for this ticket
    logs_res = (
        sb.table("vendor_dispatch_logs")
        .select("vendor_id, status")
        .eq("ticket_id", ticket_id)
        .execute()
    )
    
    contacted_vendor_ids = [log["vendor_id"] for log in logs_res.data]
    
    # Find the first vendor not in the contacted list
    available_vendors = [v for v in all_vendors if v["id"] not in contacted_vendor_ids]
    
    if not available_vendors:
        return {"status": "error", "message": "All available vendors have already been contacted or rejected the job."}
        
    next_vendor = available_vendors[0]
    
    # Log that we are calling them
    try:
        res = sb.table("vendor_dispatch_logs").insert({
            "ticket_id": ticket_id,
            "vendor_id": next_vendor["id"],
            "status": "called"
        }).execute()
        if res.data:
            next_vendor["dispatch_log_id"] = res.data[0]["id"]
    except Exception as e:
        print(f"Failed to log vendor call: {e}")
        
    return {"status": "success", "message": "Found next available vendor.", "vendor": next_vendor}

async def _dispatch_vendor_for_ticket(ticket_id: str, specialty: str) -> dict:
    """Find the next available vendor and initiate a Twilio voice call."""
    logger.info(f"_dispatch_vendor_for_ticket called: ticket_id={ticket_id}, specialty={specialty}")
    
    next_vendor_res = await find_next_available_vendor(ticket_id, specialty)
    logger.info(f"find_next_available_vendor result: {next_vendor_res}")

    if next_vendor_res["status"] == "error":
        logger.error(f"Failed to find vendor: {next_vendor_res}")
        return next_vendor_res

    vendor = next_vendor_res["vendor"]
    vendor_phone = vendor["phone"]
    dispatch_log_id = vendor.get("dispatch_log_id")

    if not dispatch_log_id:
        logger.error(f"Missing dispatch_log_id for vendor {vendor}")
        return {"status": "error", "message": "Missing dispatch log id"}

    try:
        from app.routers import maintenance_twilio
        
        twiml_url = maintenance_twilio.twiml_url(dispatch_log_id)
        status_url = maintenance_twilio.status_callback_url(dispatch_log_id)
        
        logger.info(f"Initiating Twilio call to {vendor_phone}, twiml_url={twiml_url}")

        provider = twilio_voice.create_outbound_call(
            to_number=vendor_phone,
            call_id=dispatch_log_id,
            twiml_url_override=twiml_url,
            status_callback_override=status_url,
        )
        
        logger.info(f"Twilio call initiated successfully: {provider}")
        
        provider_call_sid = provider.get("provider_call_sid")
        live_session_id = None
        
        if settings.enable_partner_twilio_live:
            logger.info(f"Starting live session for maintenance dispatch, dispatch_log_id={dispatch_log_id}")
            live_record = live_session_service.start_session(
                call_id=dispatch_log_id,
                source="maintenance_dispatch",
                provider_call_sid=provider_call_sid,
                metadata={
                    "ticket_id": ticket_id,
                    "vendor_id": vendor["id"],
                    "vendor_name": vendor["name"],
                    "vendor_phone": vendor_phone,
                    "specialty": specialty,
                },
            )
            live_session_id = live_record.get("session_id")
            logger.info(f"Live session started: {live_session_id}")

        return {
            "status": "success",
            "message": f"Initiated call to vendor {vendor['name']} at {vendor_phone}",
            "vendor": vendor,
            "provider_status": provider["provider_status"],
            "live_session_id": live_session_id,
        }
    except Exception as e:
        logger.exception(f"Exception during vendor dispatch: {e}")
        return {"status": "error", "message": f"Failed to trigger Twilio call: {e}"}


async def find_next_available_vendor(ticket_id: str, specialty: str) -> dict:
    """Finds the next uncontacted vendor on the platform for a specific ticket issue.
    
    Args:
        ticket_id (str): UUID of the maintenance ticket.
        specialty (str): The required specialty (e.g., 'plumbing', 'electrical').
    Returns:
        dict: The next available vendor details (id, name, phone).
    """
    return await asyncio.to_thread(_find_next_available_vendor, ticket_id, specialty)


def _vendor_accepts_ticket(vendor_id: str, ticket_id: str) -> dict:
    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    
    try:
        # Insert activity_log first while ticket is still visible (avoids FK failure if RLS
        # or replication hides the row after we update)
        try:
            sb.table("activity_log").insert({
                "ticket_id": ticket_id,
                "action": "vendor_assigned",
                "notes": f"Vendor {vendor_id} accepted and was assigned to the ticket.",
            }).execute()
        except Exception as log_err:
            logger.warning(
                "activity_log insert failed (will still update ticket): %s",
                log_err,
                exc_info=True,
            )

        # Update ticket status to assigned
        sb.table("maintenance_tickets").update({
            "status": "assigned",
            "assigned_vendor_id": vendor_id,
            "updated_at": now
        }).eq("id", ticket_id).execute()
        
        # Update dispatch log
        sb.table("vendor_dispatch_logs").update({
            "status": "accepted"
        }).eq("ticket_id", ticket_id).eq("vendor_id", vendor_id).execute()
        
        return {"status": "success", "message": "Successfully assigned the ticket to the vendor."}
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

async def vendor_accepts_ticket(vendor_id: str, ticket_id: str) -> dict:
    """Records that a vendor has accepted a maintenance job over the phone and assigns them.
    
    Args:
        vendor_id (str): UUID of the vendor who accepted.
        ticket_id (str): UUID of the ticket.
    Returns:
        dict: Success status.
    """
    return await asyncio.to_thread(_vendor_accepts_ticket, vendor_id, ticket_id)


def _vendor_rejects_ticket(vendor_id: str, ticket_id: str) -> dict:
    sb = get_supabase()
    
    try:
        # Update dispatch log
        sb.table("vendor_dispatch_logs").update({
            "status": "rejected"
        }).eq("ticket_id", ticket_id).eq("vendor_id", vendor_id).execute()
        
        # Log to activity_log (non-fatal: rejection already recorded above)
        try:
            sb.table("activity_log").insert({
                "ticket_id": ticket_id,
                "action": "vendor_rejected",
                "notes": f"Vendor {vendor_id} rejected the job.",
            }).execute()
        except Exception as log_err:
            logger.warning(
                "activity_log insert failed (rejection already recorded): %s",
                log_err,
                exc_info=True,
            )
        
        return {"status": "success", "message": "Logged vendor rejection successfully. Please find_next_available_vendor."}
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

async def vendor_rejects_ticket(vendor_id: str, ticket_id: str) -> dict:
    """Records that a vendor has declined or is too busy to take a maintenance job.
    
    Args:
        vendor_id (str): UUID of the vendor who declined.
        ticket_id (str): UUID of the ticket.
    Returns:
        dict: Success status.
    """
    return await asyncio.to_thread(_vendor_rejects_ticket, vendor_id, ticket_id)
