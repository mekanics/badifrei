"""Agreement tests between committed source fragments and the generated file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
GENERATED = ROOT / "ml" / "data" / "opening_hours.generated.json"
SOURCES = ROOT / "ml" / "data" / "sources"


@pytest.fixture
def generated():
    assert GENERATED.exists(), "opening_hours.generated.json must exist"
    return json.loads(GENERATED.read_text(encoding="utf-8"))


def test_generated_has_revision_closures(generated):
    by_uid = {p["uid"]: p for p in generated["pools"]}
    # City must be closed for Revision in summer 2026
    city = by_uid["SSD-4"]
    assert any(c.get("reason") == "Revision" for c in city["closures"])


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
    from scripts.scrape_closures import parse_revision_lines

    sample = """
    <h2>Revisionsarbeiten</h2>
    <ul>
      <li>Altstetten: Donnerstag, 30. Juli, bis und mit Sonntag, 16. August</li>
      <li>City: Samstag, 4. Juli, bis und mit Freitag, 7. August</li>
      <li>Oerlikon: Sonntag, 2., bis und mit Sonntag, 23. August</li>
    </ul>
    Mehr zum Thema
    """
    closures = parse_revision_lines(sample, year=2026)
    by_uid = {c["uid"]: c for c in closures}
    assert "SSD-1" in by_uid
    assert "SSD-4" in by_uid
    assert by_uid["SSD-4"]["from"].startswith("2026-07-04")
    assert by_uid["SSD-4"]["to"].startswith("2026-08-08")
