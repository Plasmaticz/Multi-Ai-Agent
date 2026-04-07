from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Multi-Agent Research Copilot"
    environment: str = "dev"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: int = 45
    max_concurrent_research: int = 4
    max_review_loops: int = 2
    min_sources_per_company: int = 2
    default_companies: str = "Archireef,Coral Vita,SECORE International"
    request_timeout_seconds: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def default_company_list(self) -> list[str]:
        return [item.strip() for item in self.default_companies.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
