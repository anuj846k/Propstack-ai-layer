from datetime import datetime, timezone

from app.dependencies import get_supabase


def find_tenant_by_name(name: str, landlord_id: str) -> dict:
    """
    Find tenants by name (partial or full match) for a specific landlord.

    Args:
        name: Tenant name to search for (partial or full match)
        landlord_id: The landlord's ID to filter tenants

    Returns:
        dict with status and results:
        - If single match: returns tenant details
        - If multiple matches: returns list with ask_for_clarification flag
        - If no matches: returns empty list
    """
    sb = get_supabase()

    # Search for tenants with matching name (case-insensitive, partial match)
    search_term = f"%{name}%"

    result = (
        sb.table("tenancies")
        .select("""
            id,
            tenant_id,
            users!tenancies_tenant_id_fkey(
                id, name, phone, email, preferred_language
            ),
            units!tenancies_unit_id_fkey(
                id, unit_number, rent_amount,
                properties!units_property_id_fkey(
                    id, name, address, landlord_id
                )
            )
        """)
        .eq("status", "active")
        .execute()
    )

    # Filter by landlord and name match
    matching_tenants = []
    for tenancy in result.data or []:
        user_data = tenancy.get("users") or {}
        unit_data = tenancy.get("units") or {}
        prop_data = unit_data.get("properties") or {}

        # Check landlord ownership
        if prop_data.get("landlord_id") != landlord_id:
            continue

        # Check name match (case-insensitive partial)
        tenant_name = user_data.get("name") or ""
        if name.lower() in tenant_name.lower() or tenant_name.lower() in name.lower():
            matching_tenants.append(
                {
                    "tenancy_id": tenancy.get("id"),
                    "tenant_id": user_data.get("id"),
                    "tenant_name": tenant_name,
                    "tenant_phone": user_data.get("phone"),
                    "tenant_email": user_data.get("email"),
                    "preferred_language": user_data.get("preferred_language"),
                    "unit_id": unit_data.get("id"),
                    "unit_number": unit_data.get("unit_number"),
                    "rent_amount": unit_data.get("rent_amount"),
                    "property_name": prop_data.get("name"),
                    "property_address": prop_data.get("address"),
                }
            )

    if len(matching_tenants) == 0:
        return {
            "status": "success",
            "message": "No tenants found matching that name",
            "tenants": [],
            "ask_for_clarification": False,
        }

    if len(matching_tenants) == 1:
        tenant = matching_tenants[0]
        return {
            "status": "success",
            "message": "Found exactly one tenant",
            "tenants": [tenant],
            "ask_for_clarification": False,
            "selected_tenant": tenant,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # Multiple matches - ask for clarification
    clarification_options = [
        {
            "option": i + 1,
            "name": t["tenant_name"],
            "phone": t["tenant_phone"],
            "unit": t["unit_number"],
            "property": t.get("property_name", ""),
        }
        for i, t in enumerate(matching_tenants)
    ]

    return {
        "status": "success",
        "message": f"Found {len(matching_tenants)} tenants with similar names",
        "tenants": matching_tenants,
        "ask_for_clarification": True,
        "clarification_message": (
            f"I found {len(matching_tenants)} tenants with similar names. "
            "Please provide more details to identify the correct one:\n"
            + "\n".join(
                [
                    f"{i + 1}. {t['name']} - {t['property']}, Unit {t['unit']}"
                    for i, t in enumerate(clarification_options)
                ]
            )
        ),
    }


async def find_tenant_by_phone(phone: str, landlord_id: str) -> dict:
    """Find a tenant by phone number for a specific landlord."""
    sb = get_supabase()

    # Clean phone number
    phone = phone.replace("+91", "").replace(" ", "").replace("-", "")

    result = (
        sb.table("tenancies")
        .select("""
            id,
            tenant_id,
            users!tenancies_tenant_id_fkey(
                id, name, phone, email, preferred_language
            ),
            units!tenancies_unit_id_fkey(
                id, unit_number, rent_amount,
                properties!units_property_id_fkey(
                    id, name, address, landlord_id
                )
            )
        """)
        .eq("status", "active")
        .execute()
    )

    for tenancy in result.data or []:
        user_data = tenancy.get("users") or {}
        unit_data = tenancy.get("units") or {}
        prop_data = unit_data.get("properties") or {}

        # Check landlord ownership
        if prop_data.get("landlord_id") != landlord_id:
            continue

        # Check phone match
        tenant_phone = user_data.get("phone") or ""
        cleaned_phone = (
            tenant_phone.replace("+91", "").replace(" ", "").replace("-", "")
        )

        if phone in cleaned_phone or cleaned_phone in phone:
            return {
                "status": "success",
                "tenant": {
                    "tenancy_id": tenancy.get("id"),
                    "tenant_id": user_data.get("id"),
                    "tenant_name": user_data.get("name"),
                    "tenant_phone": user_data.get("phone"),
                    "tenant_email": user_data.get("email"),
                    "preferred_language": user_data.get("preferred_language"),
                    "unit_id": unit_data.get("id"),
                    "unit_number": unit_data.get("unit_number"),
                    "rent_amount": unit_data.get("rent_amount"),
                    "property_name": prop_data.get("name"),
                    "property_address": prop_data.get("address"),
                },
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

    return {
        "status": "not_found",
        "message": "No tenant found with that phone number",
    }


def update_tenant_details(
    tenant_id: str,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    preferred_language: str | None = None,
) -> dict:
    """
    Update core tenant details in the `users` table.

    This is used when the landlord provides corrected or updated tenant
    information such as name, phone number, email, or preferred language.

    Args:
        tenant_id: The Supabase user ID of the tenant to update.
        name: Optional new full name for the tenant.
        phone: Optional new phone number (ideally E.164, e.g. "+919876543210").
        email: Optional new email address.
        preferred_language: Optional new preferred language code or label.

    Returns:
        dict with:
        - status: "success" or "error"
        - message: Human-readable summary
        - tenant: Updated tenant row when successful
    """
    sb = get_supabase()

    update_data: dict = {}
    if name is not None:
        update_data["name"] = name.strip()
    if phone is not None:
        update_data["phone"] = phone.strip()
    if email is not None:
        update_data["email"] = email.strip()
    if preferred_language is not None:
        update_data["preferred_language"] = preferred_language.strip()

    if not update_data:
        return {
            "status": "error",
            "message": "At least one field (name, phone, email, preferred_language) must be provided.",
        }

    try:
        result = (
            sb.table("users")
            .update(update_data)
            .eq("id", tenant_id)
            .execute()
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "status": "error",
            "message": f"Failed to update tenant details: {exc}",
        }

    if not result.data:
        return {
            "status": "error",
            "message": "No tenant record was updated. Please confirm the tenant_id.",
        }

    return {
        "status": "success",
        "message": "Tenant details updated successfully.",
        "tenant": result.data[0],
    }
