import asyncio
import uuid

from datetime import date, datetime, timedelta, timezone

from app.config import settings
from app.dependencies import get_supabase


def _fetch_tenancies(landlord_id: str) -> dict:
    sb = get_supabase()
    result = (
        sb.table("tenancies")
        .select(
            "*, "
            "users!tenancies_tenant_id_fkey("
            "  id, name, phone, email, preferred_language"
            "), "
            "units("
            "  id, unit_number, rent_amount, "
            "  properties(id, name, address, landlord_id)"
            ")"
        )
        .eq("status", "active")
        .execute()
    )

    tenants = []
    today = date.today()
    current_month = today.strftime("%Y-%m")

    tenancy_ids = []
    for tenancy in result.data or []:
        unit = tenancy.get("units") or {}
        prop = unit.get("properties") or {}
        if prop.get("landlord_id") != landlord_id:
            continue
        tenancy_ids.append(tenancy.get("id"))

    cycles_by_tenancy = {}
    if tenancy_ids:
        try:
            cycles_res = (
                sb.table("rent_cycles")
                .select("tenancy_id, status, grace_date, amount_due, amount_paid")
                .in_("tenancy_id", tenancy_ids)
                .eq("month", current_month)
                .execute()
            )
            for c in cycles_res.data or []:
                cycles_by_tenancy[c["tenancy_id"]] = c
        except Exception:
            pass

    for tenancy in result.data or []:
        unit = tenancy.get("units") or {}
        prop = unit.get("properties") or {}
        if prop.get("landlord_id") != landlord_id:
            continue

        tenant_user = tenancy.get("users") or {}
        tenancy_id = tenancy.get("id")
        cycle = cycles_by_tenancy.get(tenancy_id)
        
        tenancy_due_day = tenancy.get("rent_due_day") or settings.rent_due_day
        due_day = min(tenancy_due_day, 28)
        due_date = today.replace(day=due_day)
        overdue_threshold = due_date + timedelta(days=settings.grace_period_days)
        date_based_days_overdue = max((today - overdue_threshold).days, 0)
        
        promised_payment_date = cycle.get("promised_payment_date") if cycle else None

        if cycle and cycle.get("status") == "paid":
            is_overdue = False
            days_overdue = 0
        elif cycle and cycle.get("status") in ("overdue", "unpaid", "partially_paid"):
            is_overdue = cycle.get("status") in ("overdue", "unpaid")
            if cycle.get("grace_date"):
                try:
                    gd = date.fromisoformat(str(cycle["grace_date"])[:10])
                    days_overdue = max((today - gd).days, 0)
                except (ValueError, TypeError):
                    days_overdue = date_based_days_overdue
            else:
                days_overdue = date_based_days_overdue
        else:
            days_overdue = date_based_days_overdue
            is_overdue = days_overdue > 0

        tenants.append(
            {
                "tenant_id": tenant_user.get("id"),
                "tenant_name": tenant_user.get("name"),
                "tenant_phone": tenant_user.get("phone"),
                "tenant_email": tenant_user.get("email"),
                "preferred_language": tenant_user.get("preferred_language", "english"),
                "unit_id": unit.get("id"),
                "unit_number": unit.get("unit_number"),
                "property_name": prop.get("name"),
                "property_address": prop.get("address"),
                "rent_amount": unit.get("rent_amount"),
                "days_overdue": days_overdue,
                "is_overdue": is_overdue,
                "payment_status": cycle.get("status") if cycle else "unpaid",
                "rent_due_day": tenancy_due_day,
                "promised_payment_date": promised_payment_date,
            }
        )

    return {
        "status": "success",
        "tenants": tenants,
        "total_count": len(tenants),
        "overdue_count": sum(1 for t in tenants if t["is_overdue"]),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_tenants_with_rent_status(landlord_id: str) -> dict:
    """Retrieves all active tenants for a landlord with their rent payment status.

    Uses rent_cycles (payment source of truth) when available; falls back to
    date-based logic. Paid tenants are never marked overdue.

    Args:
        landlord_id (str): The UUID of the landlord whose tenants to check.

    Returns:
        dict: status and tenants list with name, phone, unit, rent amount,
              days overdue, payment_status, and is_overdue for each tenant.
    """
    try:
        return await asyncio.to_thread(_fetch_tenancies, landlord_id)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def _fetch_payment_history(tenant_id: str) -> dict:
    sb = get_supabase()
    try:
        result = (
            sb.table("payments")
            .select("id, amount, currency, paid_at, provider, status, period_month")
            .eq("tenant_id", tenant_id)
            .eq("status", "succeeded")
            .order("paid_at", desc=True)
            .limit(10)
            .execute()
        )
        return {
            "status": "success",
            "payments": result.data or [],
            "total_payments": len(result.data or []),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return {"status": "success", "payments": [], "total_payments": 0}


async def get_tenant_payment_history(tenant_id: str) -> dict:
    """Retrieves actual payment records for a tenant (from gateway webhooks).

    Shows real rent payments, not call logs. Use get_tenant_collection_history
    for past collection calls and transcripts.

    Args:
        tenant_id (str): The UUID of the tenant.

    Returns:
        dict: status and payments list with amount, paid_at, provider, period.
    """
    try:
        return await asyncio.to_thread(_fetch_payment_history, tenant_id)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def _fetch_collection_history(tenant_id: str) -> dict:
    sb = get_supabase()
    result = (
        sb.table("call_logs")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    return {
        "status": "success",
        "call_history": result.data or [],
        "total_past_calls": len(result.data or []),
    }


async def get_tenant_collection_history(tenant_id: str) -> dict:
    """Retrieves past rent collection calls for a tenant (Sara's interactions).

    Useful before making a new call: see transcripts, outcomes, commitments.
    For actual payment records, use get_tenant_payment_history.

    Args:
        tenant_id (str): The UUID of the tenant.

    Returns:
        dict: status and call_history with transcript, outcome, dates.
    """
    try:
        return await asyncio.to_thread(_fetch_collection_history, tenant_id)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def _log_promised_payment_date(tenant_id: str, promised_date: str) -> dict:
    sb = get_supabase()
    today = date.today()
    current_month = today.strftime("%Y-%m")
    
    # Get active tenancy and required details to ensure cycle exists
    tenancy_res = (
        sb.table("tenancies")
        .select("id, rent_due_day, units(rent_amount)")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not tenancy_res.data:
        return {"status": "error", "message": "Could not find active tenancy for this tenant."}
        
    tenancy = tenancy_res.data[0]
    tenancy_id = tenancy["id"]
    unit = tenancy.get("units") or {}
    amount_due = float(unit.get("rent_amount") or 0)
    rent_due_day = tenancy.get("rent_due_day")
    
    # Verify promised_date is valid YYYY-MM-DD
    try:
        date.fromisoformat(promised_date)
    except ValueError:
        return {"status": "error", "message": "promised_date must be in YYYY-MM-DD format."}

    # Ensure the rent cycle exists before updating
    from app.services.rent_cycle_service import ensure_rent_cycle
    cycle = ensure_rent_cycle(sb, tenancy_id, current_month, amount_due, rent_due_day)

    # Update rent cycle
    (
        sb.table("rent_cycles")
        .update({"promised_payment_date": promised_date})
        .eq("id", cycle["id"])
        .execute()
    )
    
    return {"status": "success", "message": f"Successfully logged promised payment date: {promised_date}"}


async def log_promised_payment_date(tenant_id: str, promised_date: str) -> dict:
    """Logs the date a tenant promised to pay their rent into their rent cycle.
    
    Args:
        tenant_id (str): The UUID of the tenant.
        promised_date (str): The date they promised to pay, in YYYY-MM-DD format.
        
    Returns:
        dict: A status map confirming if the promised date was successfully recorded.
    """
    try:
        return await asyncio.to_thread(_log_promised_payment_date, tenant_id, promised_date)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def _log_manual_payment(tenant_id: str, amount: float) -> dict:
    sb = get_supabase()
    today = date.today()
    current_month = today.strftime("%Y-%m")
    
    # Get active tenancy for unit_id, landlord_id, and tenancy_id
    tenancy_res = (
        sb.table("tenancies")
        .select("id, unit_id, units(properties(landlord_id))")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not tenancy_res.data:
        return {"status": "error", "message": "Could not find active tenancy for this tenant."}
        
    tenancy = tenancy_res.data[0]
    tenancy_id = tenancy["id"]
    unit_id = tenancy["unit_id"]
    unit = tenancy.get("units") or {}
    prop = unit.get("properties") or {}
    landlord_id = prop.get("landlord_id")
    
    # Insert record into payments table so it appears in payment history
    try:
        sb.table("payments").insert({
            "tenant_id": tenant_id,
            "unit_id": unit_id,
            "landlord_id": landlord_id,
            "tenancy_id": tenancy_id,
            "amount": amount,
            "currency": "INR",
            "provider": "Cash (Manual)",
            "provider_payment_id": f"manual_{uuid.uuid4().hex[:16]}",
            "status": "succeeded",
            "period_month": current_month,
            "paid_at": datetime.now(timezone.utc).isoformat()
        }).execute()
    except Exception as e:
        print(f"Failed to insert manual payment record: {e}")
    
    from app.services.rent_cycle_service import update_cycle_on_payment
    return update_cycle_on_payment(sb, tenant_id, unit_id, amount, current_month)


async def log_manual_payment(tenant_id: str, amount: float) -> dict:
    """Manually logs a rent payment for a tenant when the landlord informs you they paid.
    
    Args:
        tenant_id (str): The UUID of the tenant who paid.
        amount (float): The amount paid.
        
    Returns:
        dict: Process result indicating successful payment logging.
    """
    try:
        return await asyncio.to_thread(_log_manual_payment, tenant_id, amount)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def _fetch_units_for_landlord(landlord_id: str) -> dict:
    """Fetch all units for a landlord with simple occupancy info.

    A unit is considered:
    - 'occupied' if there is an active tenancy for that unit
    - 'vacant' if there is no active tenancy
    """
    sb = get_supabase()

    units_res = (
        sb.table("units")
        .select(
            "id, unit_number, rent_amount, is_occupied, "
            "properties(id, name, address, landlord_id)"
        )
        .execute()
    )

    units_raw = []
    unit_ids: list[str] = []
    for unit in units_res.data or []:
        prop = unit.get("properties") or {}
        if prop.get("landlord_id") != landlord_id:
            continue
        units_raw.append(unit)
        if unit.get("id"):
            unit_ids.append(unit["id"])

    tenancies_by_unit: dict[str, list[dict]] = {}
    if unit_ids:
        tenancies_res = (
            sb.table("tenancies")
            .select(
                "id, status, unit_id, tenant_id, "
                "users!tenancies_tenant_id_fkey(id, name)"
            )
            .in_("unit_id", unit_ids)
            .execute()
        )
        for t in tenancies_res.data or []:
            key = t.get("unit_id")
            if not key:
                continue
            tenancies_by_unit.setdefault(key, []).append(t)

    units: list[dict] = []
    for unit in units_raw:
        unit_id = unit.get("id")
        prop = unit.get("properties") or {}
        related = tenancies_by_unit.get(unit_id, [])
        active_tenancy = next((t for t in related if t.get("status") == "active"), None)

        # Derive occupancy primarily from units.is_occupied, with a fallback to tenancies.
        is_occupied_flag = unit.get("is_occupied")
        if is_occupied_flag is True:
            occupancy_status = "occupied"
        elif is_occupied_flag is False:
            occupancy_status = "vacant"
        elif active_tenancy:
            occupancy_status = "occupied"
        else:
            occupancy_status = "vacant"

        if active_tenancy:
            tenant_user = active_tenancy.get("users") or {}
            tenant_name = tenant_user.get("name")
            tenancy_status = active_tenancy.get("status")
        else:
            tenant_name = None
            tenancy_status = None

        units.append(
            {
                "unit_id": unit_id,
                "unit_number": unit.get("unit_number"),
                "rent_amount": unit.get("rent_amount"),
                "property_id": prop.get("id"),
                "property_name": prop.get("name"),
                "property_address": prop.get("address"),
                "occupancy_status": occupancy_status,
                "tenant_name": tenant_name,
                "tenancy_status": tenancy_status,
            }
        )

    return {
        "status": "success",
        "units": units,
        "total_units": len(units),
        "occupied_units": sum(1 for u in units if u["occupancy_status"] == "occupied"),
        "vacant_units": sum(1 for u in units if u["occupancy_status"] == "vacant"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def list_units_for_landlord(landlord_id: str) -> dict:
    """Lists all units for a landlord with basic occupancy information.

    Use this for portfolio / overview questions such as:
    - \"Show me all my units\"
    - \"Which units are vacant?\"
    - \"What is the rent and occupancy for each flat in MG Heights?\"

    Args:
        landlord_id (str): UUID of the landlord whose units to list.

    Returns:
        dict: status plus an array of units with property, rent, and occupancy:
            {
              unit_id, unit_number, rent_amount, unit_status,
              property_id, property_name, property_address,
              occupancy_status: 'occupied' | 'vacant',
              tenant_name, tenancy_status
            }
    """
    try:
        return await asyncio.to_thread(_fetch_units_for_landlord, landlord_id)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}
