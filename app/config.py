from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

from pydantic_settings import BaseSettings


def _normalize_sqlalchemy_asyncpg_url(url: str) -> str:
    """Normalize Postgres URLs to SQLAlchemy asyncpg format."""
    normalized = url.strip()
    if normalized.startswith("postgresql+asyncpg://"):
        return normalized
    if normalized.startswith("postgresql://"):
        return "postgresql+asyncpg://" + normalized[len("postgresql://") :]
    if normalized.startswith("postgres://"):
        return "postgresql+asyncpg://" + normalized[len("postgres://") :]
    return normalized


def _with_default_query_params(url: str, defaults: dict[str, str]) -> str:
    """Add query params only when they are missing."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in defaults.items():
        query.setdefault(key, value)
    return urlunparse(parsed._replace(query=urlencode(query)))


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""
    supabase_db_password: str = ""
    # Optional: full Session Pooler URL from Supabase dashboard.
    # Example: postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
    supabase_database_url: str = ""
    # Optional: Session Pooler host from Supabase dashboard.
    # Example: aws-0-ap-south-1.pooler.supabase.com
    supabase_pooler_host: str = ""
    supabase_pooler_port: int = 5432
    supabase_jwt_secret: str = ""
    google_api_key: str = ""
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    gemini_model: str = "gemini-2.5-flash"
    gemini_live_model: str = "gemini-live-2.5-flash-native-audio"
    gemini_live_location: str = "us-central1"
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    sip_trunk_id: str = ""
    environment: str = "development"
    log_level: str = "INFO"  # DEBUG in dev, INFO/WARNING in prod
    nextjs_base_url: str = "http://localhost:3000"
    rent_due_day: int = 1
    grace_period_days: int = 5
    demo_landlord_id: str = ""  # Set in .env; "demo" aliases to this UUID in chat API
    razorpay_webhook_secret: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_voice_from_number: str = ""
    public_base_url: str = "http://localhost:8001"
    twilio_validate_webhook_signature: bool = True
    twilio_call_timeout_seconds: int = 30
    twilio_stream_sample_rate_hz: int = 8000
    internal_scheduler_token: str = ""
    callback_shared_secret: str = ""
    internal_api_secret: str = "propstack-internal-secret"
    adk_max_llm_calls: int = 40
    live_session_max_seconds: int = 900
    live_input_sample_rate_hz: int = 16000
    live_output_sample_rate_hz: int = 24000
    enable_partner_twilio_live: bool = True
    enable_custom_bridge_fallback: bool = False
    call_window_start_hour: int = 9
    call_window_end_hour: int = 20
    max_call_attempts_per_tenant_per_day: int = 2

    @property
    def supabase_db_url(self) -> str:
        """Async Postgres URL for ADK DatabaseSessionService.

        Priority:
        1) SUPABASE_DATABASE_URL (full connection string from dashboard)
        2) Build Session Pooler URL from SUPABASE_URL + SUPABASE_DB_PASSWORD
           (+ optional SUPABASE_POOLER_HOST/PORT)
        Returns empty string if credentials are missing -> falls back to InMemory.
        """
        if self.supabase_database_url:
            # Ensure SQLAlchemy asyncpg driver and required Supabase pooler params.
            return _with_default_query_params(
                _normalize_sqlalchemy_asyncpg_url(self.supabase_database_url),
                {"ssl": "require", "prepared_statement_cache_size": "0"},
            )

        if not self.supabase_url or not self.supabase_db_password:
            return ""

        # supabase_url = https://<project-ref>.supabase.co
        supabase_host = urlparse(self.supabase_url).hostname or ""
        project_ref = supabase_host.split(".")[0]
        if not project_ref:
            return ""

        password = quote_plus(self.supabase_db_password)
        # Session-mode pooler: username = postgres.<project-ref>, port = 5432
        # NOTE: override SUPABASE_POOLER_HOST for non-ap-south-1 regions.
        pooler_host = (
            self.supabase_pooler_host or "aws-0-ap-south-1.pooler.supabase.com"
        )
        return (
            f"postgresql+asyncpg://postgres.{project_ref}:{password}"
            f"@{pooler_host}:{self.supabase_pooler_port}/postgres"
            f"?ssl=require&prepared_statement_cache_size=0"
        )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Allow GOOGLE_GENAI_USE_VERTEXAI etc. for ADK/GenAI
    }


settings = Settings()
