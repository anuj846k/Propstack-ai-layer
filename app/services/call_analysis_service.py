"""Generate Sara's Analysis (summary, promise amount/date, sentiment) from call transcript or event_data."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def _text_from_transcript(transcript: str | None) -> str:
    """Convert stored transcript JSON to plain text for the model."""
    if not transcript or not transcript.strip():
        return ""
    try:
        parts = json.loads(transcript)
    except json.JSONDecodeError:
        return transcript
    lines: list[str] = []
    for p in parts if isinstance(parts, list) else []:
        speaker = (p.get("speaker") or "unknown").lower()
        text = (p.get("text") or "").strip()
        if not text:
            continue
        if speaker == "user":
            lines.append(f"Tenant: {text}")
        elif speaker == "sara":
            lines.append(f"Sara: {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _text_from_event_data(event_data: dict[str, Any] | None) -> str:
    """Extract text from ADK-style event_data (e.g. content.parts[].text)."""
    if not event_data:
        return ""
    content = event_data.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            texts.append(p["text"].strip())
    return "\n".join(texts) if texts else ""


def build_analysis_prompt(transcript_text: str, event_text: str) -> str:
    """Build prompt for the model to output structured analysis."""
    combined = transcript_text.strip()
    if event_text.strip():
        combined = f"{combined}\n\n--- Additional context ---\n{event_text.strip()}" if combined else event_text.strip()
    if not combined:
        return ""
    return f"""You are Sara's analysis assistant. Based on the following rent collection call transcript (and any additional context), output a JSON object with exactly these keys:
- "summary": One or two sentences summarizing the call outcome (e.g. whether rent was acknowledged, promised, or any escalation).
- "promiseAmount": If the tenant promised a specific amount, format it like "₹18,000" or "Rs. 15,000". Otherwise use null.
- "promiseDate": If the tenant promised a date, use a short format like "Feb 24" or "Mar 15". Otherwise use null.
- "sentiment": One of: "cooperative", "positive", "neutral", "frustrated", "no_answer". Default "neutral" if unclear.

Transcript and context:
{combined}

Respond with only the JSON object, no markdown or extra text."""


async def generate_call_analysis(
    transcript: str | None,
    event_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call Gemini to produce structured analysis. Returns dict with summary, promiseAmount, promiseDate, sentiment."""
    transcript_text = _text_from_transcript(transcript)
    event_text = _text_from_event_data(event_data)
    prompt = build_analysis_prompt(transcript_text, event_text)
    if not prompt:
        return {
            "status": "no_data",
            "summary": "No transcript or context available for this call.",
            "promiseAmount": None,
            "promiseDate": None,
            "sentiment": "neutral",
        }

    text = ""
    try:
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
        )
        text = (response.text or "").strip()
        if not text:
            return {
                "summary": "Could not generate analysis.",
                "promiseAmount": None,
                "promiseDate": None,
                "sentiment": "neutral",
            }
        # Strip markdown code block if present
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        out = json.loads(text)
        return {
            "status": "success",
            "summary": out.get("summary") or "No summary generated.",
            "promiseAmount": out.get("promiseAmount"),
            "promiseDate": out.get("promiseDate"),
            "sentiment": (out.get("sentiment") or "neutral").lower(),
        }
    except json.JSONDecodeError as e:
        logger.warning("Call analysis JSON parse error: %s", e)
        return {
            "status": "error",
            "summary": (text[:500] if text else "Analysis response was not valid JSON."),
            "promiseAmount": None,
            "promiseDate": None,
            "sentiment": "neutral",
        }
    except Exception as e:
        logger.exception("Call analysis generation failed: %s", e)
        return {
            "status": "error",
            "summary": "Analysis is temporarily unavailable.",
            "promiseAmount": None,
            "promiseDate": None,
            "sentiment": "neutral",
        }
