-- Additive, nullable columns (instant). Run BEFORE deploying the new worker (the insert names exp_max).
ALTER TABLE bridge_job ADD COLUMN IF NOT EXISTS exp_max      REAL;         -- upper bound of an explicit range ("3-5 years"); NULL = open ended or unknown
ALTER TABLE bridge_job ADD COLUMN IF NOT EXISTS desc_tried_at TIMESTAMPTZ; -- last page-fetch attempt, retry after 14 days
ALTER TABLE bridge_job ADD COLUMN IF NOT EXISTS desc_status  TEXT;         -- ok | no_desc | gone | robots | timeout | http_NNN | fetch_fail
