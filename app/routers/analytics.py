from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.dependencies import verify_internal_request
from app.tools.rent_intel_tools import (
    analyze_rent_intelligence_for_landlord,
    get_vacancy_cost_for_landlord,
)


router = APIRouter()


def get_landlord_id_from_request(
    x_landlord_id: str | None = Header(None, alias="x-landlord-id"),
    _x_internal_secret: str = Depends(verify_internal_request),
) -> str:
    """
    Get landlord_id from request headers after verifying internal secret.
    The x-landlord-id header is set by Next.js after JWT verification.
    """
    if not x_landlord_id:
        raise HTTPException(
            status_code=400,
            detail="Missing x-landlord-id header",
        )
    return x_landlord_id


class VacancyUnitItem(BaseModel):
    unit_id: str
    unit_number: str | None = None
    property_id: str | None = None
    property_name: str | None = None
    property_address: str | None = None
    rent_amount: float
    days_vacant: int
    vacancy_cost: float


class VacancyCostSummary(BaseModel):
    total_vacant_units: int
    total_days_vacant: int
    total_vacancy_cost: float
    as_of_date: str
    month_start: str
    units: List[VacancyUnitItem]


@router.get("/vacancy-cost", response_model=VacancyCostSummary)
async def get_vacancy_cost(
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> VacancyCostSummary:
    """
    Returns vacancy cost analytics for the authenticated landlord.
    """
    result = await get_vacancy_cost_for_landlord(landlord_id=landlord_id)
    if result.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail=result.get("error_message") or "Failed to calculate vacancy cost",
        )

    summary = result.get("summary") or {}
    units = result.get("units") or []

    try:
        return VacancyCostSummary(
            total_vacant_units=int(summary.get("total_vacant_units", 0)),
            total_days_vacant=int(summary.get("total_days_vacant", 0)),
            total_vacancy_cost=float(summary.get("total_vacancy_cost", 0.0)),
            as_of_date=str(summary.get("as_of_date") or ""),
            month_start=str(summary.get("month_start") or ""),
            units=[VacancyUnitItem(**u) for u in units],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to serialize vacancy cost response: {exc}",
        ) from exc


class RentIntelUnitItem(BaseModel):
    unit_id: str
    unit_number: str | None = None
    property_id: str | None = None
    property_name: str | None = None
    property_address: str | None = None
    city: str | None = None
    state: str | None = None
    rent_amount: float
    market_rent_estimate: float
    delta: float
    delta_pct: float
    is_underpriced: bool


class RentIntelSummary(BaseModel):
    underpriced_units: int
    total_units_evaluated: int
    estimated_monthly_uplift: float
    units: List[RentIntelUnitItem]


@router.get("/rent-intelligence", response_model=RentIntelSummary)
async def get_rent_intelligence(
    landlord_id: str = Depends(get_landlord_id_from_request),
) -> RentIntelSummary:
    """
    Returns rent intelligence analysis for the authenticated landlord.
    """
    result = await analyze_rent_intelligence_for_landlord(landlord_id=landlord_id)
    if result.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail=result.get("error_message") or "Failed to run rent intelligence",
        )

    summary = result.get("summary") or {}
    units = result.get("units") or []

    try:
        return RentIntelSummary(
            underpriced_units=int(summary.get("underpriced_units", 0)),
            total_units_evaluated=int(summary.get("total_units_evaluated", 0)),
            estimated_monthly_uplift=float(
                summary.get("estimated_monthly_uplift", 0.0)
            ),
            units=[RentIntelUnitItem(**u) for u in units],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to serialize rent intelligence response: {exc}",
        ) from exc


