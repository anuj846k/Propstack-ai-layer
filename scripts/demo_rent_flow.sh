#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8001}"
INTERNAL_TOKEN="${INTERNAL_SCHEDULER_TOKEN:-}"
TENANT_ID="${TENANT_ID:-}"
UNIT_ID="${UNIT_ID:-}"
LANDLORD_ID="${LANDLORD_ID:-}"
PERIOD_MONTH="${PERIOD_MONTH:-$(date +%Y-%m)}"
PARTIAL_AMOUNT="${PARTIAL_AMOUNT:-9000}"
FULL_REMAINING_AMOUNT="${FULL_REMAINING_AMOUNT:-9000}"

if [[ -z "$INTERNAL_TOKEN" || -z "$TENANT_ID" || -z "$UNIT_ID" || -z "$LANDLORD_ID" ]]; then
  echo "Set INTERNAL_SCHEDULER_TOKEN, TENANT_ID, UNIT_ID, LANDLORD_ID before running."
  exit 1
fi

echo "1) Kickoff sweep"
curl -sS -X POST "$API_BASE/api/v1/rent/sweep" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d "{\"mode\":\"kickoff\",\"month\":\"$PERIOD_MONTH\",\"dry_run\":false}"

echo "2) Simulate partial online payment"
python scripts/simulate_razorpay_webhook.py \
  --api-base "$API_BASE" \
  --tenant-id "$TENANT_ID" \
  --unit-id "$UNIT_ID" \
  --period-month "$PERIOD_MONTH" \
  --amount "$PARTIAL_AMOUNT"

echo "3) Daily sweep (should still call/follow-up if outstanding remains)"
curl -sS -X POST "$API_BASE/api/v1/rent/sweep" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d "{\"mode\":\"daily\",\"month\":\"$PERIOD_MONTH\",\"dry_run\":false}"

echo "4) Simulate final online payment"
python scripts/simulate_razorpay_webhook.py \
  --api-base "$API_BASE" \
  --tenant-id "$TENANT_ID" \
  --unit-id "$UNIT_ID" \
  --period-month "$PERIOD_MONTH" \
  --amount "$FULL_REMAINING_AMOUNT"

echo "5) Daily sweep (should skip fully-paid cycle)"
curl -sS -X POST "$API_BASE/api/v1/rent/sweep" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -d "{\"mode\":\"daily\",\"month\":\"$PERIOD_MONTH\",\"dry_run\":false}"

echo "6) Cash path (manual cash entry) example"
curl -sS -X POST "$API_BASE/api/v1/payments/manual-cash" \
  -H "Content-Type: application/json" \
  -d "{\"landlord_id\":\"$LANDLORD_ID\",\"tenant_id\":\"$TENANT_ID\",\"unit_id\":\"$UNIT_ID\",\"amount\":5000,\"paid_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"period_month\":\"$PERIOD_MONTH\",\"note\":\"Demo manual cash settlement\"}"
