-- Additive only. Run against a scratch Neon branch first. New tables, no changes to existing ones.
CREATE TABLE IF NOT EXISTS bridge_crawl (
  portal_id      BIGINT PRIMARY KEY,            -- outreach_career_portals.id (no FK: keeps crawler decoupled)
  career_url     TEXT NOT NULL,
  ats            TEXT,
  ats_token      TEXT,
  method         TEXT,
  confidence     SMALLINT DEFAULT 0,
  tier           SMALLINT NOT NULL DEFAULT 2,   -- 1 hourly, 2 daily, 3 weekly
  next_check     TIMESTAMPTZ NOT NULL DEFAULT now(),
  fail_count     INT NOT NULL DEFAULT 0,
  last_ok        TIMESTAMPTZ,
  job_hash       TEXT,                          -- hash of sorted job ids, skip writes when unchanged
  n_jobs         INT
);
CREATE INDEX IF NOT EXISTS bridge_crawl_due ON bridge_crawl (next_check) WHERE fail_count < 5;

CREATE TABLE IF NOT EXISTS bridge_job (
  job_key     TEXT PRIMARY KEY,                 -- sha1(portal_id|ext_id), idempotent upsert key
  portal_id   BIGINT NOT NULL,
  title       TEXT NOT NULL,
  url         TEXT NOT NULL,
  loc         TEXT,
  role        TEXT, level TEXT,
  india       SMALLINT, remote SMALLINT,
  exp_min     REAL,                             -- NULL = unknown, never hidden
  confidence  SMALLINT,
  sort_ts     TIMESTAMPTZ NOT NULL,             -- coalesce(posted, first_seen)
  first_seen  TIMESTAMPTZ NOT NULL,
  last_seen   TIMESTAMPTZ NOT NULL,
  closed      BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS bridge_job_match ON bridge_job (role, india, remote, sort_ts DESC, job_key DESC) WHERE NOT closed;
CREATE INDEX IF NOT EXISTS bridge_job_portal ON bridge_job (portal_id) WHERE NOT closed;

CREATE TABLE IF NOT EXISTS bridge_feed (
  user_id   BIGINT NOT NULL,
  job_key   TEXT NOT NULL,
  sort_ts   TIMESTAMPTZ NOT NULL,
  notified  BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (user_id, job_key)
);
CREATE INDEX IF NOT EXISTS bridge_feed_page ON bridge_feed (user_id, sort_ts DESC, job_key DESC);
