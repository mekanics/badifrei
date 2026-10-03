"""Per-process Schedule source: baked file, then a validated remote copy."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from ml.opening_hours import (
    GENERATED_PATH,
    METADATA_PATH,
    PoolSchedule,
    schedules_from_document,
)

logger = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = 1_000_000
FETCH_TIMEOUT_S = 10.0
MIN_INTERVAL_S = 60

Outcome = Literal["updated", "unchanged", "rejected", "fetch_failed"]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


HttpGet = Callable[[str], HttpResponse]


@dataclass(frozen=True)
class ScheduleSnapshot:
    schedules: Mapping[str, PoolSchedule]
    origin: Literal["baked", "remote"]
    content_sha256: str
    loaded_at: dt.datetime


def _capped_body(
    chunks: Iterable[bytes], *, content_length: str | None = None
) -> bytes:
    """Read at most MAX_DOCUMENT_BYTES + 1; skip the stream when length is over."""
    if content_length is not None:
        try:
            if int(content_length) > MAX_DOCUMENT_BYTES:
                return b"x" * (MAX_DOCUMENT_BYTES + 1)
        except ValueError:
            pass
    parts: list[bytes] = []
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > MAX_DOCUMENT_BYTES:
            return b"x" * (MAX_DOCUMENT_BYTES + 1)
        parts.append(chunk)
    return b"".join(parts)


def _default_http_get(
    url: str, *, transport: httpx.BaseTransport | None = None
) -> HttpResponse:
    with httpx.Client(
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
        transport=transport,
    ) as client:
        with client.stream("GET", url) as response:
            body = _capped_body(
                response.iter_bytes(),
                content_length=response.headers.get("content-length"),
            )
            return HttpResponse(status=response.status_code, body=body)


def _default_clock() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ScheduleSource:
    def __init__(
        self,
        *,
        baked_path: Path = GENERATED_PATH,
        metadata_path: Path = METADATA_PATH,
        http_get: HttpGet | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._baked_path = baked_path
        self._metadata_path = metadata_path
        self._http_get = http_get or _default_http_get
        self._clock = clock or _default_clock
        self._snapshot: ScheduleSnapshot | None = None
        self._last_refresh_at: dt.datetime | None = None
        self._last_result: Outcome | None = None
        self._last_error: str | None = None

    def current(self) -> ScheduleSnapshot:
        if self._snapshot is None:
            self._snapshot = self._load_baked()
        return self._snapshot

    def refresh(self, url: str) -> Outcome:
        try:
            response = self._http_get(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Hours fetch failed: %s", exc)
            return self._record("fetch_failed", str(exc))
        if response.status != 200:
            logger.warning("Hours fetch failed: HTTP %s", response.status)
            return self._record("fetch_failed", f"HTTP {response.status}")
        if len(response.body) > MAX_DOCUMENT_BYTES:
            logger.error(
                "Hours document rejected: exceeds %s bytes", MAX_DOCUMENT_BYTES
            )
            return self._record("rejected", "document exceeds MAX_DOCUMENT_BYTES")
        current = self.current()
        digest = hashlib.sha256(response.body).hexdigest()
        if digest == current.content_sha256:
            return self._record("unchanged")
        try:
            doc = json.loads(response.body)
        except Exception as exc:  # noqa: BLE001
            logger.error("Hours document rejected: %s", exc)
            return self._record("rejected", str(exc))
        if (
            not isinstance(doc, dict)
            or not isinstance(doc.get("pools"), list)
            or not doc["pools"]
        ):
            logger.error(
                "Hours document rejected: expected object with non-empty pools"
            )
            return self._record("rejected", "expected object with non-empty pools list")
        try:
            schedules = schedules_from_document(doc, self._metadata_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Hours document rejected: %s", exc)
            return self._record("rejected", str(exc))
        self._snapshot = ScheduleSnapshot(
            schedules=schedules,
            origin="remote",
            content_sha256=digest,
            loaded_at=self._clock(),
        )
        return self._record("updated")

    def status(self) -> dict:
        snap = self.current()
        return {
            "origin": snap.origin,
            "content_sha256": snap.content_sha256[:12],
            "loaded_at": snap.loaded_at.isoformat(),
            "last_refresh_at": (
                self._last_refresh_at.isoformat() if self._last_refresh_at else None
            ),
            "last_result": self._last_result,
            "last_error": self._last_error,
        }

    def _load_baked(self) -> ScheduleSnapshot:
        body = self._baked_path.read_bytes() if self._baked_path.exists() else b"{}"
        raw = json.loads(body) if self._baked_path.exists() else None
        return ScheduleSnapshot(
            schedules=schedules_from_document(raw, self._metadata_path),
            origin="baked",
            content_sha256=hashlib.sha256(body).hexdigest(),
            loaded_at=self._clock(),
        )

    def _record(self, result: Outcome, error: str | None = None) -> Outcome:
        self._last_refresh_at = self._clock()
        self._last_result = result
        self._last_error = error
        return result


_default: ScheduleSource | None = None


def default_source() -> ScheduleSource:
    global _default
    if _default is None:
        _default = ScheduleSource()
    return _default


def current_schedules() -> Mapping[str, PoolSchedule]:
    return default_source().current().schedules


async def run_refresh_loop(source: ScheduleSource, url: str, interval_s: float) -> None:
    while True:
        await asyncio.to_thread(source.refresh, url)
        await asyncio.sleep(interval_s)
