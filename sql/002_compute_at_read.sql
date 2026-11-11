-- Additive. Replaces the per-user push feed (does not scale on free Neon) with compute-at-read + a small seen table.
CREATE TABLE IF NOT EXISTS bridge_seen (
  user_id     BIGINT NOT NULL,
  job_key     TEXT NOT NULL,
  notified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, job_key)
);
DROP TABLE IF EXISTS bridge_feed;
