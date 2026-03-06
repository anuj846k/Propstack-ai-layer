"""Deterministic rent-cycle and payment status helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings
IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class RentTimeline:
    period_month: str
    due_date: date
    grace_date: date
    overdue_start_date: date


def period_month_for_date(current_date: date | None = None) -> str:
    target_date = current_date or datetime.now(IST).date()
    return target_date.strftime("%Y-%m")


def build_rent_timeline(
    period_month: str,
    due_day: int | None = None,
    grace_period_days: int | None = None,
) -> RentTimeline:
    year, month = _parse_period_month(period_month)
    due_day = min(max(due_day if due_day is not None else settings.rent_due_day, 1), 28)
    grace_period_days = (
        grace_period_days
        if grace_period_days is not None
        else settings.grace_period_days
    )

    due_date = date(year, month, due_day)
    grace_date = due_date.fromordinal(due_date.toordinal() + grace_period_days)
    overdue_start_date = grace_date.fromordinal(grace_date.toordinal() + 1)

    return RentTimeline(
        period_month=period_month,
        due_date=due_date,
        grace_date=grace_date,
        overdue_start_date=overdue_start_date,
    )


def derive_cycle_status(amount_due: float, amount_paid: float) -> str:
    if amount_paid >= amount_due:
        return "paid"
    if amount_paid > 0:
        return "partially_paid"
    return "unpaid"


def ensure_rent_cycle(
    sb,
    tenancy_id: str,
    period_month: str,
    amount_due: float,
) -> dict:
    existing = (
        sb.table("rent_cycles")
        .select("*")
        .eq("tenancy_id", tenancy_id)
        .eq("month", period_month)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]

    timeline = build_rent_timeline(period_month)
    created = (
        sb.table("rent_cycles")
        .insert(
            {
                "tenancy_id": tenancy_id,
                "month": period_month,
                "amount_due": amount_due,
                "amount_paid": 0,
                "status": "unpaid",
                "due_date": timeline.due_date.isoformat(),
                "grace_date": timeline.grace_date.isoformat(),
            }
        )
        .execute()
    )
    return (created.data or [{}])[0]


def update_cycle_on_payment(
    sb,
    tenant_id: str,
    unit_id: str,
    amount: float,
    period_month: str,
    paid_at: datetime | None = None,
) -> dict:
    tenancy_res = (
        sb.table("tenancies")
        .select("id, units(rent_amount, properties(landlord_id))")
        .eq("tenant_id", tenant_id)
        .eq("unit_id", unit_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not tenancy_res.data:
        return {
            "status": "error",
            "message": "Active tenancy not found",
            "error_message": "No active tenancy for tenant/unit",
            "data": None,
        }

    tenancy = tenancy_res.data[0]
    tenancy_id = tenancy["id"]
    unit = tenancy.get("units") or {}
    amount_due = float(unit.get("rent_amount") or amount)
    cycle = ensure_rent_cycle(sb, tenancy_id, period_month, amount_due)

    cycle_id = cycle["id"]
    current_paid = float(cycle.get("amount_paid") or 0)
    updated_paid = current_paid + float(amount)
    status = derive_cycle_status(amount_due, updated_paid)

    paid_at_dt = paid_at or datetime.now(timezone.utc)
    paid_at_iso = paid_at_dt.isoformat()

    updated = (
        sb.table("rent_cycles")
        .update(
            {
                "amount_paid": updated_paid,
                "status": status,
                "paid_at": paid_at_iso,
                "updated_at": paid_at_iso,
            }
        )
        .eq("id", cycle_id)
        .execute()
    )

    return {
        "status": "success",
        "message": "Rent cycle updated",
        "error_message": None,
        "data": {
            "tenancy_id": tenancy_id,
            "cycle_id": cycle_id,
            "amount_due": amount_due,
            "amount_paid": updated_paid,
            "cycle_status": status,
            "cycle": (updated.data or [cycle])[0],
        },
    }


def list_overdue_candidates(
    sb,
    period_month: str,
    as_of_date: date | None = None,
) -> list[dict]:
    today = as_of_date or datetime.now(IST).date()
    default_timeline = build_rent_timeline(period_month)

    tenancies_res = (
        sb.table("tenancies")
        .select(
            "id, tenant_id, unit_id, "
            "users!tenancies_tenant_id_fkey(id, name, phone, preferred_language), "
            "units(id, unit_number, rent_amount, properties(id, name, landlord_id))"
        )
        .eq("status", "active")
        .execute()
    )
    tenancies = tenancies_res.data or []
    tenancy_ids = [t["id"] for t in tenancies if t.get("id")]

    cycles_by_tenancy: dict[str, dict] = {}
    if tenancy_ids:
        cycles_res = (
            sb.table("rent_cycles")
            .select(
                "id, tenancy_id, month, status, amount_due, amount_paid, due_date, grace_date"
            )
            .in_("tenancy_id", tenancy_ids)
            .eq("month", period_month)
            .execute()
        )
        for cycle in cycles_res.data or []:
            cycles_by_tenancy[cycle["tenancy_id"]] = cycle

    landlord_names = _fetch_landlord_names(
        sb,
        {
            (t.get("units") or {}).get("properties", {}).get("landlord_id")
            for t in tenancies
            if (t.get("units") or {}).get("properties", {}).get("landlord_id")
        },
    )

    candidates: list[dict] = []
    for tenancy in tenancies:
        tenancy_id = tenancy.get("id")
        unit = tenancy.get("units") or {}
        prop = unit.get("properties") or {}
        tenant_user = tenancy.get("users") or {}

        landlord_id = prop.get("landlord_id")
        if not tenancy_id or not landlord_id:
            continue

        cycle = cycles_by_tenancy.get(tenancy_id)
        amount_due = float((cycle or {}).get("amount_due") or unit.get("rent_amount") or 0)
        amount_paid = float((cycle or {}).get("amount_paid") or 0)
        outstanding = max(amount_due - amount_paid, 0)
        if outstanding <= 0:
            continue

        status = str((cycle or {}).get("status") or derive_cycle_status(amount_due, amount_paid)).lower()
        if status == "paid":
            continue

        due_date = _safe_date((cycle or {}).get("due_date"), default_timeline.due_date)
        grace_date = _safe_date((cycle or {}).get("grace_date"), default_timeline.grace_date)
        is_overdue = today > grace_date
        if not is_overdue:
            continue

        candidates.append(
            {
                "tenancy_id": tenancy_id,
                "tenant_id": tenancy.get("tenant_id") or tenant_user.get("id"),
                "tenant_name": tenant_user.get("name"),
                "tenant_phone": tenant_user.get("phone"),
                "preferred_language": tenant_user.get("preferred_language") or "english",
                "unit_id": tenancy.get("unit_id") or unit.get("id"),
                "unit_number": unit.get("unit_number"),
                "property_id": prop.get("id"),
                "property_name": prop.get("name"),
                "landlord_id": landlord_id,
                "landlord_name": landlord_names.get(landlord_id) or "Landlord",
                "period_month": period_month,
                "amount_due": amount_due,
                "amount_paid": amount_paid,
                "amount_outstanding": outstanding,
                "cycle_id": (cycle or {}).get("id"),
                "cycle_status": status,
                "due_date": due_date.isoformat(),
                "grace_date": grace_date.isoformat(),
                "days_overdue": max((today - grace_date).days, 0),
            }
        )

    return candidates


def mark_candidate_cycle_overdue(
    sb,
    candidate: dict,
    period_month: str,
    as_of_date: date | None = None,
) -> dict:
    today = as_of_date or datetime.now(IST).date()
    timeline = build_rent_timeline(period_month)
    grace_date = _safe_date(candidate.get("grace_date"), timeline.grace_date)

    amount_due = float(candidate.get("amount_due") or 0)
    amount_paid = float(candidate.get("amount_paid") or 0)
    outstanding = max(amount_due - amount_paid, 0)
    if outstanding <= 0:
        return {"status": "success", "message": "Cycle already settled", "data": None, "error_message": None}

    cycle = ensure_rent_cycle(sb, candidate["tenancy_id"], period_month, amount_due)
    if today <= grace_date:
        return {"status": "success", "message": "Cycle not overdue yet", "data": cycle, "error_message": None}

    if str(cycle.get("status", "")).lower() != "overdue":
        updated = (
            sb.table("rent_cycles")
            .update({"status": "overdue", "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", cycle["id"])
            .execute()
        )
        cycle = (updated.data or [cycle])[0]

    return {
        "status": "success",
        "message": "Cycle marked overdue",
        "error_message": None,
        "data": cycle,
    }


def _parse_period_month(period_month: str) -> tuple[int, int]:
    try:
        year, month = period_month.split("-")
        parsed_year = int(year)
        parsed_month = int(month)
        if parsed_month < 1 or parsed_month > 12:
            raise ValueError
        return parsed_year, parsed_month
    except ValueError as exc:
        raise ValueError("period_month must be in YYYY-MM format") from exc


def _safe_date(raw_value: object, fallback: date) -> date:
    if not raw_value:
        return fallback
    try:
        return date.fromisoformat(str(raw_value)[:10])
    except ValueError:
        return fallback


def _fetch_landlord_names(sb, landlord_ids: set[str]) -> dict[str, str]:
    if not landlord_ids:
        return {}
    result = (
        sb.table("users")
        .select("id, name")
        .in_("id", list(landlord_ids))
        .execute()
    )
    return {row["id"]: row.get("name") or "Landlord" for row in (result.data or [])}
