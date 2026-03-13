-- TASK-023: hourly_weather hypertable for persistent weather caching
-- Appended to the TimescaleDB init sequence.

CREATE TABLE IF NOT EXISTS hourly_weather (
  date             DATE       NOT NULL,
  hour             SMALLINT   NOT NULL,
  temperature_c    FLOAT,
  precipitation_mm FLOAT,
  weathercode      SMALLINT,
  CONSTRAINT hourly_weather_pkey PRIMARY KEY (date, hour)
);

SELECT create_hypertable(
  'hourly_weather',
  'date',
  chunk_time_interval => INTERVAL '1 month',
  if_not_exists => TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_hourly_weather_date_hour
  ON hourly_weather (date, hour);
