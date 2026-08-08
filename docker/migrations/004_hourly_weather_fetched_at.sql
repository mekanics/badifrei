-- Live air-weather freshness: track when an hourly_weather row was last fetched.
-- Nullable so existing write-once ML cache rows remain valid; null ⇒ treat as stale
-- for the live WeatherHint path (forces one refresh after migrate).

ALTER TABLE hourly_weather
  ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ;
