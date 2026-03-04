"""Replay a signed Razorpay payment.captured webhook for local demos."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://localhost:8001", help="FastAPI base URL")
    parser.add_argument("--tenant-id", required=True, help="Tenant UUID")
    parser.add_argument("--unit-id", required=True, help="Unit UUID")
    parser.add_argument("--period-month", required=True, help="Period month YYYY-MM")
    parser.add_argument("--amount", type=float, required=True, help="Amount in INR")
    parser.add_argument("--payment-id", default=None, help="Razorpay payment ID override")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise SystemExit("RAZORPAY_WEBHOOK_SECRET is required in environment/.env")

    payment_id = args.payment_id or f"pay_demo_{int(time.time())}"
    payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": int(round(args.amount * 100)),
                    "status": "captured",
                    "currency": "INR",
                    "created_at": int(time.time()),
                    "notes": {
                        "tenant_id": args.tenant_id,
                        "unit_id": args.unit_id,
                        "period_month": args.period_month,
                    },
                }
            }
        },
    }

    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        f"{args.api_base.rstrip('/')}/api/v1/payments/webhook/razorpay",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
        },
    )

    with urllib.request.urlopen(req) as resp:
        response_text = resp.read().decode("utf-8")

    print(f"[{datetime.now().isoformat()}] Razorpay webhook sent")
    print(f"payment_id={payment_id} amount={args.amount} period_month={args.period_month}")
    print(response_text)


if __name__ == "__main__":
    main()
