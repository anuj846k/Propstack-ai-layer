# PropStack AI — Progress & What's Next

Use this file to track what's done, what to do next, and when to add things like BuiltInPlanner.

---

## ✅ Done

### Chat Feature (SSE)
- **Status**: Complete
- **What**: Streaming chat with Sara via `POST /api/v1/chat`
- **Backend**: FastAPI + ADK `run_async` with `StreamingMode.SSE`, partial-event streaming
- **Frontend**: Dashboard ChatSection → `TextStreamChatTransport` → `/api/chat` → FastAPI

---

## Rent Collection — Fully Implemented

### Data model (Supabase)

| Table | Purpose |
|-------|---------|
| `tenancies` | Links tenant + unit. No payment/paid state. |
| `units` | rent_amount, unit_number. |
| `call_logs` | Sara's rent collection calls (initiated, transcript, outcome). |
| `payments` | Payment ledger from Razorpay/Cashfree webhooks. Source of truth for rent paid. |
| `rent_cycles` | Per-tenancy per-month state (amount_due, amount_paid, status). |

### Backend Endpoints (FastAPI)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/chat` | SSE streaming chat with Sara (ADK runner) |
| `POST /api/v1/check-rent` | Manual rent status check |
| `POST /api/v1/initiate-call` | Initiate rent collection call (Twilio) |
| `POST /api/v1/rent/sweep` | Batch call sweep functionality |
| `POST /api/v1/calls/callback` | Call lifecycle callback |
| `POST /api/v1/calls/twilio/status` | Twilio status callback with signature validation |
| `POST /api/v1/calls/live/session/start` | Live session start |
| `POST /api/v1/calls/live/session/end` | Live session end |
| `POST /api/v1/calls/twilio/twiml/{call_id}` | TwiML for call prompts |
| `POST /api/v1/payments/webhook/razorpay` | Razorpay webhook (signature verification) |
| `POST /api/v1/payments/manual-cash` | Manual cash payment logging |

### Tools (ADK Agent)

| Tool | Status | Description |
|------|--------|-------------|
| `get_tenants_with_rent_status` | ✅ | Uses rent_cycles when present; paid tenants excluded from overdue. Falls back to date-based logic. |
| `get_tenant_payment_history` | ✅ | Reads from payments table (Razorpay/Cashfree webhook records). |
| `get_tenant_collection_history` | ✅ | Reads Sara's call history from call_logs. |
| `list_units_for_landlord` | ✅ | Lists all units with occupancy info. |
| `initiate_rent_collection_call` | ✅ | Fully wired to Twilio - creates call_logs + places real Twilio call |
| `save_call_result` | ✅ | Updates call_logs with transcript, outcome, duration |
| `create_notification` | ✅ | Inserts into notifications table |

### Services

| Service | Purpose |
|---------|---------|
| `call_policy_service.py` | Call policy enforcement: time window (9-20 IST), max 2 calls/day, tenant-landlord ownership validation |
| `live_session_service.py` | In-memory lifecycle manager for live voice sessions |
| `rent_cycle_service.py` | Rent cycle management: due dates, grace period, overdue detection, payment status updates |
| `twilio_voice.py` | Twilio integration: outbound calls, TwiML, audio transcoding (μ-law ↔ PCM16), webhook signature validation, trial mode guard |

### Agent

- **rent_collection_agent** - Updated with payment history + collection history context before making calls
- **voice_agent** - Voice-specific agent for live calls

### Config / Env

All Twilio settings:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_VOICE_FROM_NUMBER`
- `TWILIO_TRIAL_MODE`, `TWILIO_TRIAL_ALLOWED_TO_NUMBERS`
- `TWILIO_VALIDATE_WEBHOOK_SIGNATURE`, `TWILIO_CALL_TIMEOUT_SECONDS`
- `TWILIO_STREAM_SAMPLE_RATE_HZ`, `LIVE_INPUT_SAMPLE_RATE_HZ`, `LIVE_OUTPUT_SAMPLE_RATE_HZ`

Call policy settings:
- `CALL_WINDOW_START_HOUR`, `CALL_WINDOW_END_HOUR`, `MAX_CALL_ATTEMPTS_PER_TENANT_PER_DAY`

Live session settings:
- `LIVE_SESSION_MAX_SECONDS`, `ENABLE_PARTNER_TWILIO_LIVE`

Payment settings:
- `RAZORPAY_WEBHOOK_SECRET`

---

## Frontend

- Fixed `SpeechInput` component - now properly detects speech mode on client-side (SSR-safe)
- Fixed auto-submit - speech transcription fills text input, user presses Enter manually

---

## What's Remaining

- WhatsApp notification path remains deferred
- Production telephony quality depends on Twilio trial upgrade and verified numbers
- Live voice conversation with Gemini (real-time bidirectional) - optional enhancement

---

## 📋 What to Tell Next

*(Add instructions here when someone asks "what should we build next" or "what's the plan")*

- Rent collection is fully implemented end-to-end
- Next: Add more agent capabilities (maintenance, vendor management, etc.)

---

## 🔧 When to Add BuiltInPlanner

**BuiltInPlanner** = agent plans multi-step reasoning before acting. Add it when:

| Scenario | Add BuiltInPlanner? |
|----------|---------------------|
| Simple tool flows (check rent → call) | No — instructions already guide flow |
| Multi-step decision workflows | Yes |
| Maintenance planning, approval chains | Yes |
| Complex branching (e.g. "analyze then decide buy vs lease") | Yes |

*(Update this section as new agents or flows are added.)*

---

## ➕ Add After Completing Features

*(When you say "now add something" after a feature is done, it gets added here.)*

- *Nothing yet*

---

## ✅ Latest Changes

<span style="color:red"><strong>Last updated:</strong> 2026-03-04</span>

### Completed This Round

**Backend (FastAPI)**
- Added all call lifecycle endpoints (initiate-call, twilio/status, twiml/{call_id}, live/session/start, live/session/end, callback, sweep)
- Added payment endpoints (razorpay webhook, manual-cash)
- Added call_policy_service with time window and attempt limiting
- Added live_session_service for managing active voice sessions
- Added rent_cycle_service for payment status management
- Added twilio_voice integration with full audio transcoding and trial mode guard

**Tools + Agent**
- All rent collection tools fully wired: initiate_rent_collection_call now creates real Twilio calls
- Agent updated with payment + collection history context

**Frontend**
- Fixed SpeechInput SSR issue (client-side detection)
- Fixed auto-submit behavior (user presses Enter manually)

**Config**
- Added all Twilio, call policy, and live session settings to config.py

---

## Wiring Diagram (Complete)

```
Payment flow:
  Razorpay webhook → POST /api/v1/payments/webhook/razorpay → INSERT payments
  Webhook also updates rent_cycles (status → paid, amount_paid, paid_at)

Rent status:
  rent_cycles (when present) → paid tenants not overdue
  Fallback: tenancies + date math for tenants without rent_cycles

Payment vs collection history:
  get_tenant_payment_history → payments (actual rent payments)
  get_tenant_collection_history → call_logs (Sara's collection calls)

Call flow (fully wired):
  1. initiate_rent_collection_call → INSERT call_logs (outcome=initiated)
  2. Twilio places real call
  3. Twilio status callback → UPDATE call_logs
  4. save_call_result → UPDATE call_logs (transcript, outcome, duration)

Call policy enforcement:
  - Time window: 9am-8pm IST
  - Max 2 attempts per tenant per day
  - Tenant-landlord ownership validated
  - Trial mode: only verified numbers can be called
```
