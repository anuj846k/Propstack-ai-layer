"""PropStack AI Service — load .env before any Google GenAI/ADK imports."""
# ruff: noqa: E402
from pathlib import Path

import logging
import os

log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

from dotenv import load_dotenv

# Load .env into os.environ so google-genai (GOOGLE_API_KEY) can read it
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import payments, rent, properties
from app.services.live_session_service import live_session_service


@asynccontextmanager
async def lifespan(_app: FastAPI):
    live_session_service.cleanup_expired(max_age_seconds=settings.live_session_max_seconds)
    try:
        yield
    finally:
        live_session_service.shutdown()


app = FastAPI(
    title="PropStack AI Service",
    description="AI Layer for PropStack property management — Rent Collection Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        settings.nextjs_base_url,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rent.router, prefix="/api/v1", tags=["rent"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["payments"])
app.include_router(properties.router, prefix="/api/v1", tags=["properties"])


@app.get("/health")
async def health_check() -> dict:
    return {"status": "healthy", "environment": settings.environment}
