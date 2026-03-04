"""Deterministic call policy checks used by routes and ADK callbacks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_CALL_WINDOW_START_HOUR = 9
DEFAULT_CALL_WINDOW_END_HOUR = 20
DEFAULT_MAX_ATTEMPTS_PER_DAY = 2


def evaluate_call_policy(
    attempts_today: int,
    now_utc: datetime | None = None,
    start_hour_ist: int = DEFAULT_CALL_WINDOW_START_HOUR,
    end_hour_ist: int = DEFAULT_CALL_WINDOW_END_HOUR,
    max_attempts_per_day: int = DEFAULT_MAX_ATTEMPTS_PER_DAY,
) -> tuple[bool, str]:
    current_utc = now_utc or datetime.now(timezone.utc)
    current_ist = current_utc.astimezone(IST)

    if not is_within_call_window(current_ist, start_hour_ist, end_hour_ist):
        return (
            False,
            f"Call blocked: outside permitted window ({start_hour_ist:02d}:00-{end_hour_ist:02d}:00 IST)",
        )

    if attempts_today >= max_attempts_per_day:
        return (
            False,
            f"Call blocked: max {max_attempts_per_day} attempts reached for today",
        )

    return True, "Call policy check passed"


def is_within_call_window(
    dt_ist: datetime,
    start_hour_ist: int = DEFAULT_CALL_WINDOW_START_HOUR,
    end_hour_ist: int = DEFAULT_CALL_WINDOW_END_HOUR,
) -> bool:
    minutes_since_midnight = dt_ist.hour * 60 + dt_ist.minute
    start = start_hour_ist * 60
    end = end_hour_ist * 60
    return start <= minutes_since_midnight < end


def get_ist_day_utc_range(now_utc: datetime | None = None) -> tuple[str, str]:
    current_utc = now_utc or datetime.now(timezone.utc)
    current_ist = current_utc.astimezone(IST)

    day_start_ist = current_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_ist = day_start_ist + timedelta(days=1)

    return day_start_ist.astimezone(timezone.utc).isoformat(), day_end_ist.astimezone(timezone.utc).isoformat()


def count_call_attempts_today(
    sb,
    tenant_id: str,
    landlord_id: str | None = None,
    now_utc: datetime | None = None,
) -> int:
    start_utc, end_utc = get_ist_day_utc_range(now_utc)

    query = (
        sb.table("call_logs")
        .select("id", count="exact")
        .eq("tenant_id", tenant_id)
        .gte("created_at", start_utc)
        .lt("created_at", end_utc)
    )

    if landlord_id:
        query = query.eq("landlord_id", landlord_id)

    result = query.execute()
    if result.count is not None:
        return int(result.count)

    return len(result.data or [])


def validate_tenant_landlord_ownership(sb, landlord_id: str, tenant_id: str) -> bool:
    try:
        result = (
            sb.table("tenancies")
            .select("id")
            .eq("tenant_id", tenant_id)
            .eq("status", "active")
            .eq("units.properties.landlord_id", landlord_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return True
    except Exception:
        # Not all client versions support nested filter paths.
        pass

    # Fallback query style for clients that do not support nested eq filters.
    fallback = (
        sb.table("tenancies")
        .select("id, units(properties(landlord_id))")
        .eq("tenant_id", tenant_id)
        .eq("status", "active")
        .limit(5)
        .execute()
    )

    for row in fallback.data or []:
        unit = row.get("units") or {}
        prop = unit.get("properties") or {}
        if prop.get("landlord_id") == landlord_id:
            return True

    return False


def get_policy_limits() -> dict:
    return {
        "start_hour_ist": getattr(settings, "call_window_start_hour", DEFAULT_CALL_WINDOW_START_HOUR),
        "end_hour_ist": getattr(settings, "call_window_end_hour", DEFAULT_CALL_WINDOW_END_HOUR),
        "max_attempts_per_day": getattr(settings, "max_call_attempts_per_tenant_per_day", DEFAULT_MAX_ATTEMPTS_PER_DAY),
    }
