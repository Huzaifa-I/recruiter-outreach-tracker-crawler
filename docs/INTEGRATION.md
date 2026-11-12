# Integrating the bridge into the app (guide for the app owner)

The crawler writes `bridge_job` (and `bridge_crawl`, `bridge_seen`, `bridge_run`). The app only needs to READ `bridge_job` and write `bridge_seen`. Nothing here touches existing app tables.

## 1. Read API: `jobcrawler/api.py`
Plain functions over a psycopg2 cursor. Wrap them in any route.

    from jobcrawler import api
    prof = api.profile_from_user(role="pm", india=True, remote=True, allow_senior=False, exp_max=4, all_roles=False)
    page = api.list_jobs(cur, prof, cursor=request_cursor, limit=50)
    # page = {"items": [{title, url, location, posted_at, job_key, first_seen}], "next_cursor": "..." | None}

- Newest first by `sort_ts` (the job's posted date when the source gives one, else first crawl time). Keyset pagination on (sort_ts, job_key): no OFFSET, stable while new jobs arrive.
- A profile change needs no re-match job: the next call uses the new params.
- Unknown experience (`exp_min` NULL) is shown, never hidden. `exp_min` is only set from explicit title hints ("3+ years", "Fresher").
- `all_roles=True` is the "show all roles" toggle (default is PM only).
- `api.count_new_since(cur, prof, since)` is a badge counter.
- Use a read-only database role for the web app (SELECT on bridge_job).
- Suggested route: `GET /api/jobs?cursor=&limit=50` returning the dict as JSON. Bad cursors raise ValueError, return HTTP 400.
- Source quality: `bridge_job.confidence` 3 = ATS API, 2 = structured page data, 1 = links or sitemap, 0 = text list (no per-job URL, the link goes to the careers page). Show low confidence below the rest.

## 2. Digest: `jobcrawler/digest.py`
    from jobcrawler import digest
    n = digest.run_for_user(conn, user_id, prof, send=my_send_function)

`my_send_function(user_id, subject, body)` is yours (email, WhatsApp, in-app). Jobs are marked seen only after it returns without raising, so a failed send is retried on the next run. It sends one batched message with links only. It never applies to a job. Run it once a day per user from a scheduler of your choice. The app needs INSERT on `bridge_seen`.

## 3. Operations
- Crawl: GitHub Actions on the public crawler repo (daily). `python -m jobcrawler.healthcheck --gate` checks every run.
- Free-tier limits: Neon 1 GB (about 60 MB at 2,400 portals; check growth with the healthcheck), Actions minutes are free on public repos.
- No auto-apply anywhere in this code.
