import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.dependencies import get_supabase


def _list_properties(landlord_id: str) -> dict:
    sb = get_supabase()
    result = (
        sb.table("properties")
        .select("*")
        .eq("landlord_id", landlord_id)
        .order("name")
        .execute()
    )
    return {
        "status": "success",
        "properties": result.data or [],
        "count": len(result.data or []),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _add_property(
    landlord_id: str, name: str, address: str, city: str, state: str
) -> dict:
    sb = get_supabase()
    res = (
        sb.table("properties")
        .insert(
            {
                "landlord_id": landlord_id,
                "name": name,
                "address": address,
                "city": city,
                "state": state,
            }
        )
        .execute()
    )
    if res.data:
        return {
            "status": "success",
            "message": f"Successfully added property: {name}",
            "property": res.data[0],
        }
    return {"status": "error", "message": "Failed to add property"}


def _list_units(property_id: str) -> dict:
    sb = get_supabase()
    result = (
        sb.table("units")
        .select("*")
        .eq("property_id", property_id)
        .order("unit_number")
        .execute()
    )
    return {
        "status": "success",
        "units": result.data or [],
        "count": len(result.data or []),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _add_unit(
    property_id: str, unit_number: str, rent_amount: float, floor: Optional[int] = None
) -> dict:
    sb = get_supabase()
    res = (
        sb.table("units")
        .insert(
            {
                "property_id": property_id,
                "unit_number": unit_number,
                "rent_amount": rent_amount,
                "floor": floor,
            }
        )
        .execute()
    )
    if res.data:
        return {
            "status": "success",
            "message": f"Successfully added unit {unit_number}",
            "unit": res.data[0],
        }
    return {"status": "error", "message": "Failed to add unit"}


def _add_tenant_and_tenancy(
    landlord_id: str,
    unit_id: str,
    name: str,
    email: str,
    phone: str,
    start_date: str,
    end_date: str,
    deposit_amount: float,
) -> dict:
    sb = get_supabase()

    # 1. Check if user exists
    user_res = (
        sb.table("users")
        .select("id")
        .or_(f"email.eq.{email},phone.eq.{phone}")
        .execute()
    )

    if user_res.data:
        tenant_id = user_res.data[0]["id"]
        # Update role to tenant if it's not already
        sb.table("users").update({"role": "tenant"}).eq("id", tenant_id).execute()
    else:
        # Create user
        new_user = (
            sb.table("users")
            .insert({"name": name, "email": email, "phone": phone, "role": "tenant"})
            .execute()
        )
        if not new_user.data:
            return {
                "status": "error",
                "message": "Failed to create tenant user account",
            }
        tenant_id = new_user.data[0]["id"]

    # 2. Create tenancy
    tenancy_res = (
        sb.table("tenancies")
        .insert(
            {
                "unit_id": unit_id,
                "tenant_id": tenant_id,
                "start_date": start_date,
                "end_date": end_date,
                "deposit_amount": deposit_amount,
                "status": "active",
            }
        )
        .execute()
    )

    if tenancy_res.data:
        # 3. Mark unit as occupied
        sb.table("units").update({"is_occupied": True}).eq("id", unit_id).execute()

        return {
            "status": "success",
            "message": f"Successfully added {name} as a tenant and created an active tenancy.",
            "tenancy_id": tenancy_res.data[0]["id"],
        }

    return {"status": "error", "message": "Failed to create tenancy record"}


# Public Tool Wrappers (Async)


async def list_properties(landlord_id: str) -> dict:
    """Lists all properties owned by a landlord.

    Args:
        landlord_id: UUID of the landlord.
    """
    return await asyncio.to_thread(_list_properties, landlord_id)


async def add_property(
    landlord_id: str, name: str, address: str, city: str, state: str
) -> dict:
    """Adds a new property to the portfolio.

    Args:
        landlord_id: UUID of the landlord.
        name: Name of the property (e.g., 'Green Valley Apartments').
        address: Full street address.
        city: City name.
        state: State name.
    """
    return await asyncio.to_thread(
        _add_property, landlord_id, name, address, city, state
    )


async def list_units(property_id: str) -> dict:
    """Lists all units within a specific property.

    Args:
        property_id: UUID of the property.
    """
    return await asyncio.to_thread(_list_units, property_id)


async def add_unit(
    property_id: str, unit_number: str, rent_amount: float, floor: Optional[int] = None
) -> dict:
    """Adds a new unit to a property.

    Args:
        property_id: UUID of the property.
        unit_number: Flat/Unit number (e.g., '301').
        rent_amount: Monthly rent amount in INR.
        floor: Optional floor number.
    """
    return await asyncio.to_thread(
        _add_unit, property_id, unit_number, rent_amount, floor
    )


async def add_tenant_and_tenancy(
    landlord_id: str,
    unit_id: str,
    name: str,
    email: str,
    phone: str,
    start_date: str,
    end_date: str,
    deposit_amount: float,
) -> dict:
    """Creates a tenant user and assigns them to an active tenancy for a unit.

    Args:
        landlord_id: UUID of the landlord.
        unit_id: UUID of the unit being rented.
        name: Full name of the tenant.
        email: Email address of the tenant.
        phone: Phone number (starting with +91).
        start_date: Lease start date (YYYY-MM-DD).
        end_date: Lease end date (YYYY-MM-DD).
        deposit_amount: Security deposit amount in INR.
    """
    return await asyncio.to_thread(
        _add_tenant_and_tenancy,
        landlord_id,
        unit_id,
        name,
        email,
        phone,
        start_date,
        end_date,
        deposit_amount,
    )
