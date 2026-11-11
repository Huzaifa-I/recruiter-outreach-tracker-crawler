-- Run once after 001/002. Assumes outreach_career_portals(id, career_url); adapt names and add the "verified" filter.
INSERT INTO bridge_crawl (portal_id, career_url, tier)
SELECT p.id, p.career_url, 2
FROM outreach_career_portals p
WHERE p.career_url ~ '^https?://'
  -- AND p.verified            -- adapt: only verified career pages
ON CONFLICT (portal_id) DO NOTHING;
-- First run: keep small, e.g. seed 400 first by adding "ORDER BY p.id LIMIT 400".
