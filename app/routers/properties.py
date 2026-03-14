import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.dependencies import get_supabase, verify_internal_request
from app.tools.rent_tools import (
    get_tenant_collection_history,
    get_tenant_payment_history,
    get_tenants_with_rent_status,
    list_units_for_landlord,
)
from app.tools.call_tools import initiate_rent_collection_call


router = APIRouter()


def get_landlord_id_from_request(
    x_landlord_id: str | None = Header(None, alias="x-landlord-id"),
    x_internal_secret: str = Depends(verify_internal_request),
) -> str:
    """
    Get landlord_id from request headers after verifying internal secret.
    The x-landlord-id header is set by Next.js after JWT verification.
    """
    if not x_landlord_id:
        raise HTTPException(status_code=400, detail="Missing x-landlord-id header")
    return x_landlord_id


class PropertyResponse(BaseModel):
    id: str
    name: str
    address: str | None
    total_units: int
    occupied_units: int
    vacant_units: int


class UnitResponse(BaseModel):
    id: str
    unit_number: str
    rent_amount: float
    occupancy_status: str
    tenant_id: str | None
    tenant_name: str | None
    tenancy_status: str | None


class TenantListItem(BaseModel):
    tenant_id: str
    tenant_name: str
    tenant_phone: str | None
    tenant_email: str | None
    unit_id: str | None
    unit_number: str
    property_name: str
    property_address: str | None
    rent_amount: float
    days_overdue: int
    is_overdue: bool
    payment_status: str


class TenantDetailResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    tenant_phone: str | None
    tenant_email: str | None
    preferred_language: str | None
    unit_id: str | None
    unit_number: str
    property_id: str | None
    property_name: str
    property_address: str | None
    rent_amount: float
    days_overdue: int
    is_overdue: bool
    payment_status: str
    recent_payments: list[dict[str, Any]] = Field(default_factory=list)
    recent_calls: list[dict[str, Any]] = Field(default_factory=list)


class CallHistoryItem(BaseModel):
    id: str
    outcome: str | None
    transcript: str | None
    duration_seconds: int | None
    created_at: str | None


class PaginatedCallHistoryResponse(BaseModel):
    calls: list[CallHistoryItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class CallInitiationRequest(BaseModel):
    landlord_id: str | None = None


class CallInitiationResponse(BaseModel):
    call_id: str | None
    status: str
    message: str
    provider_status: str | None
    error_message: str | None = None


def _resolve_landlord_id(landlord_id: str) -> str:
    # Demo landlord aliasing removed: always use the explicit landlord_id.
    return landlord_id


def _find_landlord_name(landlord_id: str) -> str:
    sb = get_supabase()
    result = sb.table("users").select("name").eq("id", landlord_id).limit(1).execute()
    if result.data:
        return result.data[0].get("name") or "Landlord"
    return "Landlord"


@router.get("/properties", response_model=list[PropertyResponse])
async def list_properties(
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> list[PropertyResponse]:
    """List all properties for the authenticated landlord with unit counts."""
    landlord_id = _resolve_landlord_id(landlord_id)
    sb = get_supabase()

    result = (
        sb.table("properties")
        .select("id, name, address")
        .eq("landlord_id", landlord_id)
        .execute()
    )

    properties: list[PropertyResponse] = []
    for prop in result.data or []:
        prop_id = prop.get("id")
        if not prop_id:
            continue

        units_res = (
            sb.table("units")
            .select("id, is_occupied")
            .eq("property_id", prop_id)
            .execute()
        )

        total = len(units_res.data or [])
        occupied = sum(1 for u in units_res.data or [] if u.get("is_occupied"))
        vacant = total - occupied

        properties.append(
            PropertyResponse(
                id=prop_id,
                name=prop.get("name") or "Unnamed",
                address=prop.get("address"),
                total_units=total,
                occupied_units=occupied,
                vacant_units=vacant,
            )
        )

    return properties


@router.get("/properties/{property_id}/units", response_model=list[UnitResponse])
async def list_property_units(
    property_id: str,
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> list[UnitResponse]:
    """List all units for a specific property (owned by authenticated landlord)."""
    landlord_id = _resolve_landlord_id(landlord_id)
    sb = get_supabase()

    # Verify property belongs to landlord
    prop_res = (
        sb.table("properties")
        .select("id")
        .eq("id", property_id)
        .eq("landlord_id", landlord_id)
        .limit(1)
        .execute()
    )
    if not prop_res.data:
        raise HTTPException(status_code=404, detail="Property not found")

    units_res = (
        sb.table("units")
        .select("id, unit_number, rent_amount, is_occupied")
        .eq("property_id", property_id)
        .execute()
    )

    unit_ids = [u.get("id") for u in units_res.data or [] if u.get("id")]

    tenancies_by_unit: dict[str, dict] = {}
    if unit_ids:
        tenancies_res = (
            sb.table("tenancies")
            .select(
                "id, status, unit_id, tenant_id, users!tenancies_tenant_id_fkey(name)"
            )
            .in_("unit_id", unit_ids)
            .eq("status", "active")
            .execute()
        )
        for t in tenancies_res.data or []:
            unit_id = t.get("unit_id")
            if unit_id:
                tenancies_by_unit[unit_id] = t

    units: list[UnitResponse] = []
    for unit in units_res.data or []:
        unit_id = unit.get("id")
        tenancy = tenancies_by_unit.get(unit_id)
        tenant_user = tenancy.get("users") or {} if tenancy else {}

        units.append(
            UnitResponse(
                id=unit_id,
                unit_number=unit.get("unit_number") or "",
                rent_amount=unit.get("rent_amount") or 0,
                occupancy_status="occupied" if unit.get("is_occupied") else "vacant",
                tenant_id=tenancy.get("tenant_id") if tenancy else None,
                tenant_name=tenant_user.get("name"),
                tenancy_status=tenancy.get("status") if tenancy else None,
            )
        )

    return units


@router.get("/tenants", response_model=list[TenantListItem])
async def list_tenants(
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> list[TenantListItem]:
    """List all tenants for the authenticated landlord with their rent status."""
    landlord_id = _resolve_landlord_id(landlord_id)

    result = await get_tenants_with_rent_status(landlord_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error_message"))

    tenants = result.get("tenants", [])
    return [
        TenantListItem(
            tenant_id=t.get("tenant_id") or "",
            tenant_name=t.get("tenant_name") or "Unknown",
            tenant_phone=t.get("tenant_phone"),
            tenant_email=t.get("tenant_email"),
            unit_id=t.get("unit_id"),
            unit_number=t.get("unit_number") or "",
            property_name=t.get("property_name") or "",
            property_address=t.get("property_address"),
            rent_amount=t.get("rent_amount") or 0,
            days_overdue=t.get("days_overdue") or 0,
            is_overdue=t.get("is_overdue") or False,
            payment_status=t.get("payment_status") or "unpaid",
        )
        for t in tenants
    ]


@router.get("/tenants/{tenant_id}", response_model=TenantDetailResponse)
async def get_tenant_detail(
    tenant_id: str,
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> TenantDetailResponse:
    """Get detailed information about a specific tenant."""
    landlord_id = _resolve_landlord_id(landlord_id)
    sb = get_supabase()

    # First verify tenant belongs to this landlord
    tenancy_check = (
        sb.table("tenancies")
        .select(
            "id, units!tenancies_unit_id_fkey(properties!units_property_id_fkey(landlord_id))"
        )
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )

    if not tenancy_check.data:
        raise HTTPException(
            status_code=404, detail="Tenant not found or no active tenancy"
        )

    # Verify landlord owns this tenant
    tenancy = tenancy_check.data[0]
    unit_data = tenancy.get("units") or {}
    prop_data = unit_data.get("properties") or {}

    if prop_data.get("landlord_id") != landlord_id:
        raise HTTPException(
            status_code=403, detail="Not authorized to view this tenant"
        )

    tenancy_res = (
        sb.table("tenancies")
        .select(
            """
            id,
            users!tenancies_tenant_id_fkey(id, name, phone, email, preferred_language),
            units!tenancies_unit_id_fkey(
                id, unit_number, rent_amount,
                properties!units_property_id_fkey(id, name, address)
            )
        """
        )
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )

    if not tenancy_res.data:
        raise HTTPException(
            status_code=404, detail="Tenant not found or no active tenancy"
        )

    tenancy = tenancy_res.data[0]
    user_data = tenancy.get("users") or {}
    unit_data = tenancy.get("units") or {}
    prop_data = unit_data.get("properties") or {}

    tenant_name = user_data.get("name") or "Unknown"
    property_id = prop_data.get("id")
    rent_amount = unit_data.get("rent_amount") or 0
    unit_number = unit_data.get("unit_number") or ""

    from datetime import date, timedelta
    from app.config import settings

    today = date.today()
    due_day = min(settings.rent_due_day, 28)
    due_date = today.replace(day=due_day)
    overdue_threshold = due_date + timedelta(days=settings.grace_period_days)
    days_overdue = max((today - overdue_threshold).days, 0)

    payment_result = await get_tenant_payment_history(tenant_id)
    recent_payments = payment_result.get("payments", [])[:5]

    call_result = await get_tenant_collection_history(tenant_id)
    recent_calls = call_result.get("call_history", [])[:5]

    return TenantDetailResponse(
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        tenant_phone=user_data.get("phone"),
        tenant_email=user_data.get("email"),
        preferred_language=user_data.get("preferred_language"),
        unit_id=unit_data.get("id"),
        unit_number=unit_number,
        property_id=property_id,
        property_name=prop_data.get("name") or "",
        property_address=prop_data.get("address"),
        rent_amount=rent_amount,
        days_overdue=days_overdue,
        is_overdue=days_overdue > 0,
        payment_status="unpaid",
        recent_payments=recent_payments,
        recent_calls=recent_calls,
    )


@router.get("/tenants/{tenant_id}/calls", response_model=PaginatedCallHistoryResponse)
async def get_tenant_calls(
    tenant_id: str,
    landlord_id: str = Depends(get_landlord_id_from_request),
    page: int = 1,
    page_size: int = 10,
) -> PaginatedCallHistoryResponse:
    """Get call history for a specific tenant (must belong to authenticated landlord)."""
    landlord_id = _resolve_landlord_id(landlord_id)

    # Verify tenant belongs to landlord
    sb = get_supabase()
    tenancy_check = (
        sb.table("tenancies")
        .select(
            "id, units!tenancies_unit_id_fkey(properties!units_property_id_fkey(landlord_id))"
        )
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )

    if not tenancy_check.data:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenancy = tenancy_check.data[0]
    unit_data = tenancy.get("units") or {}
    prop_data = unit_data.get("properties") or {}

    if prop_data.get("landlord_id") != landlord_id:
        raise HTTPException(
            status_code=403, detail="Not authorized to view this tenant's calls"
        )
    """Get call history for a specific tenant with pagination."""
    sb = get_supabase()

    count_res = (
        sb.table("call_logs")
        .select("*", count="exact")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    total = count_res.count or 0

    offset = (page - 1) * page_size

    result = (
        sb.table("call_logs")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )

    calls = result.data or []
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return PaginatedCallHistoryResponse(
        calls=[
            CallHistoryItem(
                id=c.get("id") or "",
                outcome=c.get("outcome"),
                transcript=c.get("transcript"),
                duration_seconds=c.get("duration_seconds"),
                created_at=c.get("created_at"),
            )
            for c in calls
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/tenants/{tenant_id}/call", response_model=CallInitiationResponse)
async def initiate_tenant_call(
    tenant_id: str,
    body: CallInitiationRequest,
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> CallInitiationResponse:
    """Initiate a rent collection call for a specific tenant."""
    landlord_id = _resolve_landlord_id(landlord_id)

    tenants_result = await get_tenants_with_rent_status(landlord_id)
    if tenants_result.get("status") == "error":
        raise HTTPException(status_code=500, detail=tenants_result.get("error_message"))

    tenant = next(
        (
            t
            for t in tenants_result.get("tenants", [])
            if t.get("tenant_id") == tenant_id
        ),
        None,
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found for landlord")

    # Check if tenant is overdue - don't call if already paid
    if not tenant.get("is_overdue"):
        return CallInitiationResponse(
            call_id=None,
            status="failed",
            message="Tenant is not overdue (paid up to date). Call not initiated.",
            provider_status=None,
        )

    landlord_name = _find_landlord_name(landlord_id)

    call_result = await initiate_rent_collection_call(
        landlord_id=landlord_id,
        tenant_id=tenant_id,
        tenant_name=tenant.get("tenant_name") or "Tenant",
        tenant_phone=tenant.get("tenant_phone") or "",
        language=tenant.get("preferred_language") or "english",
        rent_amount=str(tenant.get("rent_amount") or "0"),
        days_overdue=str(tenant.get("days_overdue") or "0"),
        property_name=tenant.get("property_name") or "",
        unit_number=tenant.get("unit_number") or "",
        landlord_name=landlord_name,
    )

    call_id = call_result.get("data", {}).get("call_id") or call_result.get("call_id")
    status = call_result.get("status") or "failed"
    message = call_result.get("message") or "Call failed"
    provider_status = call_result.get("data", {}).get("provider_status")
    error_message = call_result.get("error_message")

    return CallInitiationResponse(
        call_id=call_id,
        status=status,
        message=message,
        provider_status=provider_status,
        error_message=error_message,
    )
