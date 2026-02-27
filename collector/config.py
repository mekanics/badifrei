"""Configuration for the badi-predictor collector."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    ws_url: str
    database_url: str
    test_database_url: str
    log_level: str


def _load_settings() -> Settings:
    return Settings(
        ws_url=os.getenv("WS_URL", "wss://badi-public.crowdmonitor.ch:9591/api"),
        database_url=os.getenv(
            "DATABASE_URL", "postgresql://badi:badi@localhost:5432/badi"
        ),
        test_database_url=os.getenv(
            "TEST_DATABASE_URL", "postgresql://badi:badi@localhost:5433/badi_test"
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


settings = _load_settings()
