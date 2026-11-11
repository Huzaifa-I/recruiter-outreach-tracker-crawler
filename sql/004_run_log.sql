-- Additive. One row per worker shard run, so post-run checks do not depend on Actions logs.
CREATE TABLE IF NOT EXISTS bridge_run (
  id        BIGSERIAL PRIMARY KEY,
  started   TIMESTAMPTZ NOT NULL DEFAULT now(),
  shard     INT, shards INT,
  secs      REAL, portals INT, ok INT, err INT, jobs INT,
  status    TEXT NOT NULL DEFAULT 'running'      -- running | ok | failed
);
