-- Opening-hours observations from the Stadt Zürich Baditicker XML feed.
-- Append-only log; read-time freshness keys off observed_at (poll time),
-- not source_modified_at (Baditicker dateModified).

CREATE TABLE IF NOT EXISTS pool_status (
  observed_at         TIMESTAMPTZ NOT NULL,
  pool_uid            TEXT        NOT NULL,
  baditicker_poiid    TEXT        NOT NULL,
  status_text         TEXT,
  water_temp_c        FLOAT,
  source_modified_at  TIMESTAMPTZ,
  CONSTRAINT pool_status_pkey PRIMARY KEY (observed_at, pool_uid)
);

SELECT create_hypertable(
  'pool_status',
  'observed_at',
  chunk_time_interval => INTERVAL '1 month',
  if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_pool_status_uid_time
  ON pool_status (pool_uid, observed_at DESC);
