import json

from fastapi.testclient import TestClient

from app.main import app
from app.routers import payments


client = TestClient(app)


def test_razorpay_webhook_rejects_invalid_signature(monkeypatch) -> None:
    monkeypatch.setattr(payments.settings, "razorpay_webhook_secret", "secret")

    body = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_1",
                    "amount": 10000,
                    "status": "captured",
                    "currency": "INR",
                    "notes": {
                        "tenant_id": "t1",
                        "unit_id": "u1",
                        "period_month": "2026-03",
                    },
                }
            }
        },
    }
    response = client.post(
        "/api/v1/payments/webhook/razorpay",
        headers={"X-Razorpay-Signature": "bad"},
        content=json.dumps(body),
    )

    assert response.status_code == 401


def test_manual_cash_endpoint_success(monkeypatch) -> None:
    monkeypatch.setattr(
        payments,
        "_record_payment_and_update_cycle",
        lambda **kwargs: {
            "status": "success",
            "message": "Payment recorded",
            "error_message": None,
            "data": {"payment_id": "p1", "cycle_status": "paid"},
        },
    )

    response = client.post(
        "/api/v1/payments/manual-cash",
        json={
            "landlord_id": "l1",
            "tenant_id": "t1",
            "unit_id": "u1",
            "amount": 10000,
            "paid_at": "2026-03-07T10:00:00Z",
            "period_month": "2026-03",
            "note": "Paid at office",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["payment_id"] == "p1"


def test_manual_cash_endpoint_landlord_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        payments,
        "_record_payment_and_update_cycle",
        lambda **kwargs: {
            "status": "error",
            "message": "Landlord mismatch",
            "error_message": "Requested landlord does not own tenant/unit",
            "data": None,
        },
    )

    response = client.post(
        "/api/v1/payments/manual-cash",
        json={
            "landlord_id": "l1",
            "tenant_id": "t1",
            "unit_id": "u1",
            "amount": 10000,
            "paid_at": "2026-03-07T10:00:00Z",
            "period_month": "2026-03",
            "note": "Paid at office",
        },
    )

    assert response.status_code == 403
