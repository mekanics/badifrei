"""Pool detail JSON-LD scripts must be valid JSON (eng-review matrix)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ml.features import load_pool_metadata
from ml.opening_hours import (
    load_schedules,
    opening_hours_faq_text,
    opening_hours_jsonld,
)

TEMPLATES = Path(__file__).resolve().parents[2] / "api" / "templates"
_SCRIPT_RE = re.compile(
    r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
    re.DOTALL,
)
_WHEN = datetime(2026, 8, 4, 12, 0, tzinfo=ZoneInfo("Europe/Zurich"))


def _render_pool_html(uid: str) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from api.main import _fmt_date_de, _static_ver

    pool = load_pool_metadata()[uid]
    schedule = load_schedules()[uid]
    hours_jsonld = opening_hours_jsonld(schedule)
    hours_faq = opening_hours_faq_text(schedule, pool["name"], when=_WHEN)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.globals["static_ver"] = _static_ver
    env.globals["umami_script_url"] = ""
    env.globals["umami_website_id"] = ""
    env.filters["date_de"] = _fmt_date_de

    return env.get_template("pool.html").render(
        pool=pool,
        schema_description=(
            f"{pool['type'].title()} in Zürich – aktuelle Auslastung "
            f"und Tagesprognose auf badifrei.ch."
        ),
        hours_jsonld=hours_jsonld,
        hours_faq=hours_faq,
        quietest_hour=10,
        related_pools=[],
        related_pools_heading="",
        active_closure=None,
        hours_confidence=schedule.confidence,
        hours_scraped_at=None,
        hours_periods_view=None,
        opening_hours_summary=None,
        weekly_insights=None,
        today_date="2026-08-04",
        today_prediction_status="ok",
        today_predictions_json="[]",
    )


def _ld_json_blocks(html: str) -> list[dict]:
    blocks = []
    for match in _SCRIPT_RE.finditer(html):
        blocks.append(json.loads(match.group(1)))
    return blocks


class TestPoolStructuredDataJson:
    def test_fb006_scripts_parse_and_guaranteed_only(self):
        html = _render_pool_html("fb006")
        blocks = _ld_json_blocks(html)
        assert len(blocks) >= 3  # WebSite + SportsActivityLocation + FAQ (+ breadcrumb)

        sports = next(b for b in blocks if b.get("@type") == "SportsActivityLocation")
        specs = sports["openingHoursSpecification"]
        assert specs
        open_specs = [s for s in specs if "dayOfWeek" in s]
        assert all(s["closes"] == "14:00:00" for s in open_specs)
        assert all(s["opens"].count(":") == 2 for s in open_specs)

        faq = next(b for b in blocks if b.get("@type") == "FAQPage")
        hours_q = next(q for q in faq["mainEntity"] if "geöffnet" in q["name"])
        answer = hours_q["acceptedAnswer"]["text"]
        assert "sicher" in answer
        assert "finden Sie" in answer

    def test_ssd4_scripts_parse_with_closure_spec(self):
        html = _render_pool_html("SSD-4")
        blocks = _ld_json_blocks(html)
        sports = next(b for b in blocks if b.get("@type") == "SportsActivityLocation")
        specs = sports["openingHoursSpecification"]
        closed = [
            s
            for s in specs
            if s.get("opens") == "00:00:00" and s.get("closes") == "00:00:00"
        ]
        assert closed
        # Every ld+json script on the page must parse (already done via _ld_json_blocks)
        assert any(b.get("@type") == "FAQPage" for b in blocks)
