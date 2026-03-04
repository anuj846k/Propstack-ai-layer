"""Voice call tools for tenant information."""

import asyncio
from app.dependencies import get_supabase


def _fetch_tenant_details(tenant_id: str) -> dict:
    """Fetch tenant details including property and landlord info."""
    sb = get_supabase()
    
    # Get active tenancy for this tenant
    tenancy = (
        sb.table("tenancies")
        .select("""
            id,
            start_date,
            end_date,
            deposit_amount,
            users!tenancies_tenant_id_fkey(name, phone, email),
            units!tenancies_unit_id_fkey(
                unit_number,
                rent_amount,
                properties!units_property_id_fkey(
                    name,
                    address,
                    city,
                    state,
                    landlord_id
                )
            )
        """)
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    
    if not tenancy.data:
        return {
            "status": "error",
            "error_message": "No active tenancy found for this tenant"
        }
    
    data = tenancy.data[0]
    tenant_user = data.get("users") or {}
    unit = data.get("units") or {}
    prop = unit.get("properties") or {}
    landlord_id = prop.get("landlord_id")
    
    # Get landlord info separately
    landlord = {}
    if landlord_id:
        landlord_res = (
            sb.table("users")
            .select("name, phone, email")
            .eq("id", landlord_id)
            .limit(1)
            .execute()
        )
        if landlord_res.data:
            landlord = landlord_res.data[0]
    
    return {
        "status": "success",
        "tenant": {
            "name": tenant_user.get("name"),
            "phone": tenant_user.get("phone"),
            "email": tenant_user.get("email"),
        },
        "property": {
            "name": prop.get("name"),
            "address": prop.get("address"),
            "city": prop.get("city"),
            "state": prop.get("state"),
        },
        "unit": {
            "unit_number": unit.get("unit_number"),
            "rent_amount": float(unit.get("rent_amount") or 0),
        },
        "tenancy": {
            "start_date": str(data.get("start_date")) if data.get("start_date") else None,
            "end_date": str(data.get("end_date")) if data.get("end_date") else None,
            "deposit_amount": float(data.get("deposit_amount") or 0),
        },
        "landlord": {
            "name": landlord.get("name"),
            "phone": landlord.get("phone"),
            "email": landlord.get("email"),
        }
    }


async def get_tenant_details(tenant_id: str) -> dict:
    """Get tenant details including property and landlord info.
    
    Use this tool to answer tenant questions about their property, rent amount,
    or landlord information. The tool returns English data - translate to tenant's
    language when responding.
    
    Args:
        tenant_id: The tenant's user ID
        
    Returns:
        Tenant details including property name, rent amount, landlord info
    """
    try:
        return await asyncio.to_thread(_fetch_tenant_details, tenant_id)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}
