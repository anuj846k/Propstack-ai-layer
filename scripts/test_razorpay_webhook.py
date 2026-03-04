"""One-off script to simulate Razorpay payment.captured webhook for local testing."""
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


"""The script sends a fake Razorpay payment.captured webhook to your API. Your app will:
Verify the X-Razorpay-Signature header using RAZORPAY_WEBHOOK_SECRET from .env
Insert a row into payments (tenant Kartik, Rs 18,000, Feb 2026)
Create or update a row in rent_cycles for that tenancy + month with status = 'paid'
Respond with {"received": true} (HTTP 200)
"""
import hashlib
import hmac
import json
import os
import urllib.request


def main() -> None:
    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_001",
                        "amount": 1800000,
                        "status": "captured",
                        "currency": "INR",
                        "created_at": 1738000000,
                        "notes": {
                            "tenant_id": "494ef1f3-5211-4431-8589-9a130db02ad2",
                            "unit_id": "f2c2919c-9711-43c6-96a4-15a38172c473",
                            "period_month": "2026-02",
                        },
                    }
                }
            },
        }
    )

    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET") or "test_secret_123"
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        "http://localhost:8001/api/v1/payments/webhook/razorpay",
        data=body.encode(),
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        print("Status:", r.status)
        print("Response:", r.read().decode())


if __name__ == "__main__":
    main()
