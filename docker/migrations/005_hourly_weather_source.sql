-- Archive-backed training weather: track whether a row came from forecast,
-- archive, or live refresh. Null = legacy (treat as needing archive upgrade
-- when the date is archive-eligible).
ALTER TABLE hourly_weather
ADD COLUMN IF NOT EXISTS source VARCHAR(16);