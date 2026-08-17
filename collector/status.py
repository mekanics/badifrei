"""Baditicker status poller — outdoor open/closed observations."""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, field_validator

from collector.config import settings
from collector.db import write_status_batch

logger = logging.getLogger(__name__)

ZURICH_TZ = ZoneInfo("Europe/Zurich")

# German weekday prefixes that appear in Baditicker dateModified values,
# e.g. "Di., 04.08.2026 08:58". Never rely on locale-aware %a parsing.
_WEEKDAY_PREFIX = re.compile(
    r"^(?:Mo|Di|Mi|Do|Fr|Sa|So)\.?,?\s*",
    re.IGNORECASE,
)


class StatusReading(BaseModel):
    poiid: str
    title: str = ""
    status_text: str | None = None
    water_temp_c: float | None = None
    source_modified_at: datetime | None = None

    @field_validator("status_text", mode="before")
    @classmethod
    def empty_status_is_none(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("water_temp_c", mode="before")
    @classmethod
    def empty_temp_is_none(cls, v):
        if v is None or v == "":
            return None
        return float(v)


def parse_german_timestamp(raw: str | None) -> datetime | None:
    """Parse Baditicker dateModified into a Europe/Zurich-aware datetime.

    Strips the German weekday token, then parses ``%d.%m.%Y %H:%M``.
    Returns None for empty or unparseable values.
    """
    if not raw:
        return None
    cleaned = _WEEKDAY_PREFIX.sub("", raw.strip())
    if not cleaned:
        return None
    try:
        naive = datetime.strptime(cleaned, "%d.%m.%Y %H:%M")
    except ValueError:
        logger.warning("Unparseable Baditicker dateModified: %r", raw)
        return None
    return naive.replace(tzinfo=ZURICH_TZ)


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def parse_feed(xml: str) -> list[StatusReading]:
    """Parse Baditicker XML into StatusReading objects.

    Malformed individual baths are skipped; a completely broken document raises.
    """
    root = ET.fromstring(xml)
    readings: list[StatusReading] = []
    for bath in root.findall(".//bath"):
        poiid = _text(bath.find("poiid"))
        if not poiid:
            logger.warning("Skipping Baditicker bath with missing poiid")
            continue
        try:
            readings.append(
                StatusReading(
                    poiid=poiid,
                    title=_text(bath.find("title")),
                    status_text=_text(bath.find("openClosedTextPlain")) or None,
                    water_temp_c=_text(bath.find("temperatureWater")) or None,
                    source_modified_at=parse_german_timestamp(
                        _text(bath.find("dateModified"))
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping Baditicker bath %s: %s", poiid, exc)
    return readings


def build_poiid_to_uid(pools: list[dict]) -> dict[str, str]:
    """Map Baditicker poiid -> our pool_uid from metadata."""
    mapping: dict[str, str] = {}
    for pool in pools:
        poiid = pool.get("baditicker_poiid")
        if poiid:
            mapping[poiid] = pool["uid"]
    return mapping


async def fetch_feed(url: str | None = None) -> str:
    target = url or settings.baditicker_url
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(target)
        response.raise_for_status()
        return response.text


async def poll_once(
    pools: list[dict],
    *,
    fetch: Callable[[], asyncio.Future] | None = None,
) -> int:
    """Fetch, parse, map, and write one Baditicker snapshot. Returns rows written."""
    xml = await (fetch() if fetch is not None else fetch_feed())
    readings = parse_feed(xml)
    poiid_to_uid = build_poiid_to_uid(pools)
    mapped = []
    for reading in readings:
        uid = poiid_to_uid.get(reading.poiid)
        if uid is None:
            continue  # untracked pools (Schanzengraben, Au-Höngg, Katzensee, …) — not an error
        mapped.append(
            {
                "pool_uid": uid,
                "baditicker_poiid": reading.poiid,
                "status_text": reading.status_text,
                "water_temp_c": reading.water_temp_c,
                "source_modified_at": reading.source_modified_at,
            }
        )
    if not mapped:
        logger.debug("No Baditicker readings mapped to tracked pools")
        return 0
    return await write_status_batch(mapped)


async def run_status_poller(
    pools: list[dict],
    *,
    shutdown: asyncio.Event | None = None,
    on_success: Callable[[int], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    """Poll the Baditicker feed forever (or until *shutdown* is set).

    Errors are logged and reported via *on_error*; they never raise out of
    this loop so the occupancy collector can keep running.
    """
    interval = settings.status_poll_seconds
    backoff = 1.0
    logger.info(
        "Starting Baditicker status poller (interval=%ss, url=%s)",
        interval,
        settings.baditicker_url,
    )
    while True:
        if shutdown is not None and shutdown.is_set():
            break
        try:
            written = await poll_once(pools)
            if on_success is not None:
                on_success(written)
            backoff = 1.0
            logger.debug("Baditicker poll wrote %s status rows", written)
        except Exception as exc:  # noqa: BLE001
            logger.error("Baditicker poll failed: %s", exc)
            if on_error is not None:
                on_error(exc)
            await asyncio.sleep(min(backoff, interval))
            backoff = min(backoff * 2, 60.0)
            continue

        # Sleep in short slices so SIGTERM can interrupt cleanly.
        slept = 0.0
        while slept < interval:
            if shutdown is not None and shutdown.is_set():
                return
            step = min(1.0, interval - slept)
            await asyncio.sleep(step)
            slept += step
