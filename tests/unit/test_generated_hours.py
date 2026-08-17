"""Agreement tests between committed source fragments and the generated file."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts import scrape_closures as sc

ROOT = Path(__file__).parents[2]
GENERATED = ROOT / "ml" / "data" / "opening_hours.generated.json"
SOURCES = ROOT / "ml" / "data" / "sources"

LIVE_PROSE = """
<h2>Revisionsarbeiten</h2>
<p>Aufgrund von Revisionsarbeiten ist das Hallenbad Oerlikon bis 23.. August gechlossen.</p>
Mehr zum Thema
"""

ABBREVIATED_START = """
<h2>Revisionsarbeiten</h2>
<ul>
  <li>Oerlikon: Sonntag, 2., bis und mit Sonntag, 23. August</li>
</ul>
Mehr zum Thema
"""

EMPTY_SECTION = """
<h2>Revisionsarbeiten</h2>
<p>Derzeit keine Revisionsarbeiten.</p>
Mehr zum Thema
"""

NAMED_UNPARSED = """
<h2>Revisionsarbeiten</h2>
<p>Oerlikon bleibt geschlossen — Angaben folgen.</p>
Mehr zum Thema
"""

MISSING_SECTION = """
<h2>Öffnungszeiten</h2>
<p>Keine Angaben auf dieser Seite.</p>
"""


@pytest.fixture
def generated():
    assert GENERATED.exists(), "opening_hours.generated.json must exist"
    return json.loads(GENERATED.read_text(encoding="utf-8"))


def test_generated_has_revision_closures(generated):
    by_uid = {p["uid"]: p for p in generated["pools"]}
    oerlikon = by_uid["SSD-7"]
    revision = next(c for c in oerlikon["closures"] if c.get("reason") == "Revision")
    assert revision["from"].startswith("2026-08-02")
    assert revision["to"].startswith("2026-08-24")
    assert revision["extracted_by"] == "deterministic"


def test_model_closures_are_cited(generated):
    """Every model-extracted closure must cite a sentence."""
    for pool in generated["pools"]:
        for closure in pool.get("closures") or []:
            if closure.get("extracted_by") == "model":
                assert closure.get("cited_sentence"), (
                    f"{pool['uid']} model closure missing cited_sentence"
                )
                # If a source fragment exists, the sentence must appear in it
                fragment_path = SOURCES / f"{pool['uid']}.json"
                if fragment_path.exists():
                    text = fragment_path.read_text(encoding="utf-8")
                    assert closure["cited_sentence"] in text, (
                        f"{pool['uid']}: cited_sentence not found in fragment"
                    )


def test_revision_closures_have_deterministic_extractor(generated):
    for pool in generated["pools"]:
        for closure in pool.get("closures") or []:
            if closure.get("reason") == "Revision":
                assert closure.get("extracted_by") == "deterministic"


def test_scrape_closures_parser():
    sample = """
    <h2>Revisionsarbeiten</h2>
    <ul>
      <li>Altstetten: Donnerstag, 30. Juli, bis und mit Sonntag, 16. August</li>
      <li>City: Samstag, 4. Juli, bis und mit Freitag, 7. August</li>
      <li>Oerlikon: Sonntag, 2., bis und mit Sonntag, 23. August</li>
    </ul>
    Mehr zum Thema
    """
    closures = sc.parse_revision_lines(sample, year=2026)
    by_uid = {c["uid"]: c for c in closures}
    assert "SSD-1" in by_uid
    assert "SSD-4" in by_uid
    assert by_uid["SSD-4"]["from"].startswith("2026-07-04")
    assert by_uid["SSD-4"]["to"].startswith("2026-08-08")


def test_parser_live_prose_end_only_with_typos():
    closures = sc.parse_revision_lines(LIVE_PROSE, year=2026)
    assert len(closures) == 1
    closure = closures[0]
    assert closure["uid"] == "SSD-7"
    assert "from" not in closure
    assert closure["to"].startswith("2026-08-24")
    assert closure["extracted_by"] == "deterministic"
    assert closure["reason"] == "Revision"


def test_parser_abbreviated_start_inherits_end_month():
    closures = sc.parse_revision_lines(ABBREVIATED_START, year=2026)
    assert len(closures) == 1
    assert closures[0]["uid"] == "SSD-7"
    assert closures[0]["from"].startswith("2026-08-02")
    assert closures[0]["to"].startswith("2026-08-24")


def test_parser_recognized_empty_section():
    assert sc.parse_revision_lines(EMPTY_SECTION, year=2026) == []


def test_parser_named_but_unparsed_returns_empty():
    assert sc.parse_revision_lines(NAMED_UNPARSED, year=2026) == []


def _seed_generated(path: Path, pools: list[dict]) -> None:
    path.write_text(
        json.dumps({"pools": pools}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _revision(
    *,
    start: str = "2026-08-02T00:00:00+02:00",
    end: str = "2026-08-24T00:00:00+02:00",
) -> dict:
    return {
        "from": start,
        "to": end,
        "reason": "Revision",
        "scope": "full",
        "extracted_by": "deterministic",
        "cited_sentence": "Oerlikon: Sonntag, 2., bis und mit Sonntag, 23. August",
    }


def test_merge_preserves_existing_from_for_end_only(tmp_path, monkeypatch):
    generated = tmp_path / "opening_hours.generated.json"
    _seed_generated(
        generated,
        [{"uid": "SSD-7", "closures": [_revision()]}],
    )
    monkeypatch.setattr(sc, "GENERATED", generated)
    data = sc.merge_into_generated(
        [
            {
                "uid": "SSD-7",
                "to": "2026-08-24T00:00:00+02:00",
                "reason": "Revision",
                "scope": "full",
                "extracted_by": "deterministic",
                "cited_sentence": "Hallenbad Oerlikon bis 23. August gechlossen",
            }
        ],
        date(2026, 8, 17),
    )
    oerlikon = next(p for p in data["pools"] if p["uid"] == "SSD-7")
    rev = next(c for c in oerlikon["closures"] if c["reason"] == "Revision")
    assert rev["from"].startswith("2026-08-02")
    assert rev["to"].startswith("2026-08-24")


def test_merge_end_only_uses_scraped_at_when_no_existing_from(tmp_path, monkeypatch):
    generated = tmp_path / "opening_hours.generated.json"
    _seed_generated(generated, [{"uid": "SSD-7", "closures": []}])
    monkeypatch.setattr(sc, "GENERATED", generated)
    data = sc.merge_into_generated(
        [
            {
                "uid": "SSD-7",
                "to": "2026-08-24T00:00:00+02:00",
                "reason": "Revision",
                "scope": "full",
                "extracted_by": "deterministic",
                "cited_sentence": "Hallenbad Oerlikon bis 23. August gechlossen",
            }
        ],
        date(2026, 8, 17),
    )
    oerlikon = next(p for p in data["pools"] if p["uid"] == "SSD-7")
    rev = next(c for c in oerlikon["closures"] if c["reason"] == "Revision")
    assert rev["from"].startswith("2026-08-17")


def test_merge_clears_revision_for_pools_not_in_current_set(tmp_path, monkeypatch):
    generated = tmp_path / "opening_hours.generated.json"
    event = {
        "from": "2026-08-20T00:00:00+02:00",
        "to": "2026-08-21T00:00:00+02:00",
        "reason": "Betriebsanlass",
        "scope": "full",
        "extracted_by": "model",
        "cited_sentence": "geschlossen wegen Betriebsanlass",
    }
    _seed_generated(
        generated,
        [
            {
                "uid": "SSD-1",
                "closures": [
                    _revision(
                        start="2026-07-30T00:00:00+02:00",
                        end="2026-08-17T00:00:00+02:00",
                    ),
                    event,
                ],
            },
            {"uid": "SSD-7", "closures": [_revision()]},
        ],
    )
    monkeypatch.setattr(sc, "GENERATED", generated)
    data = sc.merge_into_generated(
        [
            {
                "uid": "SSD-7",
                "from": "2026-08-02T00:00:00+02:00",
                "to": "2026-08-24T00:00:00+02:00",
                "reason": "Revision",
                "scope": "full",
                "extracted_by": "deterministic",
                "cited_sentence": "Oerlikon: Sonntag, 2., bis und mit Sonntag, 23. August",
            }
        ],
        date(2026, 8, 17),
    )
    by_uid = {p["uid"]: p for p in data["pools"]}
    assert [c["reason"] for c in by_uid["SSD-1"]["closures"]] == ["Betriebsanlass"]
    assert any(c["reason"] == "Revision" for c in by_uid["SSD-7"]["closures"])


def test_merge_empty_recognized_section_clears_all_revisions(tmp_path, monkeypatch):
    generated = tmp_path / "opening_hours.generated.json"
    _seed_generated(
        generated,
        [
            {"uid": "SSD-4", "closures": [_revision()]},
            {"uid": "SSD-7", "closures": [_revision()]},
        ],
    )
    monkeypatch.setattr(sc, "GENERATED", generated)
    data = sc.merge_into_generated([], date(2026, 8, 24))
    for pool in data["pools"]:
        assert not any(
            c.get("reason") == "Revision" for c in pool.get("closures") or []
        )


def _run_main(monkeypatch, tmp_path: Path, html: str) -> int:
    generated = tmp_path / "opening_hours.generated.json"
    sources = tmp_path / "sources"
    if not generated.exists():
        _seed_generated(generated, [{"uid": "SSD-7", "closures": [_revision()]}])
    monkeypatch.setattr(sc, "GENERATED", generated)
    monkeypatch.setattr(sc, "SOURCES", sources)
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["scrape_closures.py", "--from-file", str(page), "--year", "2026"],
    )
    return sc.main()


def test_main_recognized_empty_exits_0_and_writes(tmp_path, monkeypatch):
    generated = tmp_path / "opening_hours.generated.json"
    _seed_generated(generated, [{"uid": "SSD-7", "closures": [_revision()]}])
    code = _run_main(monkeypatch, tmp_path, EMPTY_SECTION)
    assert code == 0
    data = json.loads(generated.read_text(encoding="utf-8"))
    assert not any(
        c.get("reason") == "Revision"
        for p in data["pools"]
        for c in p.get("closures") or []
    )


def test_main_named_but_unparsed_exits_1(tmp_path, monkeypatch):
    assert _run_main(monkeypatch, tmp_path, NAMED_UNPARSED) == 1


def test_main_missing_section_exits_1(tmp_path, monkeypatch):
    assert _run_main(monkeypatch, tmp_path, MISSING_SECTION) == 1
