from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Healthclaw"
    app_env: str = "local"
    api_key: str = Field(default="dev-healthclaw-key")
    database_url: str = "postgresql+asyncpg://healthclaw:healthclaw@localhost:5432/healthclaw"
    auto_create_db: bool = False
    redis_url: str = "redis://localhost:6379/0"
    default_timezone: str = "Asia/Colombo"
    default_quiet_start: str = "22:00"
    default_quiet_end: str = "07:00"
    default_proactive_max_per_day: int = 2
    default_proactive_cooldown_minutes: int = 180
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    openrouter_api_key: str | None = None
    openrouter_chat_model: str = "moonshotai/kimi-k2.6"
    openrouter_chat_fallback_models: str = "minimax/minimax-m2.7,openai/gpt-5.4-mini"
    openrouter_chat_max_tokens: int = 700
    openrouter_chat_temperature: float = 0.75
    openrouter_transcribe_model: str = "mistralai/voxtral-small-24b-2507"
    openrouter_site_url: str | None = None
    openrouter_app_name: str = "Healthclaw"
    langsmith_tracing: bool = False
    langsmith_project: str = "healthclaw"
    otel_exporter_otlp_endpoint: str | None = None
    trace_raw_content: bool = False
    memory_retrieval_limit: int = 8
    recent_message_context_limit: int = 30
    recent_message_context_max_chars: int = 12_000
    openrouter_dream_model: str = "google/gemini-2.5-flash-lite"
    openrouter_decision_model: str = "google/gemini-2.5-flash-lite"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    context_harness_mode: str = "active"
    context_harness_candidate_memory_limit: int = 16
    context_harness_memory_chars: int = 1800
    context_harness_open_loop_chars: int = 320
    context_harness_recent_raw_turn_limit: int = 6
    context_harness_recent_chars: int = 2200
    context_harness_thread_summary_chars: int = 480
    context_harness_doc_section_chars: int = 420
    context_harness_document_chars: int = 1400
    use_arq_worker: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
