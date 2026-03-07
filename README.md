# PropStack AI Service

AI layer for PropStack property management, powered by [Google ADK](https://google.github.io/adk-docs/) and FastAPI.

## Current Agent: Rent Collection

- Checks rent payment status across all tenants
- Identifies overdue payments with days-overdue calculation
- Initiates voice calls via Twilio Voice (trial/prod)
- Logs transcripts, outcomes, and recordings to Supabase
- Notifies landlords on their dashboard

## Quick Start

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Copy env and fill in your keys
cp .env.example .env

# Test with ADK Dev UI (interactive chat)
uv run adk web

# Run the FastAPI production server
uv run uvicorn app.main:app --reload --port 8001

# Chat with Sara via the Next.js dashboard (POST /api/v1/chat)
```

Note:
- On Python 3.13+, `audioop-lts` is installed automatically via `uv sync` for Twilio/Gemini audio transcoding.

## Project Structure

```
propstack-ai/
├── docs/                    # Documentation
│   ├── logic.md             # Rent due, grace period, overdue logic
│   └── PROGRESS.md          # Done / next / wiring status
├── app/agents/rent_collection/
│   └── agent.py             # root_agent definition + ADK callbacks
├── rent_collection_agent/   # compatibility import for ADK discoverability
│   └── agent.py
├── app/                     # FastAPI application
│   ├── main.py              # Entry point
│   ├── config.py            # Settings via pydantic-settings
│   ├── dependencies.py      # Supabase client
│   ├── routers/rent.py      # API endpoints
│   ├── tools/               # ADK Function Tools (safe DB wrappers)
│   └── schemas/             # Pydantic response models
├── tests/
├── pyproject.toml           # uv / pip dependencies
└── .env.example
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health check |
| POST | `/api/v1/chat` | Streaming chat with Sara (SSE) |
| POST | `/api/v1/check-rent?landlord_id=...` | Check rent status for all tenants |
| POST | `/api/v1/initiate-call` | Initiate a rent collection call |
| POST | `/api/v1/rent/sweep` | Scheduler endpoint for overdue sweeps |
| POST | `/api/v1/calls/callback` | Save call outcomes from provider callbacks |
| POST | `/api/v1/calls/twilio/status?call_id=...` | Twilio voice status callback (signature validated) |
| POST | `/api/v1/calls/twilio/twiml/{call_id}` | Minimal TwiML bootstrap for Twilio media stream |
| POST | `/api/v1/calls/live/session/start` | Start tracked live session for a call |
| POST | `/api/v1/calls/live/session/end` | End tracked live session and finalize call outcome |
| WS | `/api/v1/calls/live/twilio/media/{call_id}` | Twilio media stream bridge (partner-first path) |
| WS | `/api/v1/live/browser/{session_id}` | Browser live streaming demo endpoint (ADK) |
| POST | `/api/v1/payments/manual-cash` | Log landlord-entered cash payments |
| POST | `/api/v1/payments/webhook/razorpay` | Razorpay payment webhook |

## Twilio Trial Setup

Set these env vars in `propstack-ai/.env`:

```bash
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_VOICE_FROM_NUMBER=+1...
PUBLIC_BASE_URL=https://your-public-api-domain
TWILIO_VALIDATE_WEBHOOK_SIGNATURE=true
TWILIO_TRIAL_MODE=true
TWILIO_TRIAL_ALLOWED_TO_NUMBERS=+91XXXXXXXXXX,+91YYYYYYYYYY
TWILIO_CALL_TIMEOUT_SECONDS=30
TWILIO_STREAM_SAMPLE_RATE_HZ=8000

GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
GEMINI_LIVE_MODEL=gemini-live-2.5-flash-native-audio
GEMINI_LIVE_LOCATION=us-central1
LIVE_SESSION_MAX_SECONDS=900
LIVE_INPUT_SAMPLE_RATE_HZ=16000
LIVE_OUTPUT_SAMPLE_RATE_HZ=24000
ENABLE_PARTNER_TWILIO_LIVE=true
ENABLE_CUSTOM_BRIDGE_FALLBACK=false
```

Twilio configuration notes:
- Outbound calls are created by backend API (`calls.create`) and TwiML URL is passed per call.
- TwiML endpoint is now bootstrap-only and hands off audio to `/api/v1/calls/live/twilio/media/{call_id}`.
- Status callback URL is set by backend per call as `.../api/v1/calls/twilio/status?call_id=<internal_call_id>`.
- In trial mode, verify destination numbers in Twilio Console and keep them in `TWILIO_TRIAL_ALLOWED_TO_NUMBERS`.
- For Vertex Live API auth, run `gcloud auth application-default login` on the machine running FastAPI (or set `GOOGLE_APPLICATION_CREDENTIALS` to a service account JSON). Without this, live audio bridge startup fails.
- If you see `Publisher Model ... was not found`, use `GEMINI_LIVE_LOCATION=us-central1` (recommended for Live API model availability).

## Architecture

```
Next.js Frontend (port 3000)
    │
    ▼
FastAPI AI Service (port 8001)
    │
    ├── Google ADK Agent (Gemini 2.5 Flash)
    │     └── Function Tools (safe Supabase wrappers)
    │
    └── Supabase (PostgreSQL + Auth + Storage)
```


So the workflow is:

Development testing → PYTHONPATH=. adk web adk_agents --port 8002 (already running ✅)
Production / frontend → uvicorn app.main:app --port 8001 (already running ✅)