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
            matching_tenants.append({
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
            })
    
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
        }
    
    # Multiple matches - ask for clarification
    clarification_options = [
        {
            "option": i + 1,
            "name": t["tenant_name"],
            "phone": t["tenant_phone"],
            "unit": t["unit_number"],
            "property": t["property_name"],
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
            + "\n".join([f"{i+1}. {t['name']} - {t['property_name']}, Unit {t['unit_number']}" 
                        for i, t in enumerate(clarification_options)])
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
        cleaned_phone = tenant_phone.replace("+91", "").replace(" ", "").replace("-", "")
        
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
                }
            }
    
    return {
        "status": "not_found",
        "message": "No tenant found with that phone number"
    }
