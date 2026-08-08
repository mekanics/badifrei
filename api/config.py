"""Typed API settings (pydantic-settings). See ADR-002."""

from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str | None = None
    cors_allowed_origins: str = "https://badifrei.ch"
    umami_script_url: str = ""
    umami_website_id: str = ""
    weekly_insights_cache_ttl_seconds: int = 3600

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def umami_csp_origin(self) -> str:
        if not self.umami_script_url:
            return ""
        parsed = urlparse(self.umami_script_url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
