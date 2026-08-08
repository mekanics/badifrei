"""Detail-page closed banner from Resolution (Revision + Baditicker)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from api.main import _detail_closed_notice
from ml.opening_hours import (
    Closure,
    Interval,
    Observation,
    OpenState,
    Period,
    PoolSchedule,
    resolve,
)

ZURICH = ZoneInfo("Europe/Zurich")


def _when(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ZURICH)


def _schedule(*, closures=()) -> PoolSchedule:
    return PoolSchedule(
        uid="test",
        periods=(
            Period(
                start=None,
                end=None,
                days=frozenset(range(7)),
                intervals=(Interval(9 * 60, 20 * 60, "always"),),
            ),
        ),
        closures=tuple(closures),
        confidence="official_structured",
        scraped_at=date(2026, 8, 4),
    )


class TestDetailClosedNotice:
    def test_revision_banner(self):
        closure = Closure(
            start=_when(2026, 7, 4, 0),
            end=_when(2026, 8, 8, 0),
            reason="Revision",
            scope="full",
        )
        schedule = _schedule(closures=(closure,))
        now = _when(2026, 8, 4, 12)
        resolution = resolve(schedule, now)
        notice = _detail_closed_notice(schedule, resolution, now)
        assert notice is not None
        assert notice["reason"] == "Revision"
        assert notice["end_label"] == "7. Aug"

    def test_observed_closed_banner(self):
        schedule = _schedule()
        now = _when(2026, 8, 4, 16)
        obs = Observation(
            observed_at=now,
            source_modified_at=_when(2026, 8, 4, 14),
            is_open=False,
        )
        resolution = resolve(schedule, now, observation=obs)
        assert resolution.state == OpenState.OBSERVED_CLOSED
        notice = _detail_closed_notice(schedule, resolution, now)
        assert notice is not None
        assert notice["end_label"] is None
        assert "geschlossen" in notice["reason"].lower()

    def test_open_has_no_banner(self):
        schedule = _schedule()
        now = _when(2026, 8, 4, 12)
        resolution = resolve(schedule, now)
        assert resolution.is_open is True
        assert _detail_closed_notice(schedule, resolution, now) is None
