"""HTML pool-detail helpers: related pools, hours summary, closed notice."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from api.city_display import CITY_DISPLAY
from api.prediction_days import _schedule_for_pool

logger = logging.getLogger(__name__)

# Geographic clusters used by the related-pools fallback. Cities not listed
# are treated as their own region (no cross-city peers available).
CITY_REGIONS: dict[str, set[str]] = {
    "zurich": {"zurich", "adliswil"},
    "adliswil": {"zurich", "adliswil"},
    "rotkreuz": {"rotkreuz", "hunenberg"},
    "hunenberg": {"rotkreuz", "hunenberg"},
}

# German plural labels per pool type, used as section heading when the
# related-pools fallback can only group by type (no city or region peers).
TYPE_LABELS_DE: dict[str, str] = {
    "freibad": "Freibäder",
    "hallenbad": "Hallenbäder",
    "strandbad": "Strandbäder",
    "seebad": "Seebäder",
}


def _compute_related_pools(
    current: dict, all_pools: list[dict], limit: int = 8
) -> tuple[list[dict], str]:
    """Return up to ``limit`` sibling pools and the scope tag for the heading."""
    current_uid = current.get("uid", "")
    city = current.get("city")
    pool_type = current.get("type")

    same_city = [
        p for p in all_pools if p.get("city") == city and p.get("uid") != current_uid
    ]

    if same_city:
        return _city_pools_with_rotation(current, same_city, limit), "city"

    region_cities = CITY_REGIONS.get(city, {city}) if city else set()
    region_peers = [
        p
        for p in all_pools
        if p.get("city") in region_cities and p.get("uid") != current_uid
    ]
    if region_peers:
        picked = _by_type_first(region_peers, pool_type, limit)
        if len(picked) < limit:
            picked = _topup_with_same_type(
                picked, all_pools, pool_type, current_uid, limit
            )
        return picked, "region"

    same_type = [
        p
        for p in all_pools
        if p.get("type") == pool_type and p.get("uid") != current_uid
    ]
    if same_type:
        ordered = sorted(same_type, key=lambda p: p.get("uid", ""))
        return ordered[:limit], "type"

    return [], "none"


def _topup_with_same_type(
    picked: list[dict],
    all_pools: list[dict],
    pool_type: str | None,
    current_uid: str,
    limit: int,
) -> list[dict]:
    """Append same-type pools from anywhere until ``limit`` is reached, no dupes."""
    already = {p.get("uid") for p in picked}
    extras = sorted(
        (
            p
            for p in all_pools
            if p.get("type") == pool_type
            and p.get("uid") != current_uid
            and p.get("uid") not in already
        ),
        key=lambda p: p.get("uid", ""),
    )
    return (picked + extras)[:limit]


def _by_type_first(
    candidates: list[dict], preferred_type: str | None, limit: int
) -> list[dict]:
    """Return up to ``limit`` candidates with same-type first, alpha-sorted within bucket."""
    same_type = sorted(
        (p for p in candidates if p.get("type") == preferred_type),
        key=lambda p: p.get("uid", ""),
    )
    other_type = sorted(
        (p for p in candidates if p.get("type") != preferred_type),
        key=lambda p: p.get("uid", ""),
    )
    return (same_type + other_type)[:limit]


def _city_pools_with_rotation(
    current: dict, same_city: list[dict], limit: int
) -> list[dict]:
    """Same-type-first ordering plus sliding-window rotation when siblings > limit."""
    ordered = _by_type_first(same_city, current.get("type"), len(same_city))
    if len(ordered) <= limit:
        return ordered
    current_uid = current.get("uid", "")
    city_uids_sorted = sorted(p.get("uid", "") for p in same_city + [current])
    try:
        offset = city_uids_sorted.index(current_uid)
    except ValueError:
        offset = 0
    n = len(ordered)
    return [ordered[(offset + i) % n] for i in range(limit)]


def _related_pools_label(scope: str, pool: dict) -> str:
    """Pick the German section heading for the related-pools block."""
    if scope == "city":
        city = pool.get("city", "")
        return f"Weitere Bäder in {CITY_DISPLAY.get(city, city.title())}"
    if scope == "region":
        return "Weitere Bäder in der Nähe"
    if scope == "type":
        type_label = TYPE_LABELS_DE.get(pool.get("type", ""), "Bäder")
        return f"Andere {type_label}"
    return ""


def _build_opening_hours_summary(opening_hours: dict | None) -> str | None:
    """Build a compact German summary of opening hours, grouping days with identical times."""
    if not opening_hours:
        return None
    schedule = opening_hours.get("schedule")
    if not schedule:
        return None

    _DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _DAY_DE = {
        "Mon": "Mo",
        "Tue": "Di",
        "Wed": "Mi",
        "Thu": "Do",
        "Fri": "Fr",
        "Sat": "Sa",
        "Sun": "So",
    }

    entries = []
    for day in _DAY_ORDER:
        s = schedule.get(day)
        if s:
            entries.append((day, s["open"], s["close"]))

    if not entries:
        return None

    groups = []
    i = 0
    while i < len(entries):
        day, open_t, close_t = entries[i]
        j = i + 1
        while j < len(entries) and entries[j][1] == open_t and entries[j][2] == close_t:
            idx_prev = _DAY_ORDER.index(entries[j - 1][0])
            idx_curr = _DAY_ORDER.index(entries[j][0])
            if idx_curr == idx_prev + 1:
                j += 1
            else:
                break
        start_day = _DAY_DE[entries[i][0]]
        end_day = _DAY_DE[entries[j - 1][0]]
        if start_day == end_day:
            label = start_day
        else:
            label = f"{start_day}–{end_day}"
        groups.append(f"{label}: {open_t}–{close_t} Uhr")
        i = j

    return ". ".join(groups) + "."


def _detail_closed_notice(schedule, resolution, now_zurich: datetime) -> dict | None:
    """Banner payload for Revision / live Baditicker closes on the detail page."""
    from ml.opening_hours import DE_MONTHS, OpenState

    if resolution.is_open:
        return None
    if resolution.state == OpenState.CLOSED_EXCEPTION:
        for closure in schedule.closures:
            if closure.scope == "full" and closure.active_at(now_zurich):
                inclusive = (closure.end - timedelta(minutes=1)).date()
                return {
                    "reason": closure.reason,
                    "end_label": (f"{inclusive.day}. {DE_MONTHS[inclusive.month - 1]}"),
                }
        if resolution.reason:
            return {"reason": resolution.reason, "end_label": None}
        return None
    if resolution.state == OpenState.OBSERVED_CLOSED:
        return {
            "reason": resolution.reason or "Aktuell geschlossen",
            "end_label": None,
        }
    return None


def _pool_hours_for_markdown(
    pool: dict, *, when: datetime
) -> tuple[str | None, str | None]:
    """Return (summary, detail FAQ prose) for Markdown twins — schedule preferred."""
    opening_hours_summary = _build_opening_hours_summary(pool.get("opening_hours"))
    opening_hours_detail = None
    try:
        from ml.opening_hours import (
            opening_hours_faq_text,
            opening_hours_summary_from_schedule,
        )

        schedule = _schedule_for_pool(pool)
        if schedule is not None:
            schedule_summary = opening_hours_summary_from_schedule(
                schedule, when.date()
            )
            if schedule_summary:
                opening_hours_summary = schedule_summary
            opening_hours_detail = opening_hours_faq_text(
                schedule, pool["name"], when=when
            )
    except Exception:
        logger.warning(
            "Failed to load schedule hours for Markdown twin uid=%s",
            pool.get("uid"),
            exc_info=True,
        )
    return opening_hours_summary, opening_hours_detail
