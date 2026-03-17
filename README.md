# PropStack – Live AI Rent & Maintenance Agent

**PropStack** is an AI-powered property management platform that gives small landlords their own "AI operations team" — a live rent collection agent and a maintenance triage + vendor dispatch assistant that talk to tenants and vendors over voice and WhatsApp, grounded on real data in Supabase and deployed on Google Cloud Run with Gemini + ADK.

This is my submission to the **Gemini Live Agent Challenge** in the **Live Agents** category.

---

## Code Repositories

- **Frontend:** [https://github.com/anuj846k/PropStack](https://github.com/anuj846k/PropStack) (Next.js 16 dashboard)
- **Backend (AI Agents):** [https://github.com/anuj846k/Propstack-ai-layer](https://github.com/anuj846k/Propstack-ai-layer) (Python FastAPI + Google ADK)

---

## Problem & Vision

Independent landlords juggle three painful jobs:

1. **Rent collection** – chasing late payments, logging calls, tracking promises
2. **Maintenance triage** – turning vague WhatsApp messages like "water problem" into structured tickets
3. **Vendor dispatch** – calling plumbers/electricians, explaining issues, negotiating availability

These are all repetitive, time-sensitive conversations that don't belong in dashboards — they belong in your phone and WhatsApp, handled by a smart, interruption-safe agent that's grounded in your actual portfolio data.

**My vision:** a live, multimodal "property operations team":

- **Sara**, the rent collection agent, who talks to tenants, calls them, and keeps the dashboard consistent
- A maintenance agent that understands tenant messages on WhatsApp, creates structured tickets, and then calls vendors live to accept jobs

---

## What I Built

PropStack is an end-to-end system with:

### Frontend (propstack-frontend)

- **Next.js 16** dashboard (App Router)
- Landlords manage properties, units, tenants, maintenance tickets, and chat with Sara
- Modern UI with Tailwind CSS v4 + Shadcn components

### Backend (propstack-ai)

- **Python FastAPI** + **Google ADK**
- ADK agents for:
  - Rent collection (chat + Twilio voice)
  - Maintenance triage (WhatsApp)
  - Vendor dispatch (Twilio voice + Gemini Live)
- Tool layer over Supabase (PostgreSQL) and Twilio

### Database

- **Supabase** (PostgreSQL) with schema for:
  - `users`, `properties`, `units`, `tenancies`
  - `rent_cycles`, `payments`
  - `maintenance_tickets`, `ticket_images`, `activity_log`
  - `call_logs`, `vendor_dispatch_logs`
  - `notifications`

### AI / Voice

- **Gemini 2.5 Flash** via ADK/Vertex AI for text agents
- **Gemini Live 2.5 Flash Native Audio** for voice agents
- **Twilio** for WhatsApp + Voice

---

## Why This Breaks the Text Box

This project intentionally goes beyond "chatbot in a div":

### 1. Live Voice to Vendors (Gemini Live + Twilio Media Streams)

- Bi-directional WebSocket bridge from Twilio Media Streams to ADK `run_live`
- Vendors can interrupt, ask questions, and the agent adapts in real-time
- Agent has structured context: ticket category, severity, unit number, property name, and address

### 2. WhatsApp Maintenance Triage

- Tenants send natural WhatsApp messages (text + optional images)
- Agent triages with a multi-phase protocol (acknowledge → ask clarifying questions → create structured ticket)
- Images fetched and passed to Gemini

### 3. Dashboard Chat with Sara

- Live, streaming text chat (Server-Sent Events)
- Agents use tools for all data fetches — no hallucinated SQL
- Hub agent routes between rent collection and property management

### 4. Persona & UX

- Sara has a consistent persona in voice and text
- Voice agents are short, interruption-safe, bilingual (English/Hindi)
- Optimized for phone-call dynamics, not essays

---

## Proof of Google Cloud Deployment

Click the image below to watch the recording proving the backend is running on Google Cloud Run with Vertex AI:

[![Google Cloud Deployment Proof](https://storage.googleapis.com/propstack-bucket/assets/architecture/Gcloud.png)](https://storage.googleapis.com/propstack-bucket/gcloudmp4.mp4)

---

## Architecture

**System overview:** how the Next.js dashboard, Twilio (WhatsApp + Voice), FastAPI + ADK agents (Cloud Run), Vertex AI (Gemini + Gemini Live), and Supabase work together.

![Architecture Diagram](https://storage.googleapis.com/propstack-bucket/assets/architecture/Architecture.png)

---

## Product Screenshots

### Landing Page

The landing page introduces PropStack and the “Sara” live agent experience for landlords.

![Landing Page Hero](https://storage.googleapis.com/propstack-bucket/assets/landing/Hero.png)
![Landing Page Features](https://storage.googleapis.com/propstack-bucket/assets/landing/Hero.png)

### Dashboard (Landlord)

The dashboard shows portfolio insights (vacancy cost, open tickets, rent activity) and connects directly to the live agent workflows.

![Dashboard Screenshot](https://storage.googleapis.com/propstack-bucket/assets/dashboard/Dashboard.png)

### Chat and Call with Sara (ADK Agents)

Landlords can chat with Sara for rent status, tenant lookup, and management actions (grounded in Supabase via tools).

![Chat Screenshot](https://storage.googleapis.com/propstack-bucket/assets/dashboard/Chat.png)
![Call Agent Screenshot](https://storage.googleapis.com/propstack-bucket/assets/dashboard/CallAgent.png)

### Maintenance Tickets with Twilio Whatsapp (ADK Agents)

Tenants can report issues via WhatsApp — sending text messages with optional photos. The AI agent triages these messages, asks clarifying questions when needed, and automatically creates structured maintenance tickets in the system. Landlords see all tickets on their dashboard with images, priority, and status.

![Owner Dashboard Tickets](https://storage.googleapis.com/propstack-bucket/assets/dashboard/Tickets.png)
![Tenant Side Whatsapp](https://storage.googleapis.com/propstack-bucket/assets/dashboard/WhatsappAgent.png)

| Component       | Technology                                            |
| --------------- | ----------------------------------------------------- |
| Frontend        | Next.js 16.1.6, React 19, TypeScript, Tailwind CSS v4 |
| Backend         | Python FastAPI, Google ADK                            |
| Database        | Supabase (PostgreSQL)                                 |
| AI              | Gemini 2.5 Flash, Gemini Live 2.5                     |
| Voice/Messaging | Twilio (Voice + WhatsApp)                             |
| Deployment      | Google Cloud Run                                      |
| Container       | Docker                                                |

---

## Key Agent Flows

### 1. Rent Collection (Chat + Voice)

```

Landlord → Dashboard Chat → hub_agent → rent_agent
↓
Tools: get_tenants_with_rent_status,
initiate_rent_collection_call
↓
Twilio → Tenant Call

```

### 2. Maintenance Triage (WhatsApp)

```

Tenant (WhatsApp) → Twilio Webhook → maintenance_triage_agent
↓
Tools: create_maintenance_ticket
↓
Landlord Dashboard (structured ticket)

```

### 3. Vendor Dispatch (Live Voice)

```

System Trigger → Find Vendor → Twilio Outbound
↓
vendor_dispatch_agent (Gemini Live)
↓
Tools: vendor_accepts_ticket / vendor_rejects_ticket
↓
Updates: maintenance_tickets, vendor_dispatch_logs

```

---

## Tooling (ADK Function Tools)

PropStack’s agents are grounded via **Google ADK function tools** (plain Python functions) that read/write to Supabase and trigger Twilio workflows.

### Rent & Payments (`app/tools/rent_tools.py`)

- `get_tenants_with_rent_status(landlord_id)`
- `get_tenant_payment_history(tenant_id)`
- `get_tenant_collection_history(tenant_id)`
- `log_promised_payment_date(tenant_id, promised_date)`
- `log_manual_payment(tenant_id, amount)`
- `list_units_for_landlord(landlord_id)`

### Tenant Lookup (`app/tools/tenant_tools.py`)

- `find_tenant_by_name(name, landlord_id)`
- `find_tenant_by_phone(phone, landlord_id)`
- `update_tenant_details(tenant_id, name?, phone?, email?, preferred_language?)`

### Calls & Call Logs (`app/tools/call_tools.py`)

- `initiate_rent_collection_call(landlord_id, tenant_id, tenant_name, tenant_phone, language, rent_amount, days_overdue, property_name, unit_number, landlord_name)`
- `get_call_status(landlord_id, tenant_id?, call_id?)`
- `save_call_result(call_id, transcript, outcome, duration_seconds, provider_metadata?)`
- `save_call_result_from_agent(call_id, transcript, outcome, duration_seconds)`

### Rent Intelligence (grounded market research) (`app/tools/rent_intel_tools.py`)

- `get_vacancy_cost_for_landlord(landlord_id, as_of_date?)`
- `estimate_market_rent_for_unit(city, state?, unit_description, current_rent?)`
- `analyze_rent_intelligence_for_landlord(landlord_id, sample_limit=5)`

> Note: `rent_intel_tools.py` also uses ADK’s built-in `google_search` tool for fresh comps.

## Google Cloud & Gemini Usage

**Gemini Models:**

- Text agents: `gemini-2.5-flash` via ADK + Vertex AI
- Live voice: `gemini-live-2.5-flash-native-audio` via ADK `run_live` + Vertex AI

**ADK Usage:**

- All agents defined as `LlmAgent` with tools and instructions
- `Runner` with proper `RunConfig` (text + live)
- Session management via `SessionService`
- Tool guardrails via `before_tool_callback`

**Google Cloud Services:**

- Backend container deployed to **Google Cloud Run**
- Vertex AI for online text generation with tools
- Vertex AI Live API for streaming audio
- Configuration in `app/config.py`:

```python
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

---

## Setup & Spin-Up Instructions

### Prerequisites

- Node.js (LTS) and pnpm
- Python 3.10+ and uv
- Supabase project
- Twilio account (Voice + WhatsApp)
- Google Cloud project with Vertex AI and Cloud Run enabled

### 1. Backend (propstack-ai)

```bash
cd propstack-ai

# Install dependencies
uv sync

# Create .env from .env.example with:
# - GOOGLE_GENAI_USE_VERTEXAI=TRUE
# - GOOGLE_CLOUD_PROJECT=your-gcp-project-id
# - GOOGLE_CLOUD_LOCATION=us-central1
# - SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_DB_PASSWORD
# - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_VOICE_FROM_NUMBER
# - PUBLIC_BASE_URL=https://your-ngrok-or-cloudrun-url

# Run locally
uv run uvicorn app.main:app --reload --port 8001

# For local Twilio testing
ngrok http 8001
```

### 2. Frontend (propstack-frontend)

```bash
cd propstack-frontend
pnpm install
pnpm dev
```

### 3. Deploy to Google Cloud Run

```bash
# Build container
docker build -t gcr.io/your-project/propstack-ai -f propstack-ai/Dockerfile .

# Push to Container Registry
docker push gcr.io/your-project/propstack-ai

# Deploy to Cloud Run
gcloud run deploy propstack-ai \
  --image gcr.io/your-project/propstack-ai \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=TRUE,\
GOOGLE_CLOUD_PROJECT=your-project-id,\
GOOGLE_CLOUD_LOCATION=us-central1,\
SUPABASE_URL=...,\
SUPABASE_SERVICE_KEY=...,\
TWILIO_ACCOUNT_SID=...,\
TWILIO_AUTH_TOKEN=...
```

---

## Testing

### Unit Tests

```bash
cd propstack-ai
pytest
```

Test coverage:

- Tool functions (Supabase operations)
- Router endpoints
- Agent callbacks (guardrails, normalization)
- Call policy service
- Live session service

### Manual Testing Flows

1. **Dashboard Chat**: Ask Sara about rent status
2. **WhatsApp**: Send maintenance issues with/without images
3. **Vendor Calls**: Trigger dispatch, accept/reject as vendor

### Twilio trial account note

Right now the voice/WhatsApp flows run on a **Twilio trial account**, which means:

- Outbound calls can only be placed to **verified numbers** on my Twilio account.
- In my demo this is configured with **only my own phone number** as a verified destination.

If you clone this project and want to run the full voice/WhatsApp flows yourself, you should:

1. Create your own Twilio account (and upgrade out of trial if you want to call arbitrary numbers).
2. Update the Twilio environment variables in `.env` (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_VOICE_FROM_NUMBER`, WhatsApp sender, etc.).
3. Optionally verify your own test numbers (if you stay on a trial account) and trigger calls to those numbers instead.

### Demo credentials

For quick end-to-end testing with pre-populated sample data, you can sign in with:

- **Email**: `admin@gmail.com`
- **Password**: `Test@123`

Use this account to explore the dashboard, rent flows, and maintenance tickets without having to seed everything from scratch.

### End-to-end test flow

You can test the full PropStack flow (from data setup through rent checks, calls, and logging) by following the step-by-step scenarios described in [`propstack-ai/TEST_PLAN.md`](./TEST_PLAN.md).

That document covers:

- Creating landlords, properties, units, and tenancies
- Generating rent cycles
- Triggering rent collection calls
- Verifying call logs, summaries, and payment updates

---

## Learnings

1. **ADK + Vertex AI is a great fit for complex agent workflows**

- `LlmAgent`, tools, and `Runner` enable multi-phase flows
- Business logic stays in Python

2. **Live voice changes prompt design**

- Response length: 1-2 sentences
- Interruption handling
- When to repeat location vs. detail

3. **Supabase + Twilio + Gemini = full-stack AI operations**

- Single Postgres schema
- Own entire journey: WhatsApp → ticket → vendor call → dashboard

---

## Project Structure

```
propstack-main/
├── propstack-frontend/          # Next.js 16 frontend
│   ├── app/                    # App Router pages
│   ├── components/              # React components (Shadcn + custom)
│   └── lib/                    # Utilities, API clients
│
├── propstack-ai/               # Python FastAPI + ADK backend
│   ├── app/
│   │   ├── agents/            # ADK agents (hub, rent, management, maintenance)
│   │   ├── tools/            # Function tools (16 tools)
│   │   ├── routers/           # FastAPI routes
│   │   ├── services/         # Business logic
│   │   └── config.py         # Settings
│   └── tests/                 # pytest test suite
│
├── Dockerfile                  # Backend container
├── docker-compose.yml          # Local dev orchestration
└── README.md                   # Readme file
```

---

## Demo Video

[Watch the 4-minute demo video →](https://youtu.be/7aZmRhIZRCs?si=jDM3WjHqIYfmmY3W)

---

## Future Work

### Vision: Next-Gen RWA (Resident Welfare Association) Platform

This project started as a vision to upgrade everyday apps like **MyGate** — a widely used RWA management platform — into an agentic, AI-powered system. While MyGate handles CRUD operations for society management, the world is moving toward agentic workflows. I envision integrating AI agents to handle:

- **Automated visitor verification** with AI-powered security checks
- **Smart maintenance dispatch** — agents that call vendors, negotiate prices, and schedule repairs
- **AI community managers** — handling complaints, announcements, and resident communication
- **Financial automation** — rent collection, invoice generation, and expense tracking with intelligent reminders
- **Compliance & security** — AI agents that monitor suspicious activities and alert security

PropStack demonstrates the core AI capabilities (voice agents, WhatsApp integration, live vendor calls) that can power the next generation of RWA platforms — making residential societies more secure, efficient, and tech-enabled.

---

_Built with ❤️ for the Gemini Live Agent Challenge_
