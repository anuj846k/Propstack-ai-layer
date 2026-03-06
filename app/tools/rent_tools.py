import asyncio
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
    due_day = min(settings.rent_due_day, 28)
    due_date = today.replace(day=due_day)
    overdue_threshold = due_date + timedelta(days=settings.grace_period_days)
    date_based_days_overdue = max((today - overdue_threshold).days, 0)

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
