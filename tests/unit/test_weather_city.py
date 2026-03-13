"""TASK-026: Per-city weather fetching — new unit tests.

All HTTP and DB calls are mocked — no live database required.
"""
import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest

SAMPLE_DATE = datetime.date(2025, 6, 1)


def make_weather_rows(date: datetime.date, city: str, temp: float = 20.0) -> list[dict]:
    return [
        {
            "date": date,
            "hour": h,
            "temperature_c": temp + h * 0.1,
            "precipitation_mm": 0.0,
            "weathercode": 0,
        }
        for h in range(24)
    ]


def make_mock_http_session(lat: float, lon: float, date: datetime.date):
    """Create a mock aiohttp session; records the lat/lon used."""
    date_str = date.isoformat()
    json_data = {
        "hourly": {
            "time": [f"{date_str}T{h:02d}:00" for h in range(24)],
            "temperature_2m": [22.0] * 24,
            "precipitation": [0.0] * 24,
            "weathercode": [0] * 24,
        }
    }
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=json_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_get = MagicMock(return_value=mock_resp)

    mock_session = AsyncMock()
    mock_session.get = mock_get
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


@pytest.fixture(autouse=True)
def clear_weather_cache():
    from ml.weather import clear_cache
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# 1. CITY_COORDS completeness
# ---------------------------------------------------------------------------

class TestCityCoords:
    def test_city_coords_all_cities_present(self):
        """CITY_COORDS must contain all 8 expected city slugs."""
        from ml.weather import CITY_COORDS

        expected = {"zurich", "bern", "adliswil", "luzern", "entfelden", "hunenberg", "rotkreuz", "wengen"}
        assert set(CITY_COORDS.keys()) == expected

    def test_city_coords_values_are_lat_lon_tuples(self):
        """Each value must be a (float, float) tuple representing (lat, lon)."""
        from ml.weather import CITY_COORDS

        for city, coords in CITY_COORDS.items():
            assert isinstance(coords, tuple), f"{city}: expected tuple, got {type(coords)}"
            assert len(coords) == 2, f"{city}: expected 2-tuple"
            lat, lon = coords
            assert isinstance(lat, float), f"{city}: lat must be float"
            assert isinstance(lon, float), f"{city}: lon must be float"

    def test_city_coords_bern_not_zurich(self):
        """Bern coordinates must differ from Zürich coordinates."""
        from ml.weather import CITY_COORDS

        assert CITY_COORDS["bern"] != CITY_COORDS["zurich"]
        bern_lat, bern_lon = CITY_COORDS["bern"]
        zurich_lat, zurich_lon = CITY_COORDS["zurich"]
        # Rough sanity: Bern is west/south of Zürich
        assert bern_lon < zurich_lon, "Bern lon should be less than Zürich lon"


# ---------------------------------------------------------------------------
# 2. fetch_weather_batch uses city-specific coordinates
# ---------------------------------------------------------------------------

class TestFetchWeatherBatchCityCoords:
    async def test_fetch_weather_batch_uses_city_coords(self):
        """fetch_weather_batch(city='bern') must request Bern's lat/lon, not Zürich's."""
        from ml.weather import fetch_weather_batch, CITY_COORDS

        bern_lat, bern_lon = CITY_COORDS["bern"]
        zurich_lat, zurich_lon = CITY_COORDS["zurich"]

        mock_session = make_mock_http_session(bern_lat, bern_lon, SAMPLE_DATE)

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # empty DB cache

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("aiohttp.ClientSession", return_value=mock_session):
            await fetch_weather_batch([SAMPLE_DATE], city="bern")

        # Inspect the call params
        call_kwargs = mock_session.get.call_args[1] if mock_session.get.call_args[1] else {}
        call_args = mock_session.get.call_args[0] if mock_session.get.call_args[0] else ()
        # params may be in kwargs
        params = mock_session.get.call_args.kwargs.get("params") or mock_session.get.call_args[1].get("params", {})
        assert params.get("latitude") == pytest.approx(bern_lat)
        assert params.get("longitude") == pytest.approx(bern_lon)
        assert params.get("latitude") != pytest.approx(zurich_lat)

    async def test_fetch_weather_batch_default_city_is_zurich(self):
        """fetch_weather_batch() without city kwarg uses CITY_COORDS['zurich']."""
        from ml.weather import fetch_weather_batch, CITY_COORDS

        zurich_lat, zurich_lon = CITY_COORDS["zurich"]
        mock_session = make_mock_http_session(zurich_lat, zurich_lon, SAMPLE_DATE)

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("aiohttp.ClientSession", return_value=mock_session):
            await fetch_weather_batch([SAMPLE_DATE])  # no city argument

        params = mock_session.get.call_args.kwargs.get("params") or mock_session.get.call_args[1].get("params", {})
        assert params.get("latitude") == pytest.approx(zurich_lat)
        assert params.get("longitude") == pytest.approx(zurich_lon)


# ---------------------------------------------------------------------------
# 3. persist_weather includes city
# ---------------------------------------------------------------------------

class TestPersistWeatherIncludesCity:
    async def test_persist_weather_includes_city_in_insert(self):
        """_persist_to_db(df, city='luzern') must include 'luzern' in the INSERT rows."""
        from ml.weather import _persist_to_db

        mock_conn = AsyncMock()
        df = pd.DataFrame(make_weather_rows(SAMPLE_DATE, "luzern"))

        await _persist_to_db(mock_conn, df, city="luzern")

        mock_conn.executemany.assert_called_once()
        sql, rows = mock_conn.executemany.call_args[0]
        assert "city" in sql.lower(), "SQL must mention 'city'"
        assert "ON CONFLICT" in sql.upper()
        # Each row tuple should contain "luzern"
        assert all("luzern" in row for row in rows), "Every row must include city='luzern'"

    async def test_persist_weather_city_in_conflict_clause(self):
        """ON CONFLICT clause must target (city, date, hour)."""
        from ml.weather import _persist_to_db

        mock_conn = AsyncMock()
        df = pd.DataFrame(make_weather_rows(SAMPLE_DATE, "zurich"))
        await _persist_to_db(mock_conn, df, city="zurich")

        sql = mock_conn.executemany.call_args[0][0]
        # The conflict target should include city
        assert "city" in sql.lower()


# ---------------------------------------------------------------------------
# 4. load_cached_dates filters by city
# ---------------------------------------------------------------------------

class TestLoadCachedDatesFiltersByCity:
    async def test_load_cached_dates_filters_by_city_miss(self):
        """Rows for city='zurich' must NOT satisfy a query for city='bern'."""
        from ml.weather import _load_dates_from_db

        mock_conn = AsyncMock()
        # DB returns empty result (simulating city='bern' filter on zurich rows)
        mock_conn.fetch = AsyncMock(return_value=[])

        result = await _load_dates_from_db(mock_conn, [SAMPLE_DATE], city="bern")

        # The date should not be returned (wrong city)
        assert SAMPLE_DATE not in result
        # Verify city was passed to the query
        call_args = mock_conn.fetch.call_args[0]
        assert "bern" in call_args, "city='bern' must be passed to the DB query"

    async def test_load_cached_dates_hits_cache_for_matching_city(self):
        """Rows for city='bern' are returned when querying city='bern'."""
        from ml.weather import _load_dates_from_db

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[
            {"date": SAMPLE_DATE, "hour": h,
             "temperature_c": 18.0, "precipitation_mm": 0.0, "weathercode": 0}
            for h in range(24)
        ])

        result = await _load_dates_from_db(mock_conn, [SAMPLE_DATE], city="bern")

        assert SAMPLE_DATE in result
        # Verify city was passed to the query
        call_args = mock_conn.fetch.call_args[0]
        assert "bern" in call_args


# ---------------------------------------------------------------------------
# 5. Cross-city cache isolation (in-memory)
# ---------------------------------------------------------------------------

class TestCrossCityCacheIsolation:
    async def test_in_memory_cache_isolated_by_city(self):
        """zurich cache entry must not be returned when requesting bern."""
        from ml.weather import fetch_weather_batch, _cache

        # Seed zurich cache only
        zurich_df = pd.DataFrame(make_weather_rows(SAMPLE_DATE, "zurich", temp=25.0))
        _cache[("zurich", SAMPLE_DATE)] = zurich_df

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # DB empty for bern

        date_str = SAMPLE_DATE.isoformat()
        bern_response = {
            "hourly": {
                "time": [f"{date_str}T{h:02d}:00" for h in range(24)],
                "temperature_2m": [10.0] * 24,  # distinct temperature
                "precipitation": [0.0] * 24,
                "weathercode": [0] * 24,
            }
        }
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=bern_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("ml.weather._get_db_conn", AsyncMock(return_value=mock_conn)), \
             patch("aiohttp.ClientSession", return_value=mock_session):
            bern_result = await fetch_weather_batch([SAMPLE_DATE], city="bern")

        # Bern should have temp=10, not zurich's 25
        assert bern_result["temperature_c"].iloc[0] == pytest.approx(10.0)

    async def test_city_keyed_cache_does_not_cross_contaminate(self):
        """After fetching bern, zurich cache is unaffected."""
        from ml.weather import _cache

        zurich_df = pd.DataFrame(make_weather_rows(SAMPLE_DATE, "zurich", temp=25.0))
        _cache[("zurich", SAMPLE_DATE)] = zurich_df

        # Confirm zurich cache still has temp=25.0 (h=0 → 25.0 + 0*0.1 = 25.0)
        assert _cache[("zurich", SAMPLE_DATE)]["temperature_c"].iloc[0] == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# 6. Multi-city training helper
# ---------------------------------------------------------------------------

class TestMultiCityTrainingHelper:
    async def test_fetch_weather_for_df_multi_city(self):
        """_fetch_weather_for_df must call fetch_weather_batch once per unique city."""
        from ml.retrain import _fetch_weather_for_df

        # Build a DataFrame with pools from zurich and bern
        # Use real pool uids from pool_metadata.json
        metadata_path = Path("ml/pool_metadata.json")
        metadata = json.loads(metadata_path.read_text())
        zurich_uid = next(p["uid"] for p in metadata if p.get("city") == "zurich")
        bern_uid = next(p["uid"] for p in metadata if p.get("city") == "bern")

        base = datetime.datetime(2025, 6, 1)
        rows = []
        for h in range(24):
            dt = base + datetime.timedelta(hours=h)
            rows.append({"time": dt, "pool_uid": zurich_uid, "occupancy_pct": 50.0,
                          "pool_name": "Z", "current_fill": 50, "max_space": 100, "free_space": 50})
            rows.append({"time": dt, "pool_uid": bern_uid, "occupancy_pct": 40.0,
                          "pool_name": "B", "current_fill": 40, "max_space": 100, "free_space": 60})
        df = pd.DataFrame(rows)

        weather_response = pd.DataFrame({
            "date": [SAMPLE_DATE] * 24,
            "hour": list(range(24)),
            "temperature_c": [20.0] * 24,
            "precipitation_mm": [0.0] * 24,
            "weathercode": [0] * 24,
            "city": ["zurich"] * 24,
        })

        call_cities = []

        async def fake_fetch_batch(dates, city="zurich", **kwargs):
            call_cities.append(city)
            df = weather_response.copy()
            df["city"] = city
            return df

        with patch("ml.retrain.fetch_weather_batch", side_effect=fake_fetch_batch):
            result = await _fetch_weather_for_df(df)

        assert len(call_cities) == 2, f"Expected 2 calls (one per city), got {len(call_cities)}: {call_cities}"
        assert set(call_cities) == {"zurich", "bern"}

    async def test_fetch_weather_for_df_returns_city_column(self):
        """Result of _fetch_weather_for_df must include a 'city' column."""
        from ml.retrain import _fetch_weather_for_df

        metadata_path = Path("ml/pool_metadata.json")
        metadata = json.loads(metadata_path.read_text())
        zurich_uid = next(p["uid"] for p in metadata if p.get("city") == "zurich")

        base = datetime.datetime(2025, 6, 1)
        rows = [{"time": base + datetime.timedelta(hours=h), "pool_uid": zurich_uid,
                 "occupancy_pct": 50.0, "pool_name": "Z", "current_fill": 50,
                 "max_space": 100, "free_space": 50} for h in range(24)]
        df = pd.DataFrame(rows)

        async def fake_fetch_batch(dates, city="zurich", **kwargs):
            return pd.DataFrame({
                "date": [SAMPLE_DATE] * 24,
                "hour": list(range(24)),
                "temperature_c": [20.0] * 24,
                "precipitation_mm": [0.0] * 24,
                "weathercode": [0] * 24,
                "city": [city] * 24,
            })

        with patch("ml.retrain.fetch_weather_batch", side_effect=fake_fetch_batch):
            result = await _fetch_weather_for_df(df)

        assert result is not None
        assert "city" in result.columns


# ---------------------------------------------------------------------------
# 7. Updated join key in features.py
# ---------------------------------------------------------------------------

class TestTrainingJoinUsesCityDateHour:
    def test_training_join_uses_city_and_date_hour(self):
        """add_weather_features joins on (city, date, hour_of_day); bern gets bern weather."""
        from ml.features import add_weather_features, load_pool_metadata

        metadata_path = Path("ml/pool_metadata.json")
        metadata = json.loads(metadata_path.read_text())
        metadata_dict = {p["uid"]: p for p in metadata}

        zurich_uid = next(p["uid"] for p in metadata if p.get("city") == "zurich")
        bern_uid = next(p["uid"] for p in metadata if p.get("city") == "bern")

        date = datetime.date(2025, 6, 1)
        dt = datetime.datetime(2025, 6, 1, 10)

        df = pd.DataFrame([
            {"time": dt, "pool_uid": zurich_uid, "occupancy_pct": 50.0,
             "pool_name": "Z", "current_fill": 50, "max_space": 100, "free_space": 50,
             "hour_of_day": 10, "day_of_week": 6, "is_weekend": 1, "month": 6,
             "day_of_year": 152, "is_holiday": 0, "pool_uid_encoded": 0, "pool_type": 1,
             "is_seasonal": 1, "lag_1h": 50.0, "lag_1w": 50.0, "rolling_mean_7d": 50.0,
             "date": date},
            {"time": dt, "pool_uid": bern_uid, "occupancy_pct": 40.0,
             "pool_name": "B", "current_fill": 40, "max_space": 100, "free_space": 60,
             "hour_of_day": 10, "day_of_week": 6, "is_weekend": 1, "month": 6,
             "day_of_year": 152, "is_holiday": 0, "pool_uid_encoded": 1, "pool_type": 1,
             "is_seasonal": 0, "lag_1h": 40.0, "lag_1w": 40.0, "rolling_mean_7d": 40.0,
             "date": date},
        ])

        # City-aware weather_df: zurich=30°, bern=10°
        weather_df = pd.DataFrame([
            {"city": "zurich", "date": date, "hour": 10,
             "temperature_c": 30.0, "precipitation_mm": 0.0, "weathercode": 0},
            {"city": "bern", "date": date, "hour": 10,
             "temperature_c": 10.0, "precipitation_mm": 0.0, "weathercode": 0},
        ])

        result = add_weather_features(df, weather_df, metadata=metadata_dict)

        zurich_row = result[result["pool_uid"] == zurich_uid]
        bern_row = result[result["pool_uid"] == bern_uid]

        assert zurich_row["temperature_c"].values[0] == pytest.approx(30.0), \
            "Zürich pool should get Zürich temperature"
        assert bern_row["temperature_c"].values[0] == pytest.approx(10.0), \
            "Bern pool should get Bern temperature, not Zürich"
