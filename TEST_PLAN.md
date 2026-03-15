# PropStack End-to-End Test Plan

This guide shows how to exercise **all major features and tools** in PropStack, starting from a **fresh landlord** with no properties. It’s written so a judge (or developer) can follow it step by step.

---

## 0. Prerequisites

- **Frontend (Next.js)** deployed and accessible at:

  - `https://propstack.live` (or `https://www.propstack.live`)

- **Backend (FastAPI + ADK)** deployed on Cloud Run:

  - `https://propstack-ai-858743429173.asia-south1.run.app`

- **Supabase** project configured and reachable with the env values in:

  - `propstack-ai/.env`
  - `propstack-frontend/.env`

- **Twilio**:

  - WhatsApp sandbox or WhatsApp‑enabled number configured with webhook:

    - `https://propstack-ai-858743429173.asia-south1.run.app/api/v1/maintenance/twilio-whatsapp-incoming`

  - Voice number configured and used by the backend (calls are triggered from the app; you do not need to manually hit voice webhooks for this test).

---

## 1. Sign Up as a New Landlord (No Existing Data)

**Goal:** Start from a clean state: landlord with no properties or units.

1. Open the frontend:

   - `https://propstack.live`

2. Go to **Sign Up**:

   - Click “Try for free” or “Sign up”.

3. Use **Google Sign‑In**:

   - Click “Continue with Google”.
   - Approve OAuth and return to the app.
   - You should end up in `/dashboard` (or equivalent landing page for logged‑in landlords).

4. Confirm **empty state**:

   - The dashboard should show:
     - No properties
     - No tenants
     - No calls
     - No maintenance tickets

---

## 2. Use Management Agent to Create Portfolio

All of these steps are done via **Sara’s chat** using the **Management Agent** (`management_agent`).

Open the **AI chat panel** (Ask Sara) in the dashboard.

### 2.1. Add a New Property

**Message to send:**

> Add a new property called "Sharma Properties" in Noida, Uttar Pradesh.

**Expected behavior:**

- The management agent calls the `add_property` tool.
- It confirms:

  > I’ve created the property “Sharma Properties” in Noida, Uttar Pradesh.

### 2.2. Add Units Under the Property

**Message to send:**

> Add 6 units under Sharma Properties with these monthly rents: F102 ₹9,000, G002 ₹10,000, S202 ₹8,000, S201 ₹8,000, F101 ₹9,000, and G001 ₹10,000.

**Expected behavior:**

- The agent uses `list_properties` to resolve the property.
- Then calls `add_unit` repeatedly.
- Confirms each unit, e.g.:

  > I’ve added unit F102 (₹9,000/mo), G002 (₹10,000/mo), …, G001 (₹10,000/mo) under Sharma Properties.

### 2.3. Add a Tenant + Tenancy for One Unit

**Message to send:**

> Add a tenant Anuj Kumar to unit G001 in Sharma Properties. Rent is ₹10,000 per month. The lease is from 2026-03-01 to 2027-02-28, deposit ₹20,000, rent is due on the 1st.

**Expected behavior:**

- The agent:
  - Uses `list_properties` / `list_units` to find G001.
  - Calls `add_tenant_and_tenancy`.
- Response:

  > I’ve added Anuj Kumar as a tenant in unit G001 with a tenancy from 2026‑03‑01 to 2027‑02‑28 and rent ₹10,000/mo.

### 2.4. Verify Portfolio via UI

- In the **Properties / Units** section:
  - Confirm `Sharma Properties` exists.
  - Confirm all 6 units are present.
  - Confirm G001 shows as **occupied** by Anuj Kumar, the others as **vacant**.

---

## 3. Rent Collection Flow (Rent Agent)

Now switch to **rent collection scenarios** using the **Rent Agent** (`rent_agent`).

Open **Ask Sara** (same chat, but you now talk about rent).

### 3.1. Check Overdue Tenants

**Message to send:**

> Who owes me rent right now?

**Expected behavior:**

- Sara calls `get_tenants_with_rent_status`.
- Since this is a new setup and dates may not yet mark anyone overdue, she may respond with something like:

  > Currently no tenants are marked overdue.  
  > Or: Anuj Kumar in unit G001 is … (if configured as overdue based on dates).

This verifies `get_tenants_with_rent_status` is working.

### 3.2. Initiate an Outbound Rent Collection Call

**Message to send:**

> Call Anuj Kumar about rent.

**Expected behavior:**

- Sara:
  - Uses `find_tenant_by_name` to find Anuj Kumar.
  - Uses `initiate_rent_collection_call` to start a Twilio call to his phone.
- Response example:

  > I’ve initiated a rent collection call to Anuj Kumar (G001). You’ll see the call status in your dashboard shortly.

In the **Calls / AI Agents** section, you should see a new **call log** appear, including live or completed status.

### 3.3. Check Call Status (“It’s Been So Long”)

After starting a call, ask for an update.

**Message to send:**

> Can you check, it's been so much time for the call to Anuj Kumar?

**Expected behavior:**

- Sara calls `get_call_status` (possibly with a preceding `find_tenant_by_name`).
- If the call is still ongoing:

  > The call to Anuj Kumar is still in progress.

- If it has finished:

  > The call to Anuj Kumar has finished. The outcome was completed and it lasted about N seconds.

This confirms `get_call_status` and the call policy/guardrails.

---

## 4. Manual Payment & Promised Date

### 4.1. Log a Promised Payment Date

**Message to send:**

> Anuj Kumar has promised to pay on 2026-03-20. Please log this.

**Expected behavior:**

- Sara calls `log_promised_payment_date`.
- Responds:

  > I’ve logged 2026‑03‑20 as the promised payment date for Anuj Kumar.

### 4.2. Log a Manual Payment

**Message to send:**

> I received ₹10,000 cash from Anuj Kumar for March rent. Log this manually.

**Expected behavior:**

- Sara calls `log_manual_payment` with tenant_id and amount.
- Responds:

  > I’ve logged a ₹10,000 manual payment for Anuj Kumar for the current rent cycle.

---

## 5. Maintenance via WhatsApp (Vision + Triage Agent)

**Goal:** Test the WhatsApp->vision->ticket pipeline for tenants.

From the tenant’s WhatsApp number (the one saved in Supabase for Anuj Kumar):

1. Send a **WhatsApp message** to the Twilio WhatsApp number:

   > My kitchen sink is leaking badly.

2. Attach a **photo** showing a leak (any pipe/leak image).

**What happens in the backend:**

- Twilio sends the webhook to:

  - `POST /api/v1/maintenance/twilio-whatsapp-incoming`

- The backend:
  - Validates Twilio signature.
  - Looks up the tenant by `From` number.
  - Downloads the image.
  - Calls the **maintenance `triage_agent`** with:
    - Text + image (`Part.from_bytes`) and system hints.
  - The agent analyzes severity and calls `create_maintenance_ticket`.

**Expected behavior in UI:**

- On landlord dashboard, open **Maintenance → Tickets**.
- You should see a new ticket like:

  - Title: “Sink leak in kitchen” (or similar)
  - Category: plumbing
  - Severity: High
  - Status: Open
  - Image thumbnail and image proxy URL.

---

## 6. Analytics: Vacancy Cost

**Goal:** Show dynamic analytics for vacancies (vacancy cost and units).

1. On the dashboard, view **Analytics / Vacancy Cost**.

2. Confirm:

   - **Total vacant units**: should equal the number of units without active tenancies (5 in this scenario).
   - **Vacancy impact this month (₹)**: a number corresponding to:

     \[
     \sum_{\text{vacant units}} \text{days\_vacant}_i \times \frac{\text{rent\_amount}_i}{30}
     \]

**Optional direct API test:**

- `GET https://propstack-ai-858743429173.asia-south1.run.app/api/v1/analytics/vacancy-cost`  
  with headers:

  - `x-internal-secret: <INTERNAL_API_SECRET>`
  - `x-landlord-id: <your landlord UUID>`

You should see:

- `summary.total_vacant_units`
- `summary.total_vacancy_cost`
- `units[]` with per‑unit `days_vacant` and `vacancy_cost`.

---

## 7. Analytics: Rent Intelligence (Google Search Grounded)

**Goal:** Test the **rent intelligence** flow and the optimized `analyze_rent_intelligence_for_landlord` tool.

### 7.1. Ask the Management Agent

In Ask Sara, switch to portfolio/management questions.

**Message to send:**

> Analyze my rent prices against the market for Sharma Properties in Noida.

**What happens:**

- `management_agent` calls `analyze_rent_intelligence_for_landlord`.
- This:
  - Fetches your units from Supabase.
  - For a sample of units (default 5), calls `estimate_market_rent_for_unit`.
  - Internally, `_market_rent_agent` uses the ADK `google_search` tool to pull current listings.
  - Aggregates:
    - `underpriced_units`
    - `total_units_evaluated`
    - `estimated_monthly_uplift`
    - per‑unit deltas.

**Expected behavior:**

- Sara responds with a concise summary, e.g.:

  > I’ve analyzed your units using rent intelligence.  
  > **Total units evaluated**: 6  
  > **Underpriced units**: 6  
  > **Estimated monthly uplift**: ₹14,000 per month  
  > Example: Unit F102 (₹9,000) – market estimate ₹12,000 → underpriced by ₹3,000 (33.3%).

This demonstrates:

- ADK multi‑agent + tool orchestration
- Google Search grounding
- Business insight for landlords.

---

## 8. Conversations API & Session List

**Goal:** Show the multi‑session chat capability via `/api/conversations` and `/api/v1/chat/sessions`.

### 8.1. List Existing Conversations (Frontend API)

Call the Next.js route:

- `GET https://propstack.live/api/conversations` (authenticated as landlord)

It proxies to the backend:

- `GET /api/v1/chat/sessions`

**Expected behavior:**

- JSON list of sessions:

  ```json
  {
    "items": [
      {
        "id": "...",
        "title": "Rent collection with Anuj",
        "last_message_at": "...",
        "created_at": "..."
      }
    ]
  }
  ```

### 8.2. Start a New Conversation via UI

In the chat UI:

- Start a **new conversation** (e.g. “New conversation” button).
- Ask any question, e.g.:

  > Give me a high‑level summary of my portfolio.

A new session should appear in:

- `/api/v1/chat/sessions`
- `GET /api/conversations` output.

---

## 9. Summary for Judges

This test plan validates:

- **User onboarding & auth** via Supabase + OAuth.
- **Property & unit management** via the **Management Agent** and tools (`add_property`, `add_unit`, `add_tenant_and_tenancy`).
- **Rent collection workflows** via the **Rent Agent**:
  - `get_tenants_with_rent_status`
  - `initiate_rent_collection_call`
  - `get_call_status`
  - `log_promised_payment_date`
  - `log_manual_payment`
- **Notifications & call logs** for rent calls.
- **Maintenance triage via WhatsApp** (vision + `triage_agent` + `create_maintenance_ticket`).
- **Analytics**:
  - **Vacancy Cost** (`get_vacancy_cost_for_landlord`)
  - **Rent Intelligence** (`analyze_rent_intelligence_for_landlord` + Google Search grounding).
- **Multi‑session chat** and the `/api/conversations` API.

Following these steps, a judge can see how PropStack uses ADK agents, tools, Supabase, Twilio, and Gemini (Live + Search grounded) end‑to‑end to manage a real landlord’s portfolio.

