CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS pool_occupancy (
  time          TIMESTAMPTZ     NOT NULL,
  pool_uid      TEXT            NOT NULL,
  pool_name     TEXT            NOT NULL,
  current_fill  INTEGER         NOT NULL,
  max_space     INTEGER         NOT NULL,
  free_space    INTEGER         NOT NULL,
  occupancy_pct DOUBLE PRECISION GENERATED ALWAYS AS 
                (current_fill::float / NULLIF(max_space, 0) * 100) STORED
);

SELECT create_hypertable('pool_occupancy', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_pool_occupancy_uid_time 
  ON pool_occupancy (pool_uid, time DESC);
