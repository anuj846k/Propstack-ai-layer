from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""
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
    twilio_trial_mode: bool = True
    twilio_trial_allowed_to_numbers: str = ""
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

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Allow GOOGLE_GENAI_USE_VERTEXAI etc. for ADK/GenAI
    }


settings = Settings()
