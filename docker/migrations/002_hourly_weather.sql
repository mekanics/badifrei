-- TASK-023: hourly_weather hypertable for persistent weather caching
-- TASK-026: per-city weather support — city column added to primary key
-- Appended to the TimescaleDB init sequence.

CREATE TABLE IF NOT EXISTS hourly_weather (
  city             VARCHAR(64) NOT NULL,
  date             DATE        NOT NULL,
  hour             SMALLINT    NOT NULL,
  temperature_c    FLOAT,
  precipitation_mm FLOAT,
  weathercode      SMALLINT,
  CONSTRAINT hourly_weather_pkey PRIMARY KEY (city, date, hour)
);

SELECT create_hypertable(
  'hourly_weather',
  'date',
  chunk_time_interval => INTERVAL '1 month',
  if_not_exists => TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_hourly_weather_city_date_hour
  ON hourly_weather (city, date, hour);
