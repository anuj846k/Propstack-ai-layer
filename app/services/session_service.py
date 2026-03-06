"""ADK SessionService factory.

Returns ``DatabaseSessionService`` backed by Supabase Postgres when
``SUPABASE_DB_PASSWORD`` is configured, or falls back to
``InMemorySessionService`` for local development / CI (no password needed).
"""

from __future__ import annotations

import logging

from google.adk.sessions import InMemorySessionService

logger = logging.getLogger(__name__)


def get_session_service():
    """Return the appropriate ADK SessionService based on config.

    - If ``SUPABASE_DB_PASSWORD`` and ``SUPABASE_URL`` are set → ``DatabaseSessionService``
      backed by Supabase Postgres via asyncpg (persistent across restarts).
    - Otherwise → ``InMemorySessionService`` (local dev / tests).
    """
    from app.config import (
        settings,  # import here to avoid circular imports at module load
    )

    db_url = settings.supabase_db_url
    if db_url:
        try:
            from google.adk.sessions import DatabaseSessionService

            logger.info(
                "Using DatabaseSessionService (Supabase Postgres) for ADK sessions"
            )
            return DatabaseSessionService(db_url=db_url)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to create DatabaseSessionService (%s) — falling back to InMemory",
                exc,
            )

    logger.warning(
        "SUPABASE_DB_PASSWORD not set — using InMemorySessionService. "
        "Chat sessions will be lost on server restart."
    )
    return InMemorySessionService()
