# PropStack AI Backend — Review

Review covers: rent calls, maintenance tickets, payments, analytics, auth, and integration points. No code changes—assessment only.

---

## 1. Overview

| Area | Status | Notes |
|------|--------|--------|
| **Rent calls** | ✅ End-to-end | Initiate → Twilio → status/transcript → AI analysis stored |
| **Maintenance tickets** | ✅ End-to-end | WhatsApp triage → ticket + vendor dispatch → Twilio voice |
| **Payments** | ✅ | Razorpay webhook + manual cash; rent_cycle updates |
| **Analytics** | ✅ | Vacancy cost + rent intelligence (landlord-scoped) |
| **Auth** | ✅ | Internal secret + x-landlord-id; no JWT in FastAPI |

---

## 2. Rent calls

### Flow

1. **Initiate**
   - **Properties**: `POST /api/v1/tenants/{tenant_id}/call` (used by frontend).
   - **Rent**: `POST /api/v1/initiate-call` (body: landlord_id, tenant_id; used by scheduler/other).
   - Both: resolve landlord → get tenants with rent status → ensure tenant overdue → (rent only: call policy, limits) → `initiate_rent_collection_call()`.

2. **call_tools**
   - `_insert_call_log`: inserts `call_logs` (tenant_id, landlord_id, initiated_by, language_used, outcome=initiated). No summary at insert; summary set after Twilio create.
   - Twilio: `create_outbound_call(to_number, call_id, twiml_url(call_id), status_callback_url(call_id))`.
   - On success: `_update_call_log_summary(..., outcome="initiated")`; optionally `live_session_service.start_session(...)`.
   - On exception: update summary with error, outcome=failed, return envelope with `error_message`.

3. **Twilio callbacks**
   - **TwiML**: `POST /api/v1/calls/twilio/twiml/{call_id}` → `build_twiml_bootstrap_response(call_id)` (Connect Stream to WebSocket or record).
   - **Status**: `POST /api/v1/calls/twilio/status?call_id=...` → validate signature, load call row, update outcome/duration_seconds/transcript (if no ADK transcript), terminal → end live session + landlord notification.
   - **Transcription**: `POST /api/v1/calls/twilio/transcription?call_id=...` → save transcript to `call_logs` when `call_id` in query (Twilio sends CallSid only).
   - **Recording complete**: `POST /api/v1/calls/twilio/recording-complete` → **bug**: uses `.eq("id", call_sid)` but `call_sid` is Twilio’s CallSid, not `call_logs.id`. Update never matches; recording info is not persisted.

4. **Live voice**
   - WebSocket `GET /api/v1/calls/live/twilio/media/{call_id}`: `_find_call_log(call_id)` → start session → ADK `voice_runner.run_live(...)` with `LiveRequestQueue`, Twilio μ-law ↔ PCM16 at 8k/16k/24k, transcript collector, `save_call_result` on close.

5. **Dashboard / analysis**
   - **Calls**: `GET /api/v1/calls` (list by landlord), `GET /api/v1/calls/{call_id}` (detail with ai_summary, promise_amount, promise_date, sentiment).
   - **Analysis**: `POST /api/v1/calls/{call_id}/analysis` — return from DB if `ai_summary` set; else `generate_call_analysis(transcript, event_data)` (Gemini API key), update `call_logs`, return.

### Call initiation response

- **Properties** router returns its own `CallInitiationResponse` (call_id, status, message, provider_status, error_message). Frontend uses this; `error_message` is now surfaced.
- **Rent** router uses `app.schemas.rent.CallInitiationResponse` (adds provider_call_sid, live_session_enabled, live_session_id). Used by `/initiate-call` (scheduler, etc.).

### Gaps / fixes

- **Recording-complete**: Resolve `call_id` from Twilio `CallSid` (e.g. store `provider_call_sid` on `call_logs` or look up by provider_metadata), then update the correct row instead of `.eq("id", call_sid)`.

---

## 3. Maintenance tickets

### Flow

1. **Creation**
   - **WhatsApp**: `POST /api/v1/maintenance/twilio-whatsapp-incoming` → validate Twilio signature → resolve tenant by phone → run **triage_agent** with user message (+ image if MediaUrl0) → agent can call `create_maintenance_ticket` tool.
   - **create_maintenance_ticket** (maintenance_tools): resolve unit from tenancy → insert `maintenance_tickets` → on success calls `_dispatch_vendor_for_ticket(ticket_id, issue_category)`.

2. **Vendor dispatch**
   - `_dispatch_vendor_for_ticket`: find next vendor by specialty, create `vendor_dispatch_logs`, trigger Twilio outbound via `maintenance_twilio` (twiml URL, status URL).
   - Maintenance Twilio: `POST /api/v1/maintenance/calls/twilio/twiml/{dispatch_log_id}`, status callback, WebSocket for live vendor call (voice_dispatch_agent).

3. **API**
   - **List**: `GET /api/v1/maintenance/tickets` — landlord-scoped via properties → units → tickets; filters: status, tenant_id, unit_id; pagination; joins units, tenants, vendors, ticket_images, latest dispatch status.
   - **Detail**: `GET /api/v1/maintenance/tickets/{ticket_id}` — same scope + dispatch_logs.
   - **Image proxy**: `GET /api/v1/maintenance/tickets/{ticket_id}/image` and `.../images/{ticket_image_id}` — fetch from Twilio media URL with auth, return bytes.

4. **Trigger vendor call (manual)**
   - `POST /api/v1/maintenance/trigger-vendor-call` — body includes ticket/dispatch info; calls `_dispatch_vendor_for_ticket` again path.

### Consistency

- Tickets are always created via triage agent (WhatsApp) or equivalent; list/detail enforce landlord ownership via unit → property → landlord_id.

---

## 4. Payments

- **Razorpay**: `POST /api/v1/payments/webhook/razorpay` — verify signature, parse payload, `_record_payment_and_update_cycle` (find active tenancy, landlord match, insert payment, update rent_cycle).
- **Manual cash**: `POST /api/v1/payments/manual-cash` — landlord_id in body; same record + update cycle; landlord must own tenant/unit.

Idempotency: payments keyed by `provider_payment_id`; duplicate webhook no-op.

---

## 5. Analytics

- **Vacancy cost**: `GET /api/v1/analytics/vacancy-cost` — landlord-scoped; `get_vacancy_cost_for_landlord` (tools).
- **Rent intelligence**: `GET /api/v1/analytics/rent-intelligence` — landlord-scoped; `analyze_rent_intelligence_for_landlord` (market rent, etc.).

Both require internal secret + x-landlord-id.

---

## 6. Auth and dependencies

- **Internal**: `verify_internal_request` (Header `x-internal-secret`) used by all landlord-scoped routes. Next.js verifies Supabase JWT and sends `x-landlord-id` + internal secret.
- **Supabase**: Single server-side client (`get_supabase()`) with service key.
- **Twilio**: Signature validation on status/WhatsApp; optional via `twilio_validate_webhook_signature`.

---

## 7. Summary

| Item | Status |
|------|--------|
| Rent: initiate, Twilio lifecycle, live voice, transcript, AI analysis stored | ✅ |
| Rent: recording-complete handler | ⚠️ Bug (call_id vs CallSid) |
| Maintenance: WhatsApp triage, ticket create, vendor dispatch, list/detail, images | ✅ |
| Payments: Razorpay + manual, rent_cycle | ✅ |
| Analytics: vacancy + rent intel | ✅ |
| Auth: internal secret + landlord id | ✅ |

**Recommended fix**: In `twilio_recording_complete`, resolve internal `call_id` from Twilio `CallSid` (e.g. store `provider_call_sid` on `call_logs` and query by it, or search provider_metadata), then update that row with recording URL and duration.
