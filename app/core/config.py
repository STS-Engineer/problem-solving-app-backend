from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
ENV_DIST_FILE = PROJECT_ROOT / ".env.dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ENV_DIST_FILE), str(ENV_FILE)),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    DATABASE_URL: str
    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_MODEL: str
    OPENAI_MAX_TOKENS: int = 4000
    OPENAI_TEMPERATURE: float = 0.2


settings = Settings()


class WebhookSettings(BaseSettings):
    # Shared HMAC-SHA256 signing secret — must match the audit app's secret
    webhook_secret: str = ""

    # Single target URL — the audit app's receiver endpoint
    webhook_target: str = ""

    # Tuning (override in Azure config if needed, defaults are fine)
    webhook_timeout_sec: int = 10
    webhook_max_attempts: int = 3
    webhook_poll_interval: int = 300  # seconds between DB polls
    webhook_trigger_types: str = "CS1,CS2"

    # Comma-separated emails to alert when a job permanently fails
    # e.g. "admin@avocarbon.com,devops@avocarbon.com"
    # Leave empty to disable email alerts
    webhook_alert_emails_raw: str = "hayfa.rajhi@avocarbon.com"

    model_config = SettingsConfigDict(
        env_file=(str(ENV_DIST_FILE), str(ENV_FILE)),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def webhook_alert_emails(self) -> list[str]:
        return [
            e.strip() for e in self.webhook_alert_emails_raw.split(",") if e.strip()
        ]

    @property
    def target_urls(self) -> list[str]:
        """Always returns a list so the service code stays the same."""
        url = self.webhook_target.strip()
        return [url] if url else []

    @property
    def trigger_types(self) -> frozenset[str]:
        return frozenset(
            t.strip() for t in self.webhook_trigger_types.split(",") if t.strip()
        )

    def validate_config(self) -> None:
        import logging

        log = logging.getLogger(__name__)
        if not self.webhook_secret:
            log.warning("WEBHOOK_SECRET is not set — requests will not be signed.")
        if not self.webhook_target:
            log.warning(
                "WEBHOOK_TARGET is not set — webhooks will be queued but never delivered."
            )


@lru_cache(maxsize=1)
def get_webhook_settings() -> WebhookSettings:
    cfg = WebhookSettings()
    cfg.validate_config()
    return cfg
