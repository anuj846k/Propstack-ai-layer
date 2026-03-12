from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.adk.runners import Runner
from google.adk.tools import google_search
from google.genai import types

from app.config import settings
from app.dependencies import get_supabase
from app.services.session_service import get_session_service


@dataclass
class _VacancyUnit:
    unit_id: str
    unit_number: str | None
    property_id: str | None
    property_name: str | None
    property_address: str | None
    rent_amount: float
    days_vacant: int
    vacancy_cost: float


def _calculate_vacancy_cost_for_landlord(
    landlord_id: str,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Internal helper to compute vacancy cost for a landlord.

    A unit is considered vacant when it has no active tenancy. Vacancy cost is
    approximated as days_vacant * (rent_amount / 30).
    """
    sb = get_supabase()
    today = as_of or date.today()
    month_start = today.replace(day=1)

    # Fetch properties for landlord
    props_res = (
        sb.table("properties")
        .select("id, name, address")
        .eq("landlord_id", landlord_id)
        .execute()
    )
    props = {p["id"]: p for p in (props_res.data or []) if p.get("id")}
    property_ids = list(props.keys())
    if not property_ids:
        return {
            "status": "success",
            "summary": {
                "total_vacant_units": 0,
                "total_days_vacant": 0,
                "total_vacancy_cost": 0.0,
                "as_of_date": today.isoformat(),
                "month_start": month_start.isoformat(),
            },
            "units": [],
        }

    # Fetch units under these properties
    units_res = (
        sb.table("units")
        .select("id, unit_number, rent_amount, property_id")
        .in_("property_id", property_ids)
        .execute()
    )
    units = {u["id"]: u for u in (units_res.data or []) if u.get("id")}
    unit_ids = list(units.keys())
    if not unit_ids:
        return {
            "status": "success",
            "summary": {
                "total_vacant_units": 0,
                "total_days_vacant": 0,
                "total_vacancy_cost": 0.0,
                "as_of_date": today.isoformat(),
                "month_start": month_start.isoformat(),
            },
            "units": [],
        }

    # Active tenancies for these units (to detect occupied units)
    active_tenancies_res = (
        sb.table("tenancies")
        .select("unit_id")
        .in_("unit_id", unit_ids)
        .eq("status", "active")
        .execute()
    )
    occupied_unit_ids = {
        row["unit_id"]
        for row in (active_tenancies_res.data or [])
        if row.get("unit_id")
    }

    # All tenancies ordered by end_date desc to find last end per unit
    tenancies_res = (
        sb.table("tenancies")
        .select("unit_id, end_date")
        .in_("unit_id", unit_ids)
        .order("end_date", desc=True)
        .execute()
    )
    last_end_by_unit: dict[str, date] = {}
    for row in tenancies_res.data or []:
        unit_id = row.get("unit_id")
        if not unit_id or unit_id in last_end_by_unit:
            continue
        end_date_raw = row.get("end_date")
        if not end_date_raw:
            continue
        try:
            end_date = date.fromisoformat(str(end_date_raw)[:10])
        except ValueError:
            continue
        last_end_by_unit[unit_id] = end_date

    vacancy_units: list[_VacancyUnit] = []
    total_days_vacant = 0
    total_vacancy_cost = 0.0

    for unit_id, unit in units.items():
        if unit_id in occupied_unit_ids:
            continue

        # Determine vacancy start date
        last_end = last_end_by_unit.get(unit_id)
        if last_end:
            vacancy_start = max(last_end, month_start)
        else:
            vacancy_start = month_start

        days_vacant = max((today - vacancy_start).days, 0)
        rent_amount = float(unit.get("rent_amount") or 0)
        daily_rent = rent_amount / 30.0 if rent_amount > 0 else 0.0
        vacancy_cost = round(days_vacant * daily_rent, 2)

        prop = props.get(unit.get("property_id"))
        vacancy_units.append(
            _VacancyUnit(
                unit_id=unit_id,
                unit_number=unit.get("unit_number"),
                property_id=unit.get("property_id"),
                property_name=(prop or {}).get("name"),
                property_address=(prop or {}).get("address"),
                rent_amount=rent_amount,
                days_vacant=days_vacant,
                vacancy_cost=vacancy_cost,
            )
        )
        total_days_vacant += days_vacant
        total_vacancy_cost += vacancy_cost

    units_payload = [
        {
            "unit_id": u.unit_id,
            "unit_number": u.unit_number,
            "property_id": u.property_id,
            "property_name": u.property_name,
            "property_address": u.property_address,
            "rent_amount": u.rent_amount,
            "days_vacant": u.days_vacant,
            "vacancy_cost": u.vacancy_cost,
        }
        for u in vacancy_units
    ]

    return {
        "status": "success",
        "summary": {
            "total_vacant_units": len(vacancy_units),
            "total_days_vacant": total_days_vacant,
            "total_vacancy_cost": round(total_vacancy_cost, 2),
            "as_of_date": today.isoformat(),
            "month_start": month_start.isoformat(),
        },
        "units": units_payload,
    }


async def get_vacancy_cost_for_landlord(
    landlord_id: str,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Public tool wrapper that computes vacancy cost for a landlord.

    Args:
        landlord_id: The landlord's UUID.
        as_of_date: Optional ISO date string (YYYY-MM-DD). Defaults to today.

    Returns:
        dict: Envelope with status, summary, and per-unit details.
    """
    from datetime import date as _date

    as_of: _date | None = None
    if as_of_date:
        try:
            as_of = _date.fromisoformat(as_of_date)
        except ValueError:
            # Fallback to today if parsing fails
            as_of = None

    # Mirror pattern from other tools: run sync helper in thread.
    import asyncio

    try:
        return await asyncio.to_thread(
            _calculate_vacancy_cost_for_landlord,
            landlord_id,
            as_of,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {"status": "error", "error_message": str(exc)}


# ---------------------------------------------------------------------------
# Rent intelligence – Google Search grounded market rent estimator
# ---------------------------------------------------------------------------

_rent_intel_session_service = get_session_service()

_market_rent_agent = LlmAgent(
    name="market_rent_agent",
    model=settings.gemini_model,
    description=(
        "Estimates residential market rent for a given unit using Google Search. "
        "Always respond with a compact JSON object describing the estimate."
    ),
    instruction="""
You are a real-estate pricing assistant for rental properties.

Your job is to:
- Look up current rental listings for the given city / area using Google Search.
- Infer an approximate MONTHLY market rent for a unit matching the description.
- Return a single JSON object and nothing else.

JSON response format (all numbers in the same currency as the input rent_amount):
{
  "market_rent_estimate": number,   // best guess monthly rent
  "low": number,                    // lower bound of typical range
  "high": number,                   // upper bound of typical range
  "explanation": string             // 1-2 sentence explanation
}

Rules:
- Use google_search when you need fresh listings or price information.
- Prefer data from major rental portals in the tenant's country.
- Be conservative; do not overstate prices when listings vary widely.
- Do NOT wrap the JSON in backticks or any extra text. JSON only.
""",
    tools=[google_search],
    planner=BuiltInPlanner(
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_budget=256,
        )
    ),
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
    ),
)

_market_rent_runner = Runner(
    agent=_market_rent_agent,
    app_name="propstack_rent_intel",
    session_service=_rent_intel_session_service,
    auto_create_session=True,
)


async def estimate_market_rent_for_unit(
    *,
    city: str,
    state: str | None = None,
    unit_description: str,
    current_rent: float | None = None,
) -> dict[str, Any]:
    """Estimate market rent for a single unit using Google Search grounding.

    Args:
        city: City where the property is located.
        state: Optional state/region.
        unit_description: Free-form description (e.g., '2BHK, 900 sq ft, semi-furnished').
        current_rent: Optional current monthly rent for comparison.

    Returns:
        dict envelope with status, data, and raw_text.
    """
    parts: list[types.Part] = []

    lines = [
        f"City: {city}",
    ]
    if state:
        lines.append(f"State/Region: {state}")
    lines.append(f"Unit description: {unit_description}")
    if current_rent is not None:
        lines.append(f"Current monthly rent: {current_rent}")
    prompt = "\n".join(lines)

    parts.append(types.Part.from_text(text=prompt))
    content = types.Content(role="user", parts=parts)

    final_text = ""
    try:
        async for event in _market_rent_runner.run_async(
            user_id="rent_intelligence",
            session_id=f"rent_intel_{city.lower()}",
            new_message=content,
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text and part.text.strip():
                        final_text = part.text.strip()
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "status": "error",
            "error_message": f"Failed to call market rent agent: {exc}",
        }

    if not final_text:
        return {
            "status": "error",
            "error_message": "Market rent agent returned empty response.",
        }

    # Attempt to parse JSON from the response. Be forgiving if extra text appears.
    import json

    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(final_text)
    except json.JSONDecodeError:
        # Try to salvage JSON object if wrapped in text.
        start = final_text.find("{")
        end = final_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(final_text[start : end + 1])
            except json.JSONDecodeError:
                payload = None

    if not isinstance(payload, dict):
        return {
            "status": "error",
            "error_message": "Market rent agent did not return valid JSON.",
            "raw_text": final_text,
        }

    data = {
        "market_rent_estimate": float(payload.get("market_rent_estimate") or 0.0),
        "low": float(payload.get("low") or 0.0),
        "high": float(payload.get("high") or 0.0),
        "explanation": str(payload.get("explanation") or ""),
    }

    return {
        "status": "success",
        "data": data,
        "raw_text": final_text,
    }


async def analyze_rent_intelligence_for_landlord(
    landlord_id: str,
    sample_limit: int = 10,
) -> dict[str, Any]:
    """Analyze rent intelligence for a landlord's portfolio.

    Uses Supabase for unit data and estimate_market_rent_for_unit for comps.
    """
    sb = get_supabase()

    # Fetch properties with city/state for this landlord
    props_res = (
        sb.table("properties")
        .select("id, name, address, city, state")
        .eq("landlord_id", landlord_id)
        .execute()
    )
    props = {p["id"]: p for p in (props_res.data or []) if p.get("id")}
    property_ids = list(props.keys())
    if not property_ids:
        return {
            "status": "success",
            "summary": {
                "underpriced_units": 0,
                "total_units_evaluated": 0,
                "estimated_monthly_uplift": 0.0,
            },
            "units": [],
        }

    units_res = (
        sb.table("units")
        .select("id, unit_number, rent_amount, property_id")
        .in_("property_id", property_ids)
        .execute()
    )
    units = [u for u in (units_res.data or []) if u.get("id")]
    if not units:
        return {
            "status": "success",
            "summary": {
                "underpriced_units": 0,
                "total_units_evaluated": 0,
                "estimated_monthly_uplift": 0.0,
            },
            "units": [],
        }

    # Limit number of units we call Google Search for, to keep latency/cost bounded.
    sample = units[: max(1, sample_limit)]

    underpriced_units = 0
    total_uplift = 0.0
    unit_results: list[dict[str, Any]] = []

    for unit in sample:
        prop = props.get(unit.get("property_id"))
        city = (prop or {}).get("city") or ""
        state = (prop or {}).get("state") or None
        rent_amount = float(unit.get("rent_amount") or 0.0)

        if not city or rent_amount <= 0:
            continue

        description = f"Rental unit {unit.get('unit_number') or ''} in {city}, {state or ''}"
        market_res = await estimate_market_rent_for_unit(
            city=city,
            state=state,
            unit_description=description,
            current_rent=rent_amount,
        )
        if market_res.get("status") != "success":
            continue

        data = market_res.get("data") or {}
        market_rent = float(data.get("market_rent_estimate") or 0.0)
        if market_rent <= 0:
            continue

        delta = market_rent - rent_amount
        delta_pct = (delta / rent_amount * 100.0) if rent_amount > 0 else 0.0

        is_underpriced = delta_pct >= 15.0
        if is_underpriced and delta > 0:
            underpriced_units += 1
            total_uplift += delta

        unit_results.append(
            {
                "unit_id": unit["id"],
                "unit_number": unit.get("unit_number"),
                "property_id": unit.get("property_id"),
                "property_name": (prop or {}).get("name"),
                "property_address": (prop or {}).get("address"),
                "city": city,
                "state": state,
                "rent_amount": rent_amount,
                "market_rent_estimate": market_rent,
                "delta": round(delta, 2),
                "delta_pct": round(delta_pct, 1),
                "is_underpriced": is_underpriced,
            }
        )

    return {
        "status": "success",
        "summary": {
            "underpriced_units": underpriced_units,
            "total_units_evaluated": len(unit_results),
            "estimated_monthly_uplift": round(total_uplift, 2),
        },
        "units": unit_results,
    }


