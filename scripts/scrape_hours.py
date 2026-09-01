#!/usr/bin/env python3
"""Scrape Stadt Zürich pool opening-hours tables into the generated data file.

Deterministic parse of ``stzh-datatable`` rows attributes. Writes source
fragments under ``ml/data/sources/`` and merges periods into
``ml/data/opening_hours.generated.json``. Event-closure prose extraction is
left as a separate, reviewed step (see ``--extract-closures`` stub).
"""

from __future__ import annotations

import argparse
import calendar
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
TIME_RE = re.compile(r"(\d{1,2})(?:[.:](\d{2}))?\s*[–\-]\s*(\d{1,2})(?:[.:](\d{2}))?")


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


def _parse_month_range(prose: str, year: int) -> list[tuple[date, date]] | None:
    """Parse 'Mai–September' / '(Oktober–April)' into inclusive date ranges.

    Year-wrapping ranges (Oktober–April) split into two same-year windows.
    """
    prose = prose.strip().lower().replace("–", "-").replace("—", "-")
    prose = prose.strip("()").strip()
    match = re.fullmatch(r"([a-zäöü]+)\s*-\s*([a-zäöü]+)", prose)
    if not match:
        return None
    start_month = MONTHS.get(match.group(1))
    end_month = MONTHS.get(match.group(2))
    if not start_month or not end_month:
        return None

    def _month_end(month: int) -> date:
        return date(year, month, calendar.monthrange(year, month)[1])

    if start_month <= end_month:
        return [(date(year, start_month, 1), _month_end(end_month))]
    return [
        (date(year, start_month, 1), _month_end(12)),
        (date(year, 1, 1), _month_end(end_month)),
    ]


def _parse_cell_fragments(cell: str, year: int) -> list[dict]:
    """Split a table cell on ``|`` and classify each time fragment.

    Trailing prose is either a month range (``date_bounds``) or a Session
    label. Empty fragments (trailing pipes) are dropped.
    """
    fragments: list[dict] = []
    for raw in cell.split("|"):
        piece = raw.strip()
        if not piece:
            continue
        match = TIME_RE.search(piece)
        if not match:
            continue
        open_s = _hhmm(match.group(1), match.group(2))
        close_s = _hhmm(match.group(3), match.group(4))
        if not (open_s < close_s or close_s == "24:00"):
            continue
        rest = piece[match.end() :].strip()
        rest = re.sub(r"^(uhr)\s*", "", rest, flags=re.I).strip()
        rest = rest.lstrip(":").strip()
        rest = re.sub(r"\d+$", "", rest).strip()
        fragment: dict = {"open": open_s, "close": close_s, "condition": "always"}
        bounds = _parse_month_range(rest, year) if rest else None
        if bounds:
            fragment["date_bounds"] = bounds
        elif rest:
            fragment["label"] = rest
        fragments.append(fragment)
    return fragments


def _parse_time_ranges(cell: str) -> list[dict]:
    """Extract open/close pairs from a cell, ignoring prose annotations."""
    ranges = []
    for fragment in _parse_cell_fragments(cell, year=2000):
        ranges.append(
            {
                "open": fragment["open"],
                "close": fragment["close"],
                "condition": "always",
            }
        )
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
            i = next(
                i for i, k in enumerate(keys) if a.startswith(k[:2]) or a.startswith(k)
            )
            j = next(
                i for i, k in enumerate(keys) if b.startswith(k[:2]) or b.startswith(k)
            )
            return [DAY_MAP[keys[x]] for x in range(i, j + 1)]
        except StopIteration:
            pass
    # Comma lists ("Samstag, Sonntag") before a startswith early-return,
    # otherwise "samstag, sonntag" collapses to Saturday only.
    found = []
    for full, short in DAY_MAP.items():
        if full in label or full[:2] + "." in label:
            found.append(short)
    if found:
        return found
    for full, short in DAY_MAP.items():
        if label.startswith(full) or label.startswith(full[:2]):
            return [short]
    return []


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
                (
                    i
                    for i, c in enumerate(cols)
                    if "jedem wetter" in c or c == "öffnungszeiten"
                ),
                None,
            )
            idx_fair = next(
                (
                    i
                    for i, c in enumerate(cols)
                    if "schönem wetter" in c or "schoenem" in c
                ),
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
                fragments = _parse_cell_fragments(cell, year)
                if not fragments:
                    continue
                grouped: dict[tuple[str | None, str | None], list[dict]] = {}
                for fragment in fragments:
                    interval = {
                        "open": fragment["open"],
                        "close": fragment["close"],
                        "condition": fragment.get("condition", "always"),
                    }
                    if fragment.get("label"):
                        interval["label"] = fragment["label"]
                    bounds = fragment.get("date_bounds")
                    if bounds:
                        keys = [
                            (start.isoformat(), end.isoformat())
                            for start, end in bounds
                        ]
                    else:
                        keys = [(None, None)]
                    for key in keys:
                        grouped.setdefault(key, []).append(dict(interval))
                for (start, end), intervals in grouped.items():
                    periods.append(
                        {
                            "from": start,
                            "to": end,
                            "days": days,
                            "intervals": intervals,
                        }
                    )
    return periods


def generated_source_layout(uid: str, generated_path: Path | None = None) -> str | None:
    """Return the committed Schedule source layout for *uid*, if any."""
    path = generated_path or GENERATED
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    for pool in data.get("pools", []):
        if pool.get("uid") == uid:
            return (pool.get("source") or {}).get("layout")
    return None


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


def merge_periods(
    uid: str, periods: list[dict], scraped_at: date, source_url: str
) -> None:
    if GENERATED.exists():
        data = json.loads(GENERATED.read_text(encoding="utf-8"))
    else:
        data = {"pools": []}
    by_uid = {p["uid"]: p for p in data.get("pools", [])}
    existing = by_uid.get(uid, {})
    if existing.get("periods") == periods:
        return
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
    data.setdefault("scraped_at", scraped_at.isoformat())
    GENERATED.parent.mkdir(parents=True, exist_ok=True)
    GENERATED.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--uid", help="Scrape a single pool uid")
    parser.add_argument(
        "--from-sources",
        action="store_true",
        help="Rebuild from committed ml/data/sources/*.json (no network)",
    )
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
        if generated_source_layout(pool["uid"]) == "operator_prose":
            print(f"[skip] {pool['uid']}: operator_prose schedule")
            continue

        if args.from_sources:
            src_path = SOURCES / f"{pool['uid']}.json"
            if not src_path.exists():
                print(f"[skip] {pool['uid']}: no committed source fragment")
                continue
            fragment = json.loads(src_path.read_text(encoding="utf-8"))
            page_url = fragment.get("url") or url
            tables = fragment.get("tables") or []
        else:
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
            tables = fragment["tables"]

        periods = tables_to_periods(tables, args.year)
        if not periods:
            print(f"[warn] {pool['uid']}: no periods parsed")
            continue
        if GENERATED.exists():
            existing = json.loads(GENERATED.read_text(encoding="utf-8"))
            current = next(
                (p for p in existing.get("pools", []) if p.get("uid") == pool["uid"]),
                None,
            )
            if current and current.get("periods") == periods:
                print(f"[ok] {pool['uid']}: unchanged")
                count += 1
                continue
        merge_periods(pool["uid"], periods, scraped_at, page_url)
        count += 1
        print(f"[ok] {pool['uid']}: {len(periods)} periods")

    print(f"Scraped {count} Zürich pools into {GENERATED}")
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
