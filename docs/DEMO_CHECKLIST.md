# Rent Collection Demo Checklist

## Preconditions
- FastAPI server running at `http://localhost:8001`
- `.env` has `RAZORPAY_WEBHOOK_SECRET`, `INTERNAL_SCHEDULER_TOKEN`
- Tenant/unit IDs exported for the demo script

## Online Payment Flow
1. Run kickoff sweep: `POST /api/v1/rent/sweep` with mode `kickoff`.
2. Confirm overdue tenant gets a queued call action.
3. Replay partial payment with `scripts/simulate_razorpay_webhook.py`.
4. Run daily sweep; tenant should still be followed up if outstanding exists.
5. Replay full payment webhook.
6. Run daily sweep again; fully-paid tenant should be skipped.

## Cash Flow
1. Call `POST /api/v1/payments/manual-cash` with landlord/tenant/unit details.
2. Verify response returns success and cycle status update.
3. Run sweep and confirm tenant is removed when fully settled.

## Callback Flow
1. Trigger `POST /api/v1/calls/callback` with call outcome payload.
2. Verify `call_logs` update and landlord notification creation.
