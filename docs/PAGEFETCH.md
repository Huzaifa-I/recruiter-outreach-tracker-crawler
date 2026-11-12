# Page-fetch enrichment (built)

Rows from sitemap, page links, Workday and similar have title and URL only. `python -m jobcrawler.pagefetch` fills description, real posted date, company and experience.

- Phase 1 (default): only jobs matching the role searches in env `CRAWLER_FETCH_ROLES` (default "product manager"), expanded with synonyms (`roles.py`). Phase 2 (`--phase 2`): every open job without a description. Newest first.
- Workday: per-job JSON (`/wday/cxs/<tenant>/<site>/job/...`): description and start date. Other sites: one GET (robots.txt honoured, per-host delay, 8 s timeout), JSON-LD JobPosting first, then the text of `<main>` or `<article>` if it has 400+ characters. Never the whole page.
- Text-list rows (confidence 0, URL = careers page) are skipped.
- Each job is tried once per 14 days (`desc_tried_at`, `desc_status`). 404/410 closes the job. Abandoned slow fetches are marked `timeout`.
- Storage gate: stops when the database is above `CRAWLER_FETCH_DB_LIMIT_MB` (default 500). Checked before every batch.
- No transaction is open while fetching (Neon kills idle-in-transaction connections). Shards split by `portal_id % shards`, so they never overlap.
- A real posted date also becomes `sort_ts`, so newest-first uses the real date.
- Descriptions are plain text, 8,000 characters max. Experience (`exp_min`, `exp_max`) is parsed from the title first, then the description (`classify.exp_from_text`): only mentions near the word "experience" count, earliest mention wins, `max` only for explicit ranges. Approximate by nature; unknown stays unknown and is shown.
- Run: `.github/workflows/pagefetch.yml` (manual dispatch only; inputs phase and limit). Not scheduled.

Measured on 103 random job pages from sitemap, WordPress, RSS and page-link sources: 65% gave a description (67 of 103), average 2,929 characters, 4.3 pages per second with 15 threads over mixed hosts. Workday was not in that sample (one Workday page: 5,925 characters).

## Retention (`python -m jobcrawler.retention`, not run by any workflow)
- `--clear-descriptions`: empties `description` on jobs closed more than 30 days.
- `--delete-closed`: dormant. Counts closed jobs older than 90 days and deletes only with both `--confirm-delete` and env `CRAWLER_ALLOW_DELETE=yes`.
