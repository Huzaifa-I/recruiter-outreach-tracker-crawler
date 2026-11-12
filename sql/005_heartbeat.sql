-- Additive. Run BEFORE deploying the new worker.
ALTER TABLE bridge_run   ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;           -- heartbeat
ALTER TABLE bridge_crawl ADD COLUMN IF NOT EXISTS last_secs REAL;                  -- last crawl duration, to find slow portals
ALTER TABLE bridge_crawl ADD COLUMN IF NOT EXISTS slow_count INT NOT NULL DEFAULT 0; -- consecutive timeouts, drives backoff
