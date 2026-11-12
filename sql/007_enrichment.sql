-- Additive, nullable columns, no table rewrite (Postgres adds a NULL column instantly). Run BEFORE deploying the enriching worker.
ALTER TABLE bridge_job ADD COLUMN IF NOT EXISTS description TEXT;         -- plain text JD, truncated to 8000 chars by the crawler
ALTER TABLE bridge_job ADD COLUMN IF NOT EXISTS posted_at   TIMESTAMPTZ;  -- the source's real posted date; NULL when the source gives none (sort_ts keeps coalesce(posted_at, first_seen))
ALTER TABLE bridge_job ADD COLUMN IF NOT EXISTS company     TEXT;         -- from the payload (hiringOrganization / company_name); NULL = use the registry company name at read time
