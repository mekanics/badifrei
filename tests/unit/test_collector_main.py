"""Unit tests for collector main module."""
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestJsonFormatter:
    def test_log_output_is_valid_json(self, capfd):
        from collector.main import setup_logging
        import logging
        setup_logging()
        logging.getLogger("test").info("hello world")
        # JSON logging goes to stderr
        # Just verify formatter doesn't crash and produces valid structure
        from collector.main import JsonFormatter
        import logging as lg
        formatter = JsonFormatter()
        record = lg.LogRecord("test", lg.INFO, "", 0, "test message", (), None)
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "test message"
        assert "timestamp" in parsed
        assert "logger" in parsed

    def test_log_output_json_keys(self):
        from collector.main import JsonFormatter
        import logging as lg
        formatter = JsonFormatter()
        record = lg.LogRecord("mymodule", lg.ERROR, "", 0, "oops", (), None)
        parsed = json.loads(formatter.format(record))
        assert set(parsed.keys()) >= {"timestamp", "level", "message", "logger"}


class TestMetrics:
    def test_initial_metrics(self):
        from collector.main import Metrics
        m = Metrics()
        assert m.records_written == 0
        assert m.errors == 0
        assert m.last_write is None
        assert not m.running

    def test_metrics_increment(self):
        from collector.main import Metrics
        m = Metrics()
        m.records_written += 22
        m.errors += 1
        assert m.records_written == 22
        assert m.errors == 1


class TestHealthEndpoint:
    def test_health_returns_json(self):
        import urllib.request
        from collector.main import start_health_server, metrics
        server = start_health_server(port=18080)
        try:
            with urllib.request.urlopen("http://localhost:18080/health") as resp:
                data = json.loads(resp.read())
            assert data["status"] == "ok"
            assert "records_written" in data
            assert "errors" in data
        finally:
            server.shutdown()


class TestRunCollector:
    async def test_writes_to_db_on_message(self):
        """Collector receives one batch and calls write_batch."""
        from collector.ws_client import PoolReading

        reading = PoolReading(uid="SSD-5", name="Kaeferberg", currentfill=27, maxspace=70, freespace=43)

        async def fake_stream(*args, **kwargs):
            yield [reading]
            # Then signal shutdown
            from collector import main as m
            m._shutdown.set()

        with patch("collector.main.connect_and_stream", fake_stream), \
             patch("collector.main.write_batch", new_callable=AsyncMock, return_value=1) as mock_write, \
             patch("collector.main.close_pool", new_callable=AsyncMock):
            from collector import main as m
            m._shutdown.clear()
            m.metrics.records_written = 0
            await m.run_collector()

        mock_write.assert_called_once()
        assert m.metrics.records_written == 1
