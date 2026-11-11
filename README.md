# jobcrawler

Finds open jobs on public company career pages and writes them to a Postgres database.
Reads public pages and public ATS JSON endpoints only. No logins, no scraping behind authentication.

## What it does
- Detects the applicant tracking system (ATS) behind a career page and reads its public API:
  Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, Workday, Oracle HCM, Zoho Recruit.
- Falls back to schema.org JobPosting, WordPress REST job types, filtered sitemap URLs, and job links on the page.
- Every result carries a confidence level (3 = ATS API, 2 = JSON-LD/WordPress, 1 = sitemap or page links).
- Rule-based classification (role, level, India, remote). Unknown experience stays unknown.
- Queue in Postgres (`FOR UPDATE SKIP LOCKED`), sharded, exponential backoff, closed-job tracking.

## Politeness
Identifying User-Agent (set `CRAWLER_CONTACT`), robots.txt honoured for HTML pages, per-host delay, backoff on 429/503.
JavaScript-rendered pages are not executed. Pages that need it return no jobs.

## Configuration (environment variables)
| Variable | Meaning | Default |
|---|---|---|
| `DATABASE_URL` | Postgres connection string (secret) | required |
| `CRAWLER_CONTACT` | URL or email shown in the User-Agent | empty |
| `CRAWLER_THREADS` / `CRAWLER_BATCH` | parallelism / portals per batch | 12 / 300 |
| `CRAWLER_MAX_JOBS` | max jobs stored per company | 200 |
| `CRAWLER_HOST_DELAY` | seconds between requests to one host | 1.0 |

Secrets live only in your CI secret store or local environment. This repository contains no credentials, no user data and no company lists.

## Run
```
pip install -r requirements.txt
psql "$DATABASE_URL" -f sql/001_bridge.sql      # additive tables only; try on a scratch branch first
python -m jobcrawler.worker --shard 0 --shards 4 --dry-run
python -m unittest discover -s tests -t .
```
Seeding `bridge_crawl` (portal id + career URL) is done by the operator from their own data.

## Status
Early. The extractor was measured on a 300-page sample of company career pages; coverage and precision numbers are in the project notes, not guaranteed. The SQL has not yet been run against a live Postgres.

## License
MIT
