# Implementation Plan: ADK-Based Voice Call with Transcript Logging

## Overview

Replace the custom `GeminiLiveBridge` (which uses `google-genai` SDK directly) with ADK's native `run_live()` streaming. ADK will handle:
- Connection to Vertex AI Gemini Live
- Audio streaming (bidirectional)
- Voice Activity Detection (VAD)
- Turn-taking and interruption handling
- Transcription (input/output)

## Problem

The current implementation uses a custom `GeminiLiveBridge` in `app/integrations/gemini_live.py` which connects directly to the `google-genai` SDK. This has issues connecting to Vertex AI when used in the Twilio voice call flow.

## Solution

Use Google's official ADK (Agent Development Kit) which handles all Gemini Live connectivity automatically and provides:
- Native bidirectional streaming
- Built-in transcription
- Proper Vertex AI integration via environment variables

## Architecture

```
PropStack (Landlord) 
      │
      ▼ initiates call
Twilio ──────────────► Tenant's Phone (outbound)
      │
      ▼
WebSocket: /calls/live/twilio/media/{call_id}
      │
      ▼
ADK Runner.run_live() ◄── LiveRequestQueue
      │
      ▼
Vertex AI Gemini Live (gemini-live-2.5-flash-native-audio)
      │
      ├──────────────────────┬──────────────────────┐
      ▼                      ▼                      ▼
  Audio Events          Input Transcription    Output Transcription
  (to Twilio)          (user speech)          (AI speech)
      │                      │                      │
      └──────────────────────┴──────────────────────┘
                              │
                              ▼
                    TranscriptCollector
                              │
                              ▼
                    Supabase: call_logs.transcript
```

## Files to Modify

| File | Action |
|------|--------|
| `app/config.py` | Add `gemini_live_model` setting |
| `app/routers/rent.py` | Replace websocket logic with ADK `run_live()` |
| `app/integrations/gemini_live.py` | Delete (not needed with ADK) |

---

## Detailed Changes

### 1. app/config.py

Add voice model configuration:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    gemini_live_model: str = "gemini-live-2.5-flash-native-audio"
```

### 2. app/routers/rent.py

#### A. Update Imports

```python
# REMOVE:
from app.integrations.gemini_live import GeminiLiveBridge

# ADD:
from google.adk.agents.live_request_queue import LiveRequestQueue
```

#### B. Update `_find_call_log` to Get Tenant Data

Get tenant details for dynamic greeting:

```python
def _find_call_log(call_id: str) -> dict | None:
    sb = get_supabase()
    call_row = (
        sb.table("call_logs")
        .select("""
            id, tenant_id, landlord_id, initiated_by,
            tenant:tenancies!call_logs_tenant_id_fkey(
                user:users!tenancies_tenant_id_fkey(name, phone),
                unit:units(
                    rent_amount,
                    property:properties(name)
                )
            )
        """)
        .eq("id", call_id)
        .limit(1)
        .execute()
    )
    if not call_row.data:
        return None
    
    data = call_row[0]
    if data.get("tenant"):
        data["tenant_name"] = data["tenant"].get("user", {}).get("name")
        data["tenant_phone"] = data["tenant"].get("user", {}).get("phone")
        data["rent_amount"] = data["tenant"].get("unit", {}).get("rent_amount")
        data["property_name"] = data["tenant"].get("unit", {}).get("property", {}).get("name")
    
    return data
```

#### C. Add TranscriptCollector Class

```python
class TranscriptCollector:
    """Collects conversation transcript for call logging."""
    
    def __init__(self):
        self.parts = []
    
    def add_user_speech(self, text: str):
        if text and text.strip():
            self.parts.append(f"User: {text}")
    
    def add_ai_speech(self, text: str):
        if text and text.strip():
            self.parts.append(f"Sara: {text}")
    
    def get_transcript(self) -> str:
        return "\n".join(self.parts)
```

#### D. Add `_build_initial_greeting` Function

Build dynamic greeting based on tenant data:

```python
def _build_initial_greeting(call_row: dict) -> str:
    """Build dynamic greeting based on tenant data."""
    tenant_name = call_row.get("tenant_name", "there")
    rent_amount = call_row.get("rent_amount", "0")
    property_name = call_row.get("property_name", "")
    
    try:
        amount = float(rent_amount) if rent_amount else 0
        amount_str = f"Rs. {amount:,.0f}"
    except (ValueError, TypeError):
        amount_str = "the rent"
    
    greeting = f"Hello, this is Sara from PropStack."
    
    if property_name:
        greeting += f" I'm calling about the property at {property_name}."
    else:
        greeting += " I'm calling about your rental property."
    
    greeting += f" I'm calling regarding your outstanding balance of {amount_str}."
    greeting += " Can you hear me clearly?"
    
    return greeting
```

#### E. Add `_voice_run_config` Function

```python
def _voice_run_config() -> RunConfig:
    """Create RunConfig for human-like voice conversation."""
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
    )
```

#### F. Replace WebSocket Endpoint

Replace `/calls/live/twilio/media/{call_id}` with ADK implementation:

```python
@router.websocket("/calls/live/twilio/media/{call_id}")
async def twilio_media_stream(websocket: WebSocket, call_id: str) -> None:
    """WebSocket for Twilio media stream with ADK Gemini Live integration."""
    call_row = _find_call_log(call_id)
    if not call_row:
        await websocket.close(code=4404, reason="Call log not found")
        return

    await websocket.accept()

    # Start live session tracking
    record = live_session_service.start_session(
        call_id=call_id,
        source="twilio_media_ws",
        metadata={"transport": "twilio_media_stream"},
    )
    live_session_id = record["session_id"]
    twilio_stream_sid: str | None = None

    # Transcript collection
    transcript_collector = TranscriptCollector()
    outbound_audio_task: asyncio.Task[None] | None = None
    live_queue: LiveRequestQueue | None = None

    # Audio transcoding check
    if settings.enable_partner_twilio_live:
        if not twilio_voice.has_audio_transcoding_support():
            logger.warning("Audio transcoding unavailable. Twilio live disabled.")
            if not settings.enable_custom_bridge_fallback:
                await websocket.close(code=1011, reason="Audio transcoding unavailable")
                return

    async def _send_audio_to_twilio():
        """Forward Gemini audio to Twilio in real-time."""
        nonlocal twilio_stream_sid
        if not live_queue:
            return
        
        while True:
            if not twilio_stream_sid:
                await asyncio.sleep(0.02)
                continue

            try:
                async for event in runner.run_live(
                    user_id=call_id,
                    session_id=live_session_id,
                    live_request_queue=live_queue,
                    run_config=_voice_run_config(),
                ):
                    # Handle audio from Gemini
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.inline_data:
                                payload = twilio_voice.pcm16_to_twilio_payload(
                                    part.inline_data.data
                                )
                                await websocket.send_text(json.dumps({
                                    "event": "media",
                                    "streamSid": twilio_stream_sid,
                                    "media": {"payload": payload}
                                }))
                    
                    # Collect transcriptions
                    if event.input_transcription:
                        transcript_collector.add_user_speech(
                            event.input_transcription.text
                        )
                    if event.output_transcription:
                        transcript_collector.add_ai_speech(
                            event.output_transcription.text
                        )
                    
                    # Handle interruption - ADK docs say DON'T break on interruption
                    # event.interrupted is a per-turn signal, not session-ending
                    # Keep the loop running so conversation can continue after user speaks
                    if event.interrupted:
                        logger.info(f"User interrupted - pausing audio call_id={call_id}")
                        transcript_collector.parts.append("[User interrupted]")
                        continue
                    
                    # Handle turn complete - signals end of model's response turn
                    if event.turn_complete:
                        logger.debug(f"Turn complete call_id={call_id}")
                        
            except Exception as e:
                logger.exception(f"ADK run_live error call_id={call_id}: {e}")
                break

    try:
        # Start ADK live session
        live_queue = LiveRequestQueue()
        
        # Start audio forwarding task
        outbound_audio_task = asyncio.create_task(_send_audio_to_twilio())

        while True:
            payload = await websocket.receive_text()
            event = json.loads(payload)
            event_type = (event.get("event") or "").lower()

            if event_type == "start":
                start_payload = event.get("start") or {}
                twilio_stream_sid = start_payload.get("streamSid")
                provider_call_sid = start_payload.get("callSid")
                
                logger.info(
                    "Twilio stream started call_id=%s stream_sid=%s",
                    call_id, twilio_stream_sid
                )
                
                live_session_service.attach_twilio_stream(
                    session_id=live_session_id,
                    twilio_stream_sid=twilio_stream_sid,
                    provider_call_sid=provider_call_sid,
                )
                
                # Send initial greeting
                greeting = _build_initial_greeting(call_row)
                live_queue.send_content(types.Content(
                    role="user",
                    parts=[types.Part(text=greeting)]
                ))
                continue

            if event_type == "media":
                media = event.get("media") or {}
                media_payload = media.get("payload")
                media_track = (media.get("track") or "").lower()
                
                if not media_payload or not live_queue:
                    continue
                if media_track and media_track != "inbound":
                    continue

                pcm_chunk = twilio_voice.twilio_payload_to_pcm16(media_payload)
                audio_blob = types.Blob(
                    mime_type="audio/pcm;rate=16000",
                    data=pcm_chunk
                )
                live_queue.send_realtime(audio_blob)
                continue

            if event_type in {"stop", "closed"}:
                break

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Twilio media websocket failed call_id=%s", call_id)
    finally:
        if outbound_audio_task:
            outbound_audio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await outbound_audio_task
        
        if live_queue:
            live_queue.close()

        # Save transcript to Supabase
        final_transcript = transcript_collector.get_transcript()
        if final_transcript:
            await save_call_result(
                call_id=call_id,
                transcript=final_transcript,
                outcome="completed",
                duration_seconds=0,
                provider_metadata={
                    "live_session_id": live_session_id,
                    "provider": "twilio_voice"
                },
            )

        live_session_service.end_session(
            session_id=live_session_id,
            status="ended",
            metadata={"reason": "socket_closed"},
        )
```

### 3. Delete app/integrations/gemini_live.py

This file is no longer needed - ADK handles all Gemini Live connectivity.

---

## Dynamic Greeting Examples

| Tenant Data | Greeting |
|-------------|----------|
| Default | "Hello, this is Sara from PropStack. I'm calling regarding your outstanding balance of Rs. 18,000. Can you hear me clearly?" |
| With property | "Hello, this is Sara from PropStack. I'm calling about the property at Sunset Apartments. I'm calling regarding your outstanding balance of Rs. 18,000. Can you hear me clearly?" |
| High amount | "Hello, this is Sara from PropStack. I'm calling regarding your outstanding balance of Rs. 25,000 which is now overdue. Can we discuss this?" |

---

## Transcript Format

```
call_logs.transcript column after call:

User: Hello, is this regarding the rent?
Sara: Hello, this is Sara from PropStack. I'm calling about the property at Sunset Apartments. I'm calling regarding your outstanding balance of Rs. 18,000. Can we discuss this?
User: Yes, I'll pay it next week.
Sara: Thank you. Just to confirm, you'll make the payment by when?
User: By 10th of this month.
Sara: Perfect. I've noted that you'll pay by the 10th. Thank you for your time.
```

---

## Environment Configuration (No Changes Needed)

Your existing `.env` is correct:

```
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=roll-153b3
GOOGLE_CLOUD_LOCATION=us-central1
```

---

## Testing Checklist

- [ ] Initiate call from dashboard
- [ ] Verify Twilio connects to websocket
- [ ] Verify ADK connects to Vertex AI Gemini Live
- [ ] Verify dynamic greeting is spoken (includes tenant name, rent amount, property)
- [ ] Test two-way audio (speak, hear response)
- [ ] Test interruption (cut off AI mid-sentence)
- [ ] Verify transcript saved to `call_logs.transcript`
- [ ] Verify transcript format is readable

---

## Key Benefits

1. **No custom SDK code** - Uses Google's official ADK
2. **Natural conversation** - ADK handles VAD, interruptions, turn-taking
3. **Transcript logging** - Every conversation recorded for review
4. **Dynamic greeting** - Personalized based on tenant data
5. **Flexible** - Easy to modify agent behavior via instructions
