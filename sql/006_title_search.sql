-- Additive. Role search matches job titles (ILIKE). A trigram index keeps it fast as bridge_job grows.
-- pg_trgm is available on Neon. If the extension is missing (e.g. a bare local Postgres) the index is skipped and searches still work, just slower.
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
  CREATE INDEX IF NOT EXISTS bridge_job_title_trgm ON bridge_job USING gin (title gin_trgm_ops) WHERE NOT closed;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_trgm index skipped: %', SQLERRM;
END $$;
