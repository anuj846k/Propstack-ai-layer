import logging

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.dependencies import get_supabase, verify_internal_request

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_landlord_id_from_request(
    x_landlord_id: str | None = Header(None, alias="x-landlord-id"),
    _x_internal_secret: str = Depends(verify_internal_request),
) -> str:
    if not x_landlord_id:
        raise HTTPException(status_code=400, detail="Missing x-landlord-id header")
    return x_landlord_id


def _ticket_image_proxy_path(ticket_id: str) -> str:
    return f"/api/v1/maintenance/tickets/{ticket_id}/image"


def _ticket_image_item_proxy_path(ticket_id: str, ticket_image_id: str) -> str:
    return f"/api/v1/maintenance/tickets/{ticket_id}/images/{ticket_image_id}"


class TicketImageItem(BaseModel):
    id: str
    image_url: str
    uploaded_at: str | None = None
    image_proxy_url: str


class DispatchLogItem(BaseModel):
    id: str
    vendor_id: str | None = None
    status: str | None = None
    created_at: str | None = None
    provider_call_sid: str | None = None


class VendorSummary(BaseModel):
    id: str
    name: str | None = None
    phone: str | None = None
    specialty: str | None = None


class TenantSummary(BaseModel):
    id: str
    name: str | None = None
    phone: str | None = None


class UnitSummary(BaseModel):
    id: str
    unit_number: str | None = None
    property_id: str | None = None
    property_name: str | None = None
    property_address: str | None = None


class MaintenanceTicketListItem(BaseModel):
    id: str
    title: str | None = None
    issue_category: str | None = None
    issue_description: str | None = None
    priority: str | None = None
    status: str | None = None
    ai_severity_score: int | None = None
    ai_summary: str | None = None
    scheduled_at: str | None = None
    resolved_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    unit: UnitSummary | None = None
    tenant: TenantSummary | None = None
    assigned_vendor: VendorSummary | None = None

    image_url: str | None = None
    image_proxy_url: str | None = None
    images: list[TicketImageItem] = Field(default_factory=list)
    latest_dispatch_status: str | None = None


class MaintenanceTicketDetailResponse(MaintenanceTicketListItem):
    dispatch_logs: list[DispatchLogItem] = Field(default_factory=list)


@router.get("/maintenance/tickets", response_model=list[MaintenanceTicketListItem])
async def list_maintenance_tickets(
    status: str | None = None,
    tenant_id: str | None = None,
    unit_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
    landlord_id: str = Depends(_get_landlord_id_from_request),
) -> list[MaintenanceTicketListItem]:
    """
    List maintenance tickets for the authenticated landlord.
    Includes image URLs and proxy URLs for Twilio-protected media.

    Pagination is cursorless: use page & page_size to navigate.
    """
    page = max(int(page), 1)
    page_size = max(1, min(int(page_size), 100))
    offset = (page - 1) * page_size
    sb = get_supabase()

    props_res = (
        sb.table("properties")
        .select("id, name, address")
        .eq("landlord_id", landlord_id)
        .execute()
    )
    props = {p["id"]: p for p in (props_res.data or []) if p.get("id")}
    property_ids = list(props.keys())
    if not property_ids:
        return []

    units_res = (
        sb.table("units")
        .select("id, unit_number, property_id")
        .in_("property_id", property_ids)
        .execute()
    )
    units = {u["id"]: u for u in (units_res.data or []) if u.get("id")}
    unit_ids = list(units.keys())
    if not unit_ids:
        return []

    query = (
        sb.table("maintenance_tickets")
        .select(
            "id, unit_id, tenant_id, assigned_vendor_id, title, issue_category, issue_description, "
            "priority, status, ai_severity_score, ai_summary, scheduled_at, resolved_at, "
            "created_at, updated_at, image_url"
        )
        .in_("unit_id", unit_ids)
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
    )
    if status:
        query = query.eq("status", status)
    if tenant_id:
        query = query.eq("tenant_id", tenant_id)
    if unit_id:
        query = query.eq("unit_id", unit_id)

    tickets_res = query.execute()
    tickets = tickets_res.data or []
    if not tickets:
        return []

    tenant_ids = sorted({t.get("tenant_id") for t in tickets if t.get("tenant_id")})
    vendor_ids = sorted(
        {t.get("assigned_vendor_id") for t in tickets if t.get("assigned_vendor_id")}
    )
    ticket_ids = [t["id"] for t in tickets if t.get("id")]

    tenants_by_id: dict[str, dict] = {}
    if tenant_ids:
        tenants_res = (
            sb.table("users")
            .select("id, name, phone")
            .in_("id", tenant_ids)
            .execute()
        )
        tenants_by_id = {u["id"]: u for u in (tenants_res.data or []) if u.get("id")}

    vendors_by_id: dict[str, dict] = {}
    if vendor_ids:
        vendors_res = (
            sb.table("vendors")
            .select("id, name, phone, specialty")
            .in_("id", vendor_ids)
            .execute()
        )
        vendors_by_id = {v["id"]: v for v in (vendors_res.data or []) if v.get("id")}

    images_by_ticket: dict[str, list[dict]] = {}
    if ticket_ids:
        imgs_res = (
            sb.table("ticket_images")
            .select("id, ticket_id, image_url, uploaded_at")
            .in_("ticket_id", ticket_ids)
            .order("uploaded_at", desc=True)
            .execute()
        )
        for row in imgs_res.data or []:
            tid = row.get("ticket_id")
            if tid:
                images_by_ticket.setdefault(tid, []).append(row)

    dispatch_status_by_ticket: dict[str, str] = {}
    if ticket_ids:
        dispatch_res = (
            sb.table("vendor_dispatch_logs")
            .select("ticket_id, status, created_at")
            .in_("ticket_id", ticket_ids)
            .order("created_at", desc=True)
            .execute()
        )
        for row in dispatch_res.data or []:
            tid = row.get("ticket_id")
            if tid and tid not in dispatch_status_by_ticket and row.get("status"):
                dispatch_status_by_ticket[tid] = row["status"]

    items: list[MaintenanceTicketListItem] = []
    for t in tickets:
        tid = t.get("id")
        uid = t.get("unit_id")
        unit = units.get(uid) if uid else None
        prop = props.get(unit.get("property_id")) if unit else None

        primary_image_url = t.get("image_url")
        primary_proxy = _ticket_image_proxy_path(tid) if (tid and primary_image_url) else None

        images: list[TicketImageItem] = []
        for img in images_by_ticket.get(tid, []):
            img_id = img.get("id")
            img_url = img.get("image_url")
            if not img_id or not img_url:
                continue
            images.append(
                TicketImageItem(
                    id=img_id,
                    image_url=img_url,
                    uploaded_at=img.get("uploaded_at"),
                    image_proxy_url=_ticket_image_item_proxy_path(tid, img_id),
                )
            )

        tenant = tenants_by_id.get(t.get("tenant_id") or "")
        vendor = vendors_by_id.get(t.get("assigned_vendor_id") or "")

        items.append(
            MaintenanceTicketListItem(
                id=tid,
                title=t.get("title"),
                issue_category=t.get("issue_category"),
                issue_description=t.get("issue_description"),
                priority=t.get("priority"),
                status=t.get("status"),
                ai_severity_score=t.get("ai_severity_score"),
                ai_summary=t.get("ai_summary"),
                scheduled_at=t.get("scheduled_at"),
                resolved_at=t.get("resolved_at"),
                created_at=t.get("created_at"),
                updated_at=t.get("updated_at"),
                unit=UnitSummary(
                    id=uid,
                    unit_number=(unit or {}).get("unit_number"),
                    property_id=(unit or {}).get("property_id"),
                    property_name=(prop or {}).get("name"),
                    property_address=(prop or {}).get("address"),
                )
                if uid
                else None,
                tenant=TenantSummary(
                    id=tenant.get("id"),
                    name=tenant.get("name"),
                    phone=tenant.get("phone"),
                )
                if tenant
                else None,
                assigned_vendor=VendorSummary(
                    id=vendor.get("id"),
                    name=vendor.get("name"),
                    phone=vendor.get("phone"),
                    specialty=vendor.get("specialty"),
                )
                if vendor
                else None,
                image_url=primary_image_url,
                image_proxy_url=primary_proxy,
                images=images,
                latest_dispatch_status=dispatch_status_by_ticket.get(tid),
            )
        )

    return items


@router.get("/maintenance/tickets/{ticket_id}", response_model=MaintenanceTicketDetailResponse)
async def get_maintenance_ticket(
    ticket_id: str,
    landlord_id: str = Depends(_get_landlord_id_from_request),
) -> MaintenanceTicketDetailResponse:
    sb = get_supabase()

    ticket_res = (
        sb.table("maintenance_tickets")
        .select("*")
        .eq("id", ticket_id)
        .limit(1)
        .execute()
    )
    if not ticket_res.data:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = ticket_res.data[0]

    unit_id = ticket.get("unit_id")
    unit = None
    prop = None
    if unit_id:
        unit_res = (
            sb.table("units")
            .select("id, unit_number, property_id, properties(id, name, address, landlord_id)")
            .eq("id", unit_id)
            .limit(1)
            .execute()
        )
        if unit_res.data:
            unit = unit_res.data[0]
            prop = (unit.get("properties") or {}) if isinstance(unit.get("properties"), dict) else None

    if not prop or prop.get("landlord_id") != landlord_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    tenant = None
    if ticket.get("tenant_id"):
        tenant_res = (
            sb.table("users")
            .select("id, name, phone")
            .eq("id", ticket["tenant_id"])
            .limit(1)
            .execute()
        )
        tenant = tenant_res.data[0] if tenant_res.data else None

    vendor = None
    if ticket.get("assigned_vendor_id"):
        vendor_res = (
            sb.table("vendors")
            .select("id, name, phone, specialty")
            .eq("id", ticket["assigned_vendor_id"])
            .limit(1)
            .execute()
        )
        vendor = vendor_res.data[0] if vendor_res.data else None

    imgs_res = (
        sb.table("ticket_images")
        .select("id, ticket_id, image_url, uploaded_at")
        .eq("ticket_id", ticket_id)
        .order("uploaded_at", desc=True)
        .execute()
    )
    images: list[TicketImageItem] = []
    for img in imgs_res.data or []:
        img_id = img.get("id")
        img_url = img.get("image_url")
        if not img_id or not img_url:
            continue
        images.append(
            TicketImageItem(
                id=img_id,
                image_url=img_url,
                uploaded_at=img.get("uploaded_at"),
                image_proxy_url=_ticket_image_item_proxy_path(ticket_id, img_id),
            )
        )

    dispatch_res = (
        sb.table("vendor_dispatch_logs")
        .select("id, ticket_id, vendor_id, status, created_at, provider_call_sid")
        .eq("ticket_id", ticket_id)
        .order("created_at", desc=True)
        .execute()
    )
    dispatch_logs: list[DispatchLogItem] = []
    for row in dispatch_res.data or []:
        if not row.get("id"):
            continue
        dispatch_logs.append(
            DispatchLogItem(
                id=row["id"],
                vendor_id=row.get("vendor_id"),
                status=row.get("status"),
                created_at=row.get("created_at"),
                provider_call_sid=row.get("provider_call_sid"),
            )
        )

    primary_image_url = ticket.get("image_url")
    primary_proxy = _ticket_image_proxy_path(ticket_id) if primary_image_url else None

    return MaintenanceTicketDetailResponse(
        id=ticket_id,
        title=ticket.get("title"),
        issue_category=ticket.get("issue_category"),
        issue_description=ticket.get("issue_description"),
        priority=ticket.get("priority"),
        status=ticket.get("status"),
        ai_severity_score=ticket.get("ai_severity_score"),
        ai_summary=ticket.get("ai_summary"),
        scheduled_at=ticket.get("scheduled_at"),
        resolved_at=ticket.get("resolved_at"),
        created_at=ticket.get("created_at"),
        updated_at=ticket.get("updated_at"),
        unit=UnitSummary(
            id=unit.get("id"),
            unit_number=unit.get("unit_number"),
            property_id=unit.get("property_id"),
            property_name=(prop or {}).get("name"),
            property_address=(prop or {}).get("address"),
        )
        if unit
        else None,
        tenant=TenantSummary(id=tenant.get("id"), name=tenant.get("name"), phone=tenant.get("phone"))
        if tenant
        else None,
        assigned_vendor=VendorSummary(
            id=vendor.get("id"),
            name=vendor.get("name"),
            phone=vendor.get("phone"),
            specialty=vendor.get("specialty"),
        )
        if vendor
        else None,
        image_url=primary_image_url,
        image_proxy_url=primary_proxy,
        images=images,
        latest_dispatch_status=(dispatch_logs[0].status if dispatch_logs else None),
        dispatch_logs=dispatch_logs,
    )


async def _fetch_twilio_media(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        resp = await client.get(
            url,
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type") or "application/octet-stream"
        return resp.content, content_type


@router.get("/maintenance/tickets/{ticket_id}/image")
async def get_ticket_primary_image(
    ticket_id: str,
    landlord_id: str = Depends(_get_landlord_id_from_request),
) -> Response:
    sb = get_supabase()
    ticket_res = (
        sb.table("maintenance_tickets")
        .select("id, unit_id, image_url, units(properties(landlord_id))")
        .eq("id", ticket_id)
        .limit(1)
        .execute()
    )
    if not ticket_res.data:
        raise HTTPException(status_code=404, detail="Ticket not found")
    row = ticket_res.data[0]
    prop = ((row.get("units") or {}).get("properties") or {}) if row.get("units") else {}
    if prop.get("landlord_id") != landlord_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    url = row.get("image_url")
    if not url:
        raise HTTPException(status_code=404, detail="No image for ticket")

    try:
        content, content_type = await _fetch_twilio_media(url)
        return Response(content=content, media_type=content_type)
    except Exception as e:
        logger.warning("Failed to fetch ticket primary image ticket_id=%s: %s", ticket_id, e)
        raise HTTPException(status_code=502, detail="Failed to fetch image") from e


@router.get("/maintenance/tickets/{ticket_id}/images/{ticket_image_id}")
async def get_ticket_image(
    ticket_id: str,
    ticket_image_id: str,
    landlord_id: str = Depends(_get_landlord_id_from_request),
) -> Response:
    sb = get_supabase()

    ticket_res = (
        sb.table("maintenance_tickets")
        .select("id, units(properties(landlord_id))")
        .eq("id", ticket_id)
        .limit(1)
        .execute()
    )
    if not ticket_res.data:
        raise HTTPException(status_code=404, detail="Ticket not found")
    prop = (
        ((ticket_res.data[0].get("units") or {}).get("properties") or {})
        if ticket_res.data[0].get("units")
        else {}
    )
    if prop.get("landlord_id") != landlord_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    img_res = (
        sb.table("ticket_images")
        .select("id, image_url")
        .eq("id", ticket_image_id)
        .eq("ticket_id", ticket_id)
        .limit(1)
        .execute()
    )
    if not img_res.data:
        raise HTTPException(status_code=404, detail="Image not found")
    url = img_res.data[0].get("image_url")
    if not url:
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        content, content_type = await _fetch_twilio_media(url)
        return Response(content=content, media_type=content_type)
    except Exception as e:
        logger.warning(
            "Failed to fetch ticket image ticket_id=%s image_id=%s: %s",
            ticket_id,
            ticket_image_id,
            e,
        )
        raise HTTPException(status_code=502, detail="Failed to fetch image") from e

