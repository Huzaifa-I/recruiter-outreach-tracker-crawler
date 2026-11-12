# Proposal (not built): posting-page fetch for rows without a description

Rows from sitemap, page links, Workday, text list and similar have title and URL only. To add description, posted date and company:

1. Which rows: confidence <= 1 and description IS NULL, ordered so rows matching an active user role search come first, then newest. Cap per shard per run (start at 300), so cost is bounded.
2. How: one GET of the job URL (robots.txt honoured, existing per-host 1 s delay, 8 s timeout, same hard batch cap). Parse in this order: JSON-LD JobPosting (description, datePosted, hiringOrganization), then the `<main>`/`article` text if it is at least 400 chars. Workday has a per-job JSON endpoint that gives description and start date.
3. Never refetch a row that has a description or was tried: add `desc_tried_at` (one column) and retry only after 14 days; 404 closes the row.
4. Retention to protect the 1 GB cap: clear `description` of jobs closed more than 30 days, delete closed rows after 90 days.
5. Cost estimate: one extra request per job, once. At 1 s per host and 12 threads, 30,000 postings take roughly 1 to 3 hours of crawl time spread over runs (estimate, depends on how many hosts share the load); Actions minutes are free on the public repo.
