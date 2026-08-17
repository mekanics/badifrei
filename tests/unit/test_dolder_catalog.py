"""Freibad Dolder catalog + 2026 Schedule (operator prose, not Stadt tables)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts import scrape_closures as sc
from scripts.scrape_hours import generated_source_layout
from ml.opening_hours import OpenState, load_schedules, resolve

METADATA_PATH = Path(__file__).parents[2] / "ml" / "pool_metadata.json"
ZURICH = ZoneInfo("Europe/Zurich")


def _when(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZURICH)


def test_dolder_metadata_maps_baditicker_and_crowdmonitor():
    pools = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    dolder = next(p for p in pools if p["uid"] == "SSD-13")
    assert dolder["baditicker_poiid"] == "fb016"
    assert dolder["name"] == "Freibad Dolder"
    assert dolder["type"] == "freibad"
    assert dolder["city"] == "zurich"
    assert dolder["seasonal"] is True
    assert "dolder.html" in dolder["official_url"]
    oh = dolder["opening_hours"]
    assert oh["seasonal_open"] == "2026-06-19"
    assert oh["seasonal_close"] == "2026-09-13"
    assert oh["schedule"]["Mon"]["open"] == "11:30"
    assert oh["schedule"]["Tue"]["open"] == "10:00"


def test_dolder_monday_opens_at_1130():
    schedule = load_schedules()["SSD-13"]
    before = resolve(schedule, _when(2026, 8, 17, 11, 29))
    assert before.is_open is False
    open_now = resolve(schedule, _when(2026, 8, 17, 11, 30))
    assert open_now.is_open is True
    assert open_now.state == OpenState.OPEN_GUARANTEED


def test_dolder_tuesday_opens_at_1000():
    schedule = load_schedules()["SSD-13"]
    before = resolve(schedule, _when(2026, 8, 18, 9, 59))
    assert before.is_open is False
    open_now = resolve(schedule, _when(2026, 8, 18, 10, 0))
    assert open_now.is_open is True


def test_dolder_wellenkino_closes_1930():
    """31 Jul–16 Aug 2026: Wellenkino early close. Must not inherit 20:00."""
    schedule = load_schedules()["SSD-13"]
    result = resolve(schedule, _when(2026, 8, 10, 15, 0))
    assert result.is_open is True
    assert result.guaranteed_close.strftime("%H:%M") == "19:30"


def test_dolder_after_wellenkino_closes_2000():
    schedule = load_schedules()["SSD-13"]
    result = resolve(schedule, _when(2026, 8, 17, 15, 0))
    assert result.is_open is True
    assert result.guaranteed_close.strftime("%H:%M") == "20:00"


def test_dolder_5_sep_is_full_closure():
    schedule = load_schedules()["SSD-13"]
    result = resolve(schedule, _when(2026, 9, 5, 14, 0))
    assert result.is_open is False
    assert result.state == OpenState.CLOSED_EXCEPTION


def test_hours_scraper_skips_dolder_operator_prose():
    """Stadt table scrape must not fetch or overwrite the operator Schedule."""
    assert generated_source_layout("SSD-13") == "operator_prose"


def test_closure_scrape_keeps_dolder_event_and_periods(tmp_path, monkeypatch):
    """Hours-sync Revision merge must not drop Dolder's event Closure or Periods."""
    generated = Path(__file__).parents[2] / "ml" / "data" / "opening_hours.generated.json"
    data = json.loads(generated.read_text(encoding="utf-8"))
    seed = tmp_path / "opening_hours.generated.json"
    seed.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sc, "GENERATED", seed)

    merged = sc.merge_into_generated(
        [
            {
                "uid": "SSD-7",
                "from": "2026-08-02T00:00:00+02:00",
                "to": "2026-08-24T00:00:00+02:00",
                "reason": "Revision",
                "scope": "full",
                "extracted_by": "deterministic",
                "cited_sentence": "Hallenbad Oerlikon bis 23. August gechlossen",
            }
        ],
        date(2026, 8, 17),
    )
    dolder = next(p for p in merged["pools"] if p["uid"] == "SSD-13")
    assert dolder["source"]["layout"] == "operator_prose"
    assert len(dolder["periods"]) == 6
    assert any(c.get("reason") == "Veranstaltung" for c in dolder["closures"])
