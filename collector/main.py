"""Badi occupancy collector — main entry point."""
import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from collector.config import settings
from collector.db import write_batch, close_pool
from collector.ws_client import connect_and_stream

# ── Deduplication state ────────────────────────────────────────────────────

_last_state: dict[str, int] = {}
_last_write_time: datetime | None = None


def should_write(
    readings: list,
    last_state: dict,
    last_write: datetime | None,
    force_interval_seconds: int = 900,
) -> bool:
    """Return True if readings should be persisted to the DB."""
    # Always write if no prior state
    if not last_state:
        return True
    # Force write if interval elapsed
    if last_write is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last_write).total_seconds()
    if elapsed >= force_interval_seconds:
        return True
    # Write if any fill changed or new pool appeared
    for r in readings:
        if last_state.get(r.uid) != r.currentfill:
            return True
    return False

# ── Structured JSON logging ────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        })


def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root.handlers = [handler]


logger = logging.getLogger(__name__)

# ── Metrics ────────────────────────────────────────────────────────────────

class Metrics:
    def __init__(self):
        self.records_written = 0
        self.errors = 0
        self.last_write: datetime | None = None
        self.running = False

metrics = Metrics()

# ── Health HTTP server ─────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "ok",
                "records_written": metrics.records_written,
                "errors": metrics.errors,
                "last_write": metrics.last_write.isoformat() if metrics.last_write else None,
                "running": metrics.running,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default HTTP server logs


def start_health_server(port: int = 8080):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health server on :{port}")
    return server

# ── Main collector loop ────────────────────────────────────────────────────

_shutdown = asyncio.Event()


def handle_sigterm(*_):
    logger.info("SIGTERM received, shutting down...")
    _shutdown.set()


async def run_collector():
    global _last_state, _last_write_time
    metrics.running = True
    logger.info("Starting collector...")

    async for readings in connect_and_stream():
        if _shutdown.is_set():
            break
        try:
            if should_write(readings, _last_state, _last_write_time):
                written = await write_batch(readings)
                metrics.records_written += written
                if written > 0:
                    metrics.last_write = datetime.now(timezone.utc)
                _last_write_time = datetime.now(timezone.utc)
                _last_state = {r.uid: r.currentfill for r in readings}
            else:
                logger.debug("Skipping DB write — no change detected")
        except Exception as e:
            metrics.errors += 1
            logger.error(f"DB write error: {e}")

    metrics.running = False
    await close_pool()
    logger.info("Collector stopped.")


def main():
    setup_logging()
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    health_port = int(os.getenv("HEALTH_PORT", "8080"))
    start_health_server(health_port)

    asyncio.run(run_collector())


if __name__ == "__main__":
    main()
