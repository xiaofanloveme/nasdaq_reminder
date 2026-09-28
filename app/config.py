from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    target_ticker: str = "CLSK"
    alert_threshold: float = 5.0
    poll_interval_minutes: int = 15
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    tavily_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_user: str | None = None
    smtp_password: str | None = None
    alert_recipient: str | None = None
    database_path: Path = Path("data/alerts.db")
    enable_scheduler: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def email_ready(self) -> bool:
        return all(
            [self.smtp_host, self.smtp_user, self.smtp_password, self.alert_recipient]
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
