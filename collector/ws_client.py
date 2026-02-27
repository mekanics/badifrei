"""WebSocket client for badi occupancy API."""
import asyncio
import json
import logging
from typing import AsyncGenerator

import websockets
from pydantic import BaseModel, field_validator

from collector.config import settings

logger = logging.getLogger(__name__)


class PoolReading(BaseModel):
    uid: str
    name: str
    currentfill: int
    maxspace: int
    freespace: int

    @field_validator("currentfill", mode="before")
    @classmethod
    def clamp_negative(cls, v):
        return max(0, int(v))

    @field_validator("freespace", mode="before")
    @classmethod
    def clamp_freespace(cls, v):
        return max(0, int(v))


def parse_message(raw: str) -> list[PoolReading]:
    """Parse raw WS message into list of PoolReading objects."""
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"Expected list, got {type(data)}")

    readings = []
    for item in data:
        if "uid" not in item or "currentfill" not in item:
            logger.warning("Skipping invalid pool record: missing uid or currentfill")
            continue
        readings.append(PoolReading(**item))
    return readings


async def connect_and_stream(ws_url: str = None) -> AsyncGenerator[list[PoolReading], None]:
    """
    Async generator that connects to the WS API and yields batches of PoolReading.
    Reconnects automatically with exponential backoff.
    """
    url = ws_url or settings.ws_url
    backoff = 1.0

    while True:
        try:
            logger.info(f"Connecting to {url}")
            async with websockets.connect(url) as ws:
                logger.info("Connected. Requesting all pool data...")
                await asyncio.sleep(0.5)
                await ws.send("all")
                backoff = 1.0  # reset on successful connection

                async for message in ws:
                    try:
                        readings = parse_message(message)
                        yield readings
                    except (ValueError, json.JSONDecodeError) as e:
                        logger.warning(f"Failed to parse message: {e}")

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"Connection closed: {e}. Reconnecting in {backoff}s...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}. Reconnecting in {backoff}s...")

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60.0)
