#!/usr/bin/env python3
"""Scrape the Stadt Zürich Revisionsarbeiten list into the generated hours file.

Deterministic parse of the central Hallenbäder overview page. Merges closures
into ``ml/data/opening_hours.generated.json`` without overwriting periods.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ZURICH = ZoneInfo("Europe/Zurich")
DEFAULT_URL = (
    "https://www.stadt-zuerich.ch/de/stadtleben/sport-und-erholung/"
    "sport-und-badeanlagen/hallenbaeder.html"
)
ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "ml" / "data" / "opening_hours.generated.json"
SOURCES = ROOT / "ml" / "data" / "sources"

# Name fragment (as published) -> our pool uid
NAME_TO_UID = {
    "Altstetten": "SSD-1",
    "Bläsi": "SSD-2",
    "Blaesi": "SSD-2",
    "Bungertwies": "SSD-3",
    "City": "SSD-4",
    "Käferberg": "SSD-5",
    "Kaeferberg": "SSD-5",
    "Leimbach": "SSD-6",
    "Oerlikon": "SSD-7",
}

MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

# "Altstetten: Donnerstag, 30. Juli, bis und mit Sonntag, 16. August"
_LINE_RE = re.compile(
    r"(?P<name>Altstetten|Bläsi|Blaesi|Bungertwies|City|Käferberg|Kaeferberg|"
    r"Leimbach|Oerlikon)\s*:\s*"
    r".*?(?P<d1>\d{1,2})\.?(?:,)?\s+(?P<m1>[A-Za-zäöüÄÖÜ]+)"
    r".*?bis und mit\s+.*?(?P<d2>\d{1,2})\.?(?:,)?\s+(?P<m2>[A-Za-zäöüÄÖÜ]+)",
    re.IGNORECASE | re.DOTALL,
)


def _month(name: str) -> int:
    key = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    # normalize back for lookup
    for m, num in MONTHS.items():
        if m.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue") == key:
            return num
        if m == name.lower():
            return num
    # direct
    normalized = name.lower()
    if normalized in MONTHS:
        return MONTHS[normalized]
    raise ValueError(f"Unknown month: {name}")


def parse_revision_lines(html: str, year: int | None = None) -> list[dict]:
    """Extract revision closures from the Hallenbäder overview HTML."""
    year = year or date.today().year
    # Prefer the Revisionsarbeiten section if present
    section = html
    m = re.search(r"Revisionsarbeiten(.*?)Mehr zum Thema", html, re.S | re.I)
    if m:
        section = m.group(1)

    # Decode common entities and strip tags for matching
    text = re.sub(r"<br\s*/?>", "\n", section)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = (
        text.replace("&auml;", "ä")
        .replace("&ouml;", "ö")
        .replace("&uuml;", "ü")
        .replace("&Auml;", "Ä")
        .replace("&Ouml;", "Ö")
        .replace("&Uuml;", "Ü")
        .replace("&nbsp;", " ")
    )

    closures: list[dict] = []
    for match in _LINE_RE.finditer(text):
        name = match.group("name")
        uid = NAME_TO_UID[name if name in NAME_TO_UID else name]
        m1 = _month(match.group("m1"))
        m2 = _month(match.group("m2"))
        d1 = int(match.group("d1"))
        d2 = int(match.group("d2"))
        start = datetime(year, m1, d1, 0, 0, tzinfo=ZURICH)
        # "bis und mit" → exclusive end is the next calendar day
        end_day = date(year, m2, d2) + timedelta(days=1)
        end = datetime(end_day.year, end_day.month, end_day.day, 0, 0, tzinfo=ZURICH)
        cited = re.sub(r"\s+", " ", match.group(0)).strip()
        closures.append(
            {
                "uid": uid,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "reason": "Revision",
                "scope": "full",
                "extracted_by": "deterministic",
                "cited_sentence": cited,
            }
        )
    return closures


def merge_into_generated(closures: list[dict], scraped_at: date) -> dict:
    if GENERATED.exists():
        data = json.loads(GENERATED.read_text(encoding="utf-8"))
    else:
        data = {"pools": []}

    by_uid = {p["uid"]: p for p in data.get("pools", [])}
    for c in closures:
        uid = c["uid"]
        entry = by_uid.setdefault(
            uid,
            {
                "uid": uid,
                "confidence": "official_structured",
                "scraped_at": scraped_at.isoformat(),
                "periods": None,
                "closures": [],
            },
        )
        # Replace Revision closures; keep event closures from other extractors
        kept = [
            existing
            for existing in entry.get("closures") or []
            if existing.get("reason") != "Revision"
        ]
        kept.append({k: v for k, v in c.items() if k != "uid"})
        entry["closures"] = kept
        entry["scraped_at"] = scraped_at.isoformat()
        entry["confidence"] = "official_structured"

    data["pools"] = list(by_uid.values())
    data["scraped_at"] = scraped_at.isoformat()
    data["source"] = {
        "url": DEFAULT_URL,
        "layout": "revisionsarbeiten",
        "confidence": "official_structured",
        "scraped_at": scraped_at.isoformat(),
    }
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument(
        "--from-file",
        type=Path,
        help="Parse a saved HTML file instead of fetching",
    )
    args = parser.parse_args()

    if args.from_file:
        html = args.from_file.read_text(encoding="utf-8")
    else:
        response = httpx.get(args.url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        html = response.text

    SOURCES.mkdir(parents=True, exist_ok=True)
    (SOURCES / "revisionsarbeiten.html").write_text(html, encoding="utf-8")

    closures = parse_revision_lines(html, year=args.year)
    if not closures:
        print("ERROR: no revision closures parsed", flush=True)
        return 1

    scraped_at = date.today()
    data = merge_into_generated(closures, scraped_at)
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    GENERATED.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(closures)} revision closures to {GENERATED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
