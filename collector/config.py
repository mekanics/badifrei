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
    baditicker_url: str
    status_poll_seconds: int


def _load_settings() -> Settings:
    return Settings(
        ws_url=os.getenv("WS_URL", "wss://badi-public.crowdmonitor.ch:9591/api"),
        database_url=os.getenv("DATABASE_URL"),  # None if unset — validated at connection time
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        baditicker_url=os.getenv(
            "BADITICKER_URL",
            "https://www.stadt-zuerich.ch/stzh/bathdatadownload",
        ),
        status_poll_seconds=int(os.getenv("STATUS_POLL_SECONDS", "900")),
    )


settings = _load_settings()
