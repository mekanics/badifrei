#!/usr/bin/env python3
"""Scrape Stadt Zürich pool opening-hours tables into the generated data file.

Deterministic parse of ``stzh-datatable`` rows attributes. Writes source
fragments under ``ml/data/sources/`` and merges periods into
``ml/data/opening_hours.generated.json``. Event-closure prose extraction is
left as a separate, reviewed step (see ``--extract-closures`` stub).
"""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ZURICH = ZoneInfo("Europe/Zurich")
ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "ml" / "data" / "opening_hours.generated.json"
SOURCES = ROOT / "ml" / "data" / "sources"
METADATA = ROOT / "ml" / "pool_metadata.json"

BASE = (
    "https://www.stadt-zuerich.ch/de/stadtleben/sport-und-erholung/"
    "sport-und-badeanlagen"
)

DAY_MAP = {
    "montag": "Mon",
    "dienstag": "Tue",
    "mittwoch": "Wed",
    "donnerstag": "Thu",
    "freitag": "Fri",
    "samstag": "Sat",
    "sonntag": "Sun",
}
MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
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

TABLE_RE = re.compile(r"<stzh-datatable\b(.*?)</stzh-datatable>", re.S | re.I)
ATTR_RE = re.compile(r'\b(columns|rows)="([^"]*)"', re.S)
TIME_RE = re.compile(
    r"(\d{1,2})(?:[.:](\d{2}))?\s*[–\-]\s*(\d{1,2})(?:[.:](\d{2}))?"
)


def _strip(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " | ", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _hhmm(h: str, m: str | None) -> str:
    hour = int(h)
    minute = int(m) if m else 0
    if hour == 24:
        return "24:00"
    return f"{hour:02d}:{minute:02d}"


def _parse_time_ranges(cell: str) -> list[dict]:
    """Extract open/close pairs from a cell, ignoring prose annotations."""
    ranges = []
    for match in TIME_RE.finditer(cell):
        open_s = _hhmm(match.group(1), match.group(2))
        close_s = _hhmm(match.group(3), match.group(4))
        if open_s < close_s or close_s == "24:00":
            ranges.append({"open": open_s, "close": close_s, "condition": "always"})
    return ranges


def _parse_month_day(token: str, year: int) -> date | None:
    """Parse fragments like '9.', '29. Mai', '16. August'."""
    token = token.strip().lower()
    m = re.match(r"(\d{1,2})\.?(?:\s+([a-zäöü]+))?", token)
    if not m:
        return None
    day = int(m.group(1))
    month_name = m.group(2)
    if not month_name:
        return None
    month = MONTHS.get(month_name)
    if not month:
        return None
    return date(year, month, day)


def _parse_zeitraum(cell: str, year: int) -> tuple[date | None, date | None]:
    """Parse '9.–29. Mai' or '30. Mai–16. August' or '9. Mai–6. September'."""
    cell = _strip(cell).lower()
    # Normalize en-dash
    cell = cell.replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in cell.split("-")]
    if len(parts) != 2:
        return None, None
    left, right = parts
    end = _parse_month_day(right, year)
    # Left may lack a month ('9.') — inherit from right
    start = _parse_month_day(left, year)
    if start is None and end is not None:
        m = re.match(r"(\d{1,2})\.?", left.strip())
        if m:
            start = date(year, end.month, int(m.group(1)))
    return start, end


def _days_from_label(label: str) -> list[str]:
    label = _strip(label).lower()
    # "Montag–Sonntag" / "Freitag–Sonntag"
    label = label.replace("–", "-").replace("—", "-")
    # Drop footnotes / parentheses
    label = re.sub(r"\(.*?\)", "", label)
    label = re.sub(r"\d+", "", label).strip()
    if "-" in label:
        a, b = [p.strip() for p in label.split("-", 1)]
        keys = list(DAY_MAP.keys())
        try:
            i = next(i for i, k in enumerate(keys) if a.startswith(k[:2]) or a.startswith(k))
            j = next(i for i, k in enumerate(keys) if b.startswith(k[:2]) or b.startswith(k))
            return [DAY_MAP[keys[x]] for x in range(i, j + 1)]
        except StopIteration:
            pass
    for full, short in DAY_MAP.items():
        if label.startswith(full) or label.startswith(full[:2]):
            return [short]
    # "Samstag, Sonntag"
    found = []
    for full, short in DAY_MAP.items():
        if full in label or full[:2] + "." in label:
            found.append(short)
    return found


def extract_tables(html_text: str) -> list[dict]:
    """Return parsed Öffnungszeiten tables from a pool page."""
    tables = []
    for block in TABLE_RE.findall(html_text):
        if "ffnungszeit" not in block and "Wochentag" not in html.unescape(block):
            if "Zeitraum" not in html.unescape(block):
                continue
        attrs = dict(ATTR_RE.findall(block))
        if "rows" not in attrs or "columns" not in attrs:
            continue
        try:
            columns = json.loads(html.unescape(attrs["columns"]))
            rows = json.loads(html.unescape(attrs["rows"]))
        except json.JSONDecodeError:
            continue
        col_texts = [_strip(str(c.get("text", ""))) for c in columns]
        parsed_rows = []
        for row in rows:
            parsed_rows.append([_strip(str(c.get("value", ""))) for c in row])
        tables.append({"columns": col_texts, "rows": parsed_rows, "raw_rows": rows})
    return tables


def tables_to_periods(tables: list[dict], year: int) -> list[dict]:
    """Convert parsed tables into the generated periods shape."""
    periods: list[dict] = []
    for table in tables:
        cols = [c.lower() for c in table["columns"]]
        # Shape A: Zeitraum × weather
        if any("zeitraum" in c for c in cols):
            idx_period = next(i for i, c in enumerate(cols) if "zeitraum" in c)
            idx_always = next(
                (i for i, c in enumerate(cols) if "jedem wetter" in c or c == "öffnungszeiten"),
                None,
            )
            idx_fair = next(
                (i for i, c in enumerate(cols) if "schönem wetter" in c or "schoenem" in c),
                None,
            )
            for row in table["rows"]:
                start, end = _parse_zeitraum(row[idx_period], year)
                if not start or not end:
                    continue
                intervals = []
                if idx_always is not None and idx_always < len(row):
                    for r in _parse_time_ranges(row[idx_always]):
                        intervals.append(r)
                if idx_fair is not None and idx_fair < len(row):
                    for r in _parse_time_ranges(row[idx_fair]):
                        r = {**r, "condition": "fair_weather"}
                        intervals.append(r)
                if not intervals:
                    continue
                periods.append(
                    {
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                        "intervals": intervals,
                    }
                )
            continue

        # Shape B: Wochentag × Zeit
        if any("wochentag" in c for c in cols):
            idx_day = next(i for i, c in enumerate(cols) if "wochentag" in c)
            idx_time = next(
                (i for i, c in enumerate(cols) if "zeit" in c),
                idx_day + 1 if idx_day + 1 < len(cols) else None,
            )
            # Skip sauna / Anspruchsgruppe tables for v1
            if any("anspruch" in c for c in cols):
                continue
            for row in table["rows"]:
                days = _days_from_label(row[idx_day])
                if not days or idx_time is None or idx_time >= len(row):
                    continue
                cell = row[idx_time]
                if "kein öffentliches" in cell.lower():
                    continue
                ranges = _parse_time_ranges(cell)
                if not ranges:
                    continue
                periods.append(
                    {
                        "from": None,
                        "to": None,
                        "days": days,
                        "intervals": ranges,
                    }
                )
    return periods


def slug_from_official_url(url: str) -> tuple[str, str] | None:
    """Return (section, slug) for a stadt-zuerich.ch pool page."""
    m = re.search(
        r"/sport-und-badeanlagen/(hallenbaeder|sommerbaeder)/([^./]+)",
        url,
    )
    if not m:
        return None
    return m.group(1), m.group(2)


def fetch(url: str) -> str:
    response = httpx.get(url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def merge_periods(uid: str, periods: list[dict], scraped_at: date, source_url: str) -> None:
    if GENERATED.exists():
        data = json.loads(GENERATED.read_text(encoding="utf-8"))
    else:
        data = {"pools": []}
    by_uid = {p["uid"]: p for p in data.get("pools", [])}
    entry = by_uid.setdefault(uid, {"uid": uid, "closures": []})
    entry["periods"] = periods
    entry["scraped_at"] = scraped_at.isoformat()
    entry["confidence"] = "official_structured"
    entry["holidays_follow"] = "Sun"
    entry["last_entry_offset_min"] = 30
    entry["source"] = {
        "url": source_url,
        "layout": "stzh_datatable",
        "scraped_at": scraped_at.isoformat(),
        "confidence": "official_structured",
    }
    # Preserve closures
    entry.setdefault("closures", by_uid.get(uid, {}).get("closures", []))
    by_uid[uid] = entry
    data["pools"] = list(by_uid.values())
    data["scraped_at"] = scraped_at.isoformat()
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    GENERATED.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--uid", help="Scrape a single pool uid")
    args = parser.parse_args()

    pools = json.loads(METADATA.read_text(encoding="utf-8"))
    scraped_at = date.today()
    SOURCES.mkdir(parents=True, exist_ok=True)

    count = 0
    for pool in pools:
        if args.uid and pool["uid"] != args.uid:
            continue
        url = pool.get("official_url") or ""
        if "stadt-zuerich.ch" not in url:
            continue
        slug = slug_from_official_url(url)
        if not slug:
            print(f"[skip] {pool['uid']}: cannot derive slug from {url}")
            continue
        section, name = slug
        page_url = f"{BASE}/{section}/{name}.html"
        try:
            html_text = fetch(page_url)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {pool['uid']}: {exc}")
            return 1

        fragment = {
            "uid": pool["uid"],
            "url": page_url,
            "scraped_at": scraped_at.isoformat(),
            "tables": extract_tables(html_text),
        }
        (SOURCES / f"{pool['uid']}.json").write_text(
            json.dumps(fragment, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        periods = tables_to_periods(fragment["tables"], args.year)
        if not periods:
            print(f"[warn] {pool['uid']}: no periods parsed")
            continue
        merge_periods(pool["uid"], periods, scraped_at, page_url)
        count += 1
        print(f"[ok] {pool['uid']}: {len(periods)} periods")

    print(f"Scraped {count} Zürich pools into {GENERATED}")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
