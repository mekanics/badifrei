"""Stadt Zürich hours-cell fragments — labels, month ranges, day lists."""

from __future__ import annotations

from datetime import date

from scripts.scrape_hours import (
    _days_from_label,
    _parse_cell_fragments,
    _parse_month_range,
    tables_to_periods,
)

OERLIKON_WED = "6–22 Uhr | 14–16 Uhr Kinderspielnachmittag"
BLAESI_WED = "9–19 Uhr | 14–16.30 Uhr Kinderspielnachmittag"
BLAESI_WEEKEND = "9–16 Uhr Mai–September | 9–18 Uhr Oktober–April"
BUNGERTWIES_WED = "12–14 Uhr | 14–16 Uhr Kinderspielnachmittag | 16–19 Uhr"
KAEFERBERG_TUE = (
    "6.30–9 Uhr | 11–15 Uhr | 15–16.30 Uhr: Nur für Personen, die schwimmen können1"
    " | 18–22 Uhr: Nur für Erwachsene"
)
KAEFERBERG_SAT = "9–16 Uhr (Mai–September) | 9–18 Uhr (Oktober–April)"
LEIMBACH_SAT = "9–18 Uhr | 9–14 Uhr Familienschwimmen | 14.30–18 Uhr Sportschwimmen"


def _interval(open_s: str, close_s: str, **extra) -> dict:
    out = {"open": open_s, "close": close_s, "condition": "always"}
    out.update(extra)
    return out


class TestParseCellFragments:
    def test_oerlikon_wednesday_kinderspiel_is_labeled(self):
        frags = _parse_cell_fragments(OERLIKON_WED, year=2026)
        assert frags[0] == _interval("06:00", "22:00")
        assert frags[1] == _interval("14:00", "16:00", label="Kinderspielnachmittag")
        assert "label" not in frags[0]
        assert "date_bounds" not in frags[0]
        assert "date_bounds" not in frags[1]

    def test_blaesi_wednesday_kinderspiel_half_hour(self):
        frags = _parse_cell_fragments(BLAESI_WED, year=2026)
        assert frags[1]["open"] == "14:00"
        assert frags[1]["close"] == "16:30"
        assert frags[1]["label"] == "Kinderspielnachmittag"

    def test_bungertwies_wednesday_kinderspiel_is_the_middle_span(self):
        frags = _parse_cell_fragments(BUNGERTWIES_WED, year=2026)
        assert [f["open"] for f in frags] == ["12:00", "14:00", "16:00"]
        assert frags[1]["label"] == "Kinderspielnachmittag"
        assert "label" not in frags[0]
        assert "label" not in frags[2]

    def test_leimbach_saturday_named_sessions(self):
        frags = _parse_cell_fragments(LEIMBACH_SAT, year=2026)
        assert frags[0] == _interval("09:00", "18:00")
        assert frags[1] == _interval("09:00", "14:00", label="Familienschwimmen")
        assert frags[2] == _interval("14:30", "18:00", label="Sportschwimmen")

    def test_kaeferberg_tuesday_strips_footnote_digits(self):
        frags = _parse_cell_fragments(KAEFERBERG_TUE, year=2026)
        assert len(frags) == 4
        assert frags[2]["label"] == "Nur für Personen, die schwimmen können"
        assert frags[3]["label"] == "Nur für Erwachsene"
        assert frags[2]["open"] == "15:00"
        assert frags[2]["close"] == "16:30"

    def test_kaeferberg_saturday_month_ranges_not_labels(self):
        frags = _parse_cell_fragments(KAEFERBERG_SAT, year=2026)
        assert "label" not in frags[0]
        assert "label" not in frags[1]
        assert frags[0]["date_bounds"] == [
            (date(2026, 5, 1), date(2026, 9, 30)),
        ]
        assert frags[1]["date_bounds"] == [
            (date(2026, 10, 1), date(2026, 12, 31)),
            (date(2026, 1, 1), date(2026, 4, 30)),
        ]
        assert frags[0]["close"] == "16:00"
        assert frags[1]["close"] == "18:00"

    def test_blaesi_weekend_month_ranges_without_parens(self):
        frags = _parse_cell_fragments(BLAESI_WEEKEND, year=2026)
        assert frags[0]["date_bounds"][0] == (date(2026, 5, 1), date(2026, 9, 30))
        assert len(frags[1]["date_bounds"]) == 2

    def test_trailing_pipe_is_ignored(self):
        frags = _parse_cell_fragments("6–21 Uhr |", year=2026)
        assert frags == [_interval("06:00", "21:00")]


class TestParseMonthRange:
    def test_mai_september_same_year(self):
        assert _parse_month_range("Mai–September", 2026) == [
            (date(2026, 5, 1), date(2026, 9, 30)),
        ]

    def test_oktober_april_wraps_into_two_ranges(self):
        assert _parse_month_range("Oktober–April", 2026) == [
            (date(2026, 10, 1), date(2026, 12, 31)),
            (date(2026, 1, 1), date(2026, 4, 30)),
        ]

    def test_parenthesized_range(self):
        assert _parse_month_range("(Mai–September)", 2026) == [
            (date(2026, 5, 1), date(2026, 9, 30)),
        ]

    def test_plain_label_is_not_a_month_range(self):
        assert _parse_month_range("Kinderspielnachmittag", 2026) is None


class TestDaysFromLabel:
    def test_comma_separated_saturday_sunday(self):
        assert _days_from_label("Samstag, Sonntag | (und Feiertage)") == [
            "Sat",
            "Sun",
        ]

    def test_single_weekday(self):
        assert _days_from_label("Mittwoch") == ["Wed"]

    def test_dash_range(self):
        assert _days_from_label("Montag–Sonntag") == [
            "Mon",
            "Tue",
            "Wed",
            "Thu",
            "Fri",
            "Sat",
            "Sun",
        ]


class TestTablesToPeriodsShapeB:
    def test_month_conditional_weekend_emits_dated_periods(self):
        periods = tables_to_periods(
            [
                {
                    "columns": ["Wochentag", "Zeit"],
                    "rows": [
                        [
                            "Samstag, Sonntag | (und Feiertage)",
                            BLAESI_WEEKEND,
                        ]
                    ],
                }
            ],
            year=2026,
        )
        by_bounds = {(p["from"], p["to"]): p for p in periods}
        assert set(by_bounds) == {
            ("2026-05-01", "2026-09-30"),
            ("2026-10-01", "2026-12-31"),
            ("2026-01-01", "2026-04-30"),
        }
        summer = by_bounds[("2026-05-01", "2026-09-30")]
        assert summer["days"] == ["Sat", "Sun"]
        assert summer["intervals"] == [_interval("09:00", "16:00")]
        winter = by_bounds[("2026-10-01", "2026-12-31")]
        assert winter["intervals"] == [_interval("09:00", "18:00")]
        assert (
            by_bounds[("2026-01-01", "2026-04-30")]["intervals"] == winter["intervals"]
        )

    def test_kinderspiel_stays_on_the_evergreen_wednesday_period(self):
        periods = tables_to_periods(
            [
                {
                    "columns": ["Wochentag", "Zeit"],
                    "rows": [["Mittwoch", OERLIKON_WED]],
                }
            ],
            year=2026,
        )
        assert len(periods) == 1
        assert periods[0]["from"] is None
        assert periods[0]["to"] is None
        assert periods[0]["days"] == ["Wed"]
        assert periods[0]["intervals"][1]["label"] == "Kinderspielnachmittag"
        assert periods[0]["intervals"][0] == _interval("06:00", "22:00")
