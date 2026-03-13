"""Configuration for the badi-predictor collector."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    ws_url: str
    database_url: str | None
    log_level: str


def _load_settings() -> Settings:
    return Settings(
        ws_url=os.getenv("WS_URL", "wss://badi-public.crowdmonitor.ch:9591/api"),
        database_url=os.getenv("DATABASE_URL"),  # None if unset — validated at connection time
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


settings = _load_settings()
