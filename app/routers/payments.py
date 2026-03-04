"""Payment ingestion APIs: webhooks and manual cash logging."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request , Depends

from app.config import settings
from app.dependencies import get_supabase, verify_internal_request
from app.schemas.rent import ManualCashPaymentRequest, ManualCashPaymentResponse
from app.services import rent_cycle_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _normalize_amount(raw: Any) -> float:
    if isinstance(raw, int):
        # Razorpay amounts are often in paise.
        return raw / 100
    return float(raw or 0)


def _parse_period_month(period_month: str) -> None:
    rent_cycle_service.build_rent_timeline(period_month)


def _find_active_tenancy(sb, tenant_id: str, unit_id: str) -> dict | None:
    result = (
        sb.table("tenancies")
        .select("id, units(rent_amount, properties(landlord_id))")
        .eq("tenant_id", tenant_id)
        .eq("unit_id", unit_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]


def _insert_payment_if_new(sb, payment_row: dict[str, Any]) -> tuple[bool, str | None]:
    existing = (
        sb.table("payments")
        .select("id")
        .eq("provider_payment_id", payment_row["provider_payment_id"])
        .limit(1)
        .execute()
    )
    if existing.data:
        return False, existing.data[0]["id"]

    inserted = sb.table("payments").insert(payment_row).execute()
    payment_id = (inserted.data or [{}])[0].get("id")
    return True, payment_id


def _record_payment_and_update_cycle(
    *,
    provider: str,
    provider_payment_id: str,
    tenant_id: str,
    unit_id: str,
    period_month: str,
    amount: float,
    currency: str,
    paid_at: datetime,
    raw_payload: dict[str, Any],
    expected_landlord_id: str | None = None,
) -> dict:
    sb = get_supabase()

    tenancy = _find_active_tenancy(sb, tenant_id, unit_id)
    if not tenancy:
        return {
            "status": "error",
            "message": "Active tenancy not found",
            "error_message": "No active tenancy for tenant/unit",
            "data": None,
        }

    unit = tenancy.get("units") or {}
    prop = unit.get("properties") or {}
    landlord_id = prop.get("landlord_id")
    if expected_landlord_id and landlord_id != expected_landlord_id:
        return {
            "status": "error",
            "message": "Landlord mismatch for this tenancy",
            "error_message": "Requested landlord does not own tenant/unit",
            "data": None,
        }

    payment_row = {
        "tenant_id": tenant_id,
        "unit_id": unit_id,
        "landlord_id": landlord_id,
        "tenancy_id": tenancy["id"],
        "amount": amount,
        "currency": currency or "INR",
        "paid_at": paid_at.astimezone(timezone.utc).isoformat(),
        "provider": provider,
        "provider_payment_id": provider_payment_id,
        "status": "succeeded",
        "period_month": period_month,
        "raw_payload": raw_payload,
    }

    inserted, payment_id = _insert_payment_if_new(sb, payment_row)
    cycle_status = None
    if inserted:
        cycle_update = rent_cycle_service.update_cycle_on_payment(
            sb,
            tenant_id=tenant_id,
            unit_id=unit_id,
            amount=amount,
            period_month=period_month,
            paid_at=paid_at,
        )
        cycle_data = cycle_update.get("data") or {}
        cycle_status = cycle_data.get("cycle_status")

    return {
        "status": "success",
        "message": "Payment recorded" if inserted else "Duplicate payment ignored",
        "error_message": None,
        "data": {
            "inserted": inserted,
            "payment_id": payment_id,
            "cycle_status": cycle_status,
            "landlord_id": landlord_id,
        },
    }


@router.post("/webhook/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(..., alias="X-Razorpay-Signature"),
) -> dict:
    """Receive Razorpay webhook, verify signature, idempotently persist payment."""
    if not settings.razorpay_webhook_secret:
        raise HTTPException(500, "Razorpay webhook not configured")

    body = await request.body()
    if not _verify_signature(body, x_razorpay_signature, settings.razorpay_webhook_secret):
        raise HTTPException(401, "Invalid signature")

    payload = json.loads(body)
    if payload.get("event") != "payment.captured":
        return {"received": True, "processed": False}

    payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
    payment_id = payment.get("id")
    if not payment_id:
        return {"received": True, "processed": False}

    status = str(payment.get("status") or "").lower()
    if status != "captured":
        return {"received": True, "processed": False}

    notes = payment.get("notes") or {}
    tenant_id = notes.get("tenant_id")
    unit_id = notes.get("unit_id")
    period_month = notes.get("period_month") or notes.get("period")
    if not all([tenant_id, unit_id, period_month]):
        logger.warning("Razorpay webhook missing notes tenant_id/unit_id/period_month")
        return {"received": True, "processed": False}

    try:
        _parse_period_month(period_month)
    except ValueError:
        logger.warning("Razorpay webhook invalid period_month=%s", period_month)
        return {"received": True, "processed": False}

    created_at_raw = payment.get("created_at")
    paid_at = datetime.now(timezone.utc)
    if isinstance(created_at_raw, (int, float)):
        paid_at = datetime.fromtimestamp(created_at_raw, tz=timezone.utc)

    result = _record_payment_and_update_cycle(
        provider="razorpay",
        provider_payment_id=f"razorpay_{payment_id}",
        tenant_id=tenant_id,
        unit_id=unit_id,
        period_month=period_month,
        amount=_normalize_amount(payment.get("amount")),
        currency=str(payment.get("currency") or "INR"),
        paid_at=paid_at,
        raw_payload=payment,
    )

    if result["status"] == "error":
        logger.warning("Razorpay payment skipped: %s", result.get("error_message"))
        return {"received": True, "processed": False}

    data = result.get("data") or {}
    logger.info(
        "Razorpay payment processed provider_id=%s inserted=%s tenant=%s month=%s",
        f"razorpay_{payment_id}",
        data.get("inserted"),
        tenant_id,
        period_month,
    )
    return {"received": True, "processed": bool(data.get("inserted"))}


@router.post("/manual-cash", response_model=ManualCashPaymentResponse)
async def log_manual_cash_payment(
    body: ManualCashPaymentRequest,
    x_internal_secret: str = Depends(verify_internal_request),
) -> ManualCashPaymentResponse:
    """Landlord-authenticated manual cash logging path for demo and real cash settlements."""
    try:
        _parse_period_month(body.period_month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    provider_fingerprint = (
        f"{body.landlord_id}|{body.tenant_id}|{body.unit_id}|{body.period_month}|"
        f"{body.amount:.2f}|{body.paid_at.isoformat()}|{body.note}"
    )
    provider_payment_id = f"cash_manual_{hashlib.sha256(provider_fingerprint.encode()).hexdigest()[:20]}"

    result = _record_payment_and_update_cycle(
        provider="cash_manual",
        provider_payment_id=provider_payment_id,
        tenant_id=body.tenant_id,
        unit_id=body.unit_id,
        period_month=body.period_month,
        amount=float(body.amount),
        currency="INR",
        paid_at=body.paid_at,
        raw_payload={
            "note": body.note,
            "proof_url": body.proof_url,
            "source": "manual_cash",
        },
        expected_landlord_id=body.landlord_id,
    )

    if result["status"] == "error":
        error_message = result.get("error_message") or "Manual cash logging failed"
        status_code = 403 if "landlord" in error_message.lower() else 400
        raise HTTPException(status_code=status_code, detail=error_message)

    data = result.get("data") or {}
    return ManualCashPaymentResponse(
        status="success",
        message=result.get("message") or "Manual cash payment logged",
        payment_id=data.get("payment_id"),
        cycle_status=data.get("cycle_status"),
        period_month=body.period_month,
    )
