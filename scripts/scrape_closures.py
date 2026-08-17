#!/usr/bin/env python3
"""Scrape the Stadt Zürich Revisionsarbeiten list into the generated hours file.

Deterministic parse of the central Hallenbäder overview page. The
Revisionsarbeiten section is the full current Revision set: pools not named
there lose their Revision closures. Periods and event closures are kept.
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

_NAME_ALT = (
    r"Altstetten|Bläsi|Blaesi|Bungertwies|City|Käferberg|Kaeferberg|"
    r"Leimbach|Oerlikon"
)
_MONTH_ALT = (
    r"Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|"
    r"September|Oktober|November|Dezember"
)
_WEEKDAY = r"[A-Za-zäöüÄÖÜ]+,\s+"

# "Altstetten: Donnerstag, 30. Juli, bis und mit Sonntag, 16. August"
# "Oerlikon: Sonntag, 2., bis und mit Sonntag, 23. August"
_RANGE_RE = re.compile(
    rf"(?P<name>{_NAME_ALT})\s*:\s*"
    rf"(?:{_WEEKDAY})?"
    rf"(?P<d1>\d{{1,2}})\.?(?:,)?"
    rf"(?:\s+(?P<m1>{_MONTH_ALT}))?"
    rf"\s*,?\s*"
    rf"bis(?:\s+und\s+mit)?\s+"
    rf"(?:{_WEEKDAY})?"
    rf"(?P<d2>\d{{1,2}})\.+(?:,)?"
    rf"\s+(?P<m2>{_MONTH_ALT})",
    re.IGNORECASE,
)

# "Hallenbad Oerlikon bis 23.. August gechlossen"
_END_ONLY_RE = re.compile(
    rf"(?:Hallenbad\s+)?(?P<name>{_NAME_ALT})\s*:?\s+"
    rf"bis(?:\s+und\s+mit)?\s+"
    rf"(?P<d2>\d{{1,2}})\.+\s+(?P<m2>{_MONTH_ALT})"
    rf"(?:\s+ge?chlossen)?",
    re.IGNORECASE,
)

_SECTION_RE = re.compile(r"Revisionsarbeiten(.*?)Mehr zum Thema", re.S | re.I)
_NAME_RE = re.compile(_NAME_ALT, re.IGNORECASE)


def _month(name: str) -> int:
    key = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    for m, num in MONTHS.items():
        if m.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue") == key:
            return num
        if m == name.lower():
            return num
    normalized = name.lower()
    if normalized in MONTHS:
        return MONTHS[normalized]
    raise ValueError(f"Unknown month: {name}")


def _uid_for(name: str) -> str:
    for key, uid in NAME_TO_UID.items():
        if key.lower() == name.lower():
            return uid
    raise KeyError(name)


def _exclusive_end(year: int, month: int, day: int) -> datetime:
    end_day = date(year, month, day) + timedelta(days=1)
    return datetime(end_day.year, end_day.month, end_day.day, 0, 0, tzinfo=ZURICH)


def _cited(match: re.Match[str]) -> str:
    return re.sub(r"\s+", " ", match.group(0)).strip()


def revision_section(html: str) -> str | None:
    """Return the Revisionsarbeiten body, or None if the markers are missing."""
    m = _SECTION_RE.search(html)
    return m.group(1) if m else None


def _plain_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "\n", text)
    return (
        text.replace("&auml;", "ä")
        .replace("&ouml;", "ö")
        .replace("&uuml;", "ü")
        .replace("&Auml;", "Ä")
        .replace("&Ouml;", "Ö")
        .replace("&Uuml;", "Ü")
        .replace("&nbsp;", " ")
    )


def named_pools_in_text(text: str) -> set[str]:
    return {m.group(0) for m in _NAME_RE.finditer(text)}


def parse_revision_lines(html: str, year: int | None = None) -> list[dict]:
    """Extract revision closures from the Hallenbäder overview HTML."""
    year = year or date.today().year
    section = revision_section(html)
    text = _plain_text(section if section is not None else html)

    by_uid: dict[str, dict] = {}
    for match in _RANGE_RE.finditer(text):
        m2 = _month(match.group("m2"))
        m1_raw = match.group("m1")
        m1 = _month(m1_raw) if m1_raw else m2
        d1 = int(match.group("d1"))
        d2 = int(match.group("d2"))
        start = datetime(year, m1, d1, 0, 0, tzinfo=ZURICH)
        uid = _uid_for(match.group("name"))
        by_uid[uid] = {
            "uid": uid,
            "from": start.isoformat(),
            "to": _exclusive_end(year, m2, d2).isoformat(),
            "reason": "Revision",
            "scope": "full",
            "extracted_by": "deterministic",
            "cited_sentence": _cited(match),
        }

    for match in _END_ONLY_RE.finditer(text):
        uid = _uid_for(match.group("name"))
        if uid in by_uid:
            continue
        m2 = _month(match.group("m2"))
        d2 = int(match.group("d2"))
        by_uid[uid] = {
            "uid": uid,
            "to": _exclusive_end(year, m2, d2).isoformat(),
            "reason": "Revision",
            "scope": "full",
            "extracted_by": "deterministic",
            "cited_sentence": _cited(match),
        }

    return list(by_uid.values())


def _scraped_midnight(scraped_at: date) -> str:
    return datetime(
        scraped_at.year, scraped_at.month, scraped_at.day, 0, 0, tzinfo=ZURICH
    ).isoformat()


def _revision_record(
    closure: dict, existing_closures: list[dict], scraped_at: date
) -> dict:
    start = closure.get("from")
    end = closure["to"]
    if not start:
        prior = next(
            (
                existing["from"]
                for existing in existing_closures
                if existing.get("reason") == "Revision" and existing.get("from")
            ),
            None,
        )
        start = prior if prior and prior < end else _scraped_midnight(scraped_at)
    return {
        "from": start,
        "to": end,
        "reason": closure.get("reason", "Revision"),
        "scope": closure.get("scope", "full"),
        "extracted_by": closure.get("extracted_by", "deterministic"),
        "cited_sentence": closure.get("cited_sentence"),
    }


def merge_into_generated(closures: list[dict], scraped_at: date) -> dict:
    if GENERATED.exists():
        data = json.loads(GENERATED.read_text(encoding="utf-8"))
    else:
        data = {"pools": []}

    by_uid = {p["uid"]: p for p in data.get("pools", [])}
    incoming = {c["uid"]: c for c in closures}

    for uid, entry in by_uid.items():
        previous = list(entry.get("closures") or [])
        had_revision = any(item.get("reason") == "Revision" for item in previous)
        kept = [item for item in previous if item.get("reason") != "Revision"]
        if uid in incoming:
            kept.append(_revision_record(incoming[uid], previous, scraped_at))
            entry["closures"] = kept
            entry["scraped_at"] = scraped_at.isoformat()
            entry["confidence"] = "official_structured"
        elif had_revision:
            entry["closures"] = kept
            entry["scraped_at"] = scraped_at.isoformat()

    for uid, closure in incoming.items():
        if uid in by_uid:
            continue
        by_uid[uid] = {
            "uid": uid,
            "confidence": "official_structured",
            "scraped_at": scraped_at.isoformat(),
            "periods": None,
            "closures": [_revision_record(closure, [], scraped_at)],
        }

    data["pools"] = list(by_uid.values())
    data["scraped_at"] = scraped_at.isoformat()
    data["source"] = {
        "url": DEFAULT_URL,
        "layout": "revisionsarbeiten",
        "confidence": "official_structured",
        "scraped_at": scraped_at.isoformat(),
    }
    return data


def classify_parse(html: str, closures: list[dict]) -> str | None:
    """Return an error message when the page cannot be trusted, else None."""
    section = revision_section(html)
    if section is None:
        return "ERROR: Revisionsarbeiten section missing"
    if closures:
        return None
    if named_pools_in_text(_plain_text(section)):
        return "ERROR: no revision closures parsed"
    return None


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
    error = classify_parse(html, closures)
    if error:
        print(error, flush=True)
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
