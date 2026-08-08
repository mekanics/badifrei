"""Unit tests for agent-facing Markdown renderers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from api.city_display import CITY_DISPLAY
from api.markdown_surfaces import (
    HOME_MD_CACHE_MAX_AGE,
    POOL_MD_CACHE_MAX_AGE,
    adapt_faq_for_markdown,
    markdown_response,
    render_home_markdown,
    render_pool_markdown,
)

Z = ZoneInfo("Europe/Zurich")
AS_OF = datetime(2026, 8, 8, 14, 30, tzinfo=Z)
NOW = datetime(2026, 8, 8, 14, 30, tzinfo=Z)


def _pools():
    return [
        {"uid": "LETZI-1", "name": "Freibad Letzigraben", "city": "zurich"},
        {"uid": "MARZILI-1", "name": "Bern Marzili", "city": "bern"},
        {"uid": "fb006", "name": "Freibad Allenmoos", "city": "zurich"},
    ]


class TestRenderHomeMarkdown:
    def test_no_occupancy_numbers(self):
        body = render_home_markdown(pools=_pools(), as_of=AS_OF)
        assert "occupancy" not in body.lower()
        assert "Auslastung:" not in body
        assert (
            "%" not in body.split("##")[0]
        )  # blurb may mention % in prose? avoid any %
        # Harder: no guest/fill patterns
        assert "current_fill" not in body
        assert "Besucher" not in body

    def test_absolute_md_links_grouped_by_city(self):
        body = render_home_markdown(pools=_pools(), as_of=AS_OF)
        assert "https://badifrei.ch/bad/LETZI-1.md" in body
        assert "https://badifrei.ch/bad/fb006.md" in body
        assert "https://badifrei.ch/bad/MARZILI-1.md" in body
        assert "Zürich" in body
        assert "Bern" in body
        # Zürich section before Bern
        assert body.index("Zürich") < body.index("Bern")

    def test_links_to_llms_txt(self):
        body = render_home_markdown(pools=_pools(), as_of=AS_OF)
        assert "https://badifrei.ch/llms.txt" in body


class TestRenderPoolMarkdown:
    def test_includes_occupancy_when_present(self):
        occ = {
            "pool_uid": "LETZI-1",
            "current_fill": 400,
            "max_space": 1000,
            "occupancy_pct": 40,
            "time": NOW,
            "is_open": True,
            "state": "open_guaranteed",
            "reason": None,
        }
        preds = [0.0] * 14 + [40.0, 50.0, 60.0] + [0.0] * 7
        body = render_pool_markdown(
            pool={"uid": "LETZI-1", "name": "Freibad Letzigraben", "city": "zurich"},
            occupancy=occ,
            today_predictions=preds,
            prediction_status={
                "model_available": True,
                "prediction_status": "ok",
                "open_hours_count": 10,
            },
            as_of=AS_OF,
            now_zurich=NOW,
        )
        assert "40%" in body
        assert "400" in body
        assert "1000" in body
        assert "Freibad Letzigraben" in body

    def test_keine_daten_when_occupancy_null(self):
        occ = {
            "pool_uid": "SSD-4",
            "current_fill": None,
            "max_space": None,
            "occupancy_pct": None,
            "time": None,
            "is_open": False,
            "state": "closed_exception",
            "reason": "Revision",
        }
        body = render_pool_markdown(
            pool={"uid": "SSD-4", "name": "Hallenbad City", "city": "zurich"},
            occupancy=occ,
            today_predictions=[0.0] * 24,
            prediction_status={
                "model_available": True,
                "prediction_status": "closed_all_day",
                "open_hours_count": 0,
            },
            as_of=AS_OF,
            now_zurich=NOW,
        )
        assert "keine Daten" in body

    def test_forecast_omits_past_hours(self):
        preds = [10.0] * 24  # all hours "open"
        body = render_pool_markdown(
            pool={"uid": "LETZI-1", "name": "Freibad Letzigraben", "city": "zurich"},
            occupancy={
                "occupancy_pct": 20,
                "current_fill": 100,
                "max_space": 500,
                "time": NOW,
                "is_open": True,
                "state": "open_guaranteed",
                "reason": None,
            },
            today_predictions=preds,
            prediction_status={
                "model_available": True,
                "prediction_status": "ok",
                "open_hours_count": 24,
            },
            as_of=AS_OF,
            now_zurich=NOW,
        )
        assert "14:00" in body
        assert "13:00" not in body
        assert "00:00" not in body

    def test_no_model_explained(self):
        body = render_pool_markdown(
            pool={"uid": "LETZI-1", "name": "Freibad Letzigraben", "city": "zurich"},
            occupancy=None,
            today_predictions=[0.0] * 24,
            prediction_status={
                "model_available": False,
                "prediction_status": "no_model",
                "open_hours_count": 8,
            },
            as_of=AS_OF,
            now_zurich=NOW,
        )
        assert (
            "no_model" in body or "nicht verfügbar" in body.lower() or "Modell" in body
        )

    def test_off_season_explained(self):
        body = render_pool_markdown(
            pool={"uid": "LETZI-1", "name": "Freibad Letzigraben", "city": "zurich"},
            occupancy=None,
            today_predictions=[0.0] * 24,
            prediction_status={
                "model_available": True,
                "prediction_status": "off_season",
                "open_hours_count": 0,
            },
            as_of=AS_OF,
            now_zurich=NOW,
        )
        assert "Saison" in body or "off_season" in body

    def test_absolute_links(self):
        body = render_pool_markdown(
            pool={"uid": "LETZI-1", "name": "Freibad Letzigraben", "city": "zurich"},
            occupancy=None,
            today_predictions=[0.0] * 24,
            prediction_status={
                "model_available": True,
                "prediction_status": "ok",
                "open_hours_count": 1,
            },
            as_of=AS_OF,
            now_zurich=NOW,
        )
        assert "https://badifrei.ch/bad/LETZI-1" in body
        assert "https://badifrei.ch/bad/LETZI-1.md" in body
        assert "https://badifrei.ch/llms.txt" in body

    def test_includes_description_and_opening_hours(self):
        body = render_pool_markdown(
            pool={
                "uid": "fb006",
                "name": "Freibad Allenmoos",
                "city": "zurich",
                "type": "freibad",
                "seasonal": True,
                "description": "Beliebtes Freibad im Quartier Unterstrass.",
                "official_url": "https://example.ch/allenmoos",
            },
            occupancy=None,
            today_predictions=[0.0] * 24,
            prediction_status={
                "model_available": True,
                "prediction_status": "ok",
                "open_hours_count": 1,
            },
            as_of=AS_OF,
            now_zurich=NOW,
            opening_hours_summary="Heute: 09:00–20:00",
            opening_hours_detail=(
                "Freibad Allenmoos ist heute von 09:00 bis 20:00 geöffnet; "
                "aktuelle Hinweise findest du auf dieser Seite."
            ),
        )
        assert "## Profil" in body
        assert "Beliebtes Freibad im Quartier Unterstrass." in body
        assert "Typ: freibad" in body
        assert "https://example.ch/allenmoos" in body
        assert "## Öffnungszeiten" in body
        assert "Heute: 09:00–20:00" in body
        assert "09:00 bis 20:00" in body
        assert "auf dieser Seite" not in body
        assert "auf der HTML-Seite (https://badifrei.ch/bad/fb006)" in body
        assert CITY_DISPLAY["zurich"] == "Zürich"


class TestAdaptFaqForMarkdown:
    def test_rewrites_dieser_seite(self):
        out = adapt_faq_for_markdown(
            "Hinweise findest du auf dieser Seite.",
            html_url="https://badifrei.ch/bad/fb006",
        )
        assert out == (
            "Hinweise findest du auf der HTML-Seite " "(https://badifrei.ch/bad/fb006)."
        )

    def test_none_passthrough(self):
        assert adapt_faq_for_markdown(None, html_url="https://x") is None


class TestMarkdownResponse:
    def test_headers_pool(self):
        resp = markdown_response("# hi\n", max_age=POOL_MD_CACHE_MAX_AGE)
        assert resp.media_type.startswith("text/markdown")
        assert "charset=utf-8" in (resp.media_type or "")
        assert resp.headers["Cache-Control"] == "public, max-age=180"
        assert resp.headers["X-Robots-Tag"] == "noindex"

    def test_headers_home(self):
        resp = markdown_response("# hi\n", max_age=HOME_MD_CACHE_MAX_AGE)
        assert resp.headers["Cache-Control"] == "public, max-age=3600"
        assert resp.headers["X-Robots-Tag"] == "noindex"
