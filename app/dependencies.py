from fastapi import Header, HTTPException
from supabase import create_client, Client

from app.config import settings

_supabase_client: Client | None = None


def verify_internal_request(x_internal_secret: str = Header(...)) -> str:
    """
    Verify that the request comes from our internal Next.js API.
    This is used instead of JWT verification for internal service-to-service calls.
    """
    if x_internal_secret != settings.internal_api_secret:
        raise HTTPException(
            status_code=401,
            detail="Invalid internal secret"
        )
    return x_internal_secret


def get_supabase() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(
            settings.supabase_url,
            settings.supabase_service_key,
        )
    return _supabase_client


def get_current_user_id() -> str:
    """
    DEPRECATED: Use get_current_user() instead.
    This function doesn't actually validate - it returns demo for backwards compatibility.
    """
    return settings.demo_landlord_id or "demo"


def validate_landlord_tenant_relationship(landlord_id: str, tenant_id: str) -> bool:
    """
    Validate that a tenant belongs to a landlord's portfolio.
    Used for authorization checks.
    """
    sb = get_supabase()
    
    # Get the tenant's unit
    tenancy_res = (
        sb.table("tenancies")
        .select("units(properties(landlord_id))")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    
    if not tenancy_res.data:
        return False
    
    tenancy = tenancy_res.data[0]
    unit = tenancy.get("units") or {}
    prop = unit.get("properties") or {}
    
    return prop.get("landlord_id") == landlord_id
