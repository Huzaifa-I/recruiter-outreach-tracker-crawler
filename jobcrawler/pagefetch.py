"""Page-fetch enrichment: description, real posted date, company, experience for jobs that only have a title and URL.
python -m jobcrawler.pagefetch --shard 0 --shards 4 [--phase 1|2] [--limit 750] [--max-minutes 30]
Phase 1 (default): only jobs matching the configured role searches (env CRAWLER_FETCH_ROLES, default "product manager", expanded with synonyms).
Phase 2: every open job without a description. Newest first. Workday uses its per-job JSON; other sites one GET (robots honoured, per-host delay).
Storage gate: stops when the database is above CRAWLER_FETCH_DB_LIMIT_MB (default 500). A job is tried once per 14 days. No transaction is open while fetching."""
import argparse, concurrent.futures as cf, os, re, sys, time
from . import config, db, roles as roles_mod
from .classify import exp_range
from .dates import parse_posted
from .extract import jsonld_jobs
from .http import get, get_json
from .textutil import clean_description, clean_company

RETRY_DAYS = 14
MIN_PAGE_TEXT = 400
WORKDAY = re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)(/job/.*)$")

def main_text(html):
    """Readable text of <main> or <article> only (never the whole page: nav and footers are not a job description)."""
    h = re.sub(r"<(script|style|nav|header|footer|noscript|form|aside)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    for tag in ("main", "article"):
        m = re.search(rf"<{tag}\b[^>]*>(.*)</{tag}>", h, re.S | re.I)
        if m:
            t = clean_description(m.group(1))
            if t and len(t) >= MIN_PAGE_TEXT: return t
    return None

def fetch_description(url):
    """One job page -> dict(status, description, posted, company). Never raises."""
    try:
        m = WORKDAY.match(url)
        if m:
            t, wd, site, path = m.groups()
            s, j = get_json(f"https://{t}.{wd}.myworkdayjobs.com/wday/cxs/{t}/{site}{path}")
            if s in (404, 410): return dict(status="gone")
            info = (j or {}).get("jobPostingInfo") if s == 200 else None
            if not info: return dict(status=f"http_{s}" if s else "fetch_fail")
            d = clean_description(info.get("jobDescription"))
            # hiringOrganization on Workday is an internal legal entity code ("CO_320 ..."), so it is not used as the company name
            return dict(status="ok" if d else "no_desc", description=d, posted=info.get("startDate"))
        s, b, _, final = get(url, limit=1_500_000)
        if s == 999: return dict(status="robots")
        if s in (404, 410): return dict(status="gone")
        if s != 200: return dict(status=f"http_{s}" if s else "fetch_fail")
        html = b.decode("utf8", "ignore")
        ld = jsonld_jobs(html)
        if ld:
            j = next(iter(ld.values()))
            d = clean_description(j.get("description"))
            if d: return dict(status="ok", description=d, posted=j.get("posted"), company=j.get("company"))
        d = main_text(html)
        return dict(status="ok", description=d) if d else dict(status="no_desc", posted=(next(iter(ld.values())).get("posted") if ld else None))
    except Exception:
        return dict(status="fetch_fail")

def db_size_mb(cur):
    cur.execute("SELECT pg_database_size(current_database())"); return cur.fetchone()[0] / 1e6

def select_queue(cur, shard, shards, limit, terms=None, phase=1):
    """Open jobs without a description, newest first, not tried in the last 14 days. confidence 0 (text list) is skipped: its URL is the careers page."""
    keys, pats = roles_mod.expand_many(terms or ["product manager"])
    q = dict(shard=shard, shards=shards, limit=limit, keys=keys, pats=pats, phase=phase, days=RETRY_DAYS)
    cur.execute("""SELECT job_key, url, title FROM bridge_job j
        WHERE NOT closed AND description IS NULL AND confidence > 0 AND portal_id %% %(shards)s = %(shard)s
          AND (desc_tried_at IS NULL OR desc_tried_at < now() - %(days)s * interval '1 day')
          AND (%(phase)s = 2 OR j.role = ANY(%(keys)s) OR j.title ~* ANY(%(pats)s))
        ORDER BY sort_ts DESC, job_key DESC LIMIT %(limit)s""", q)
    return cur.fetchall()

def fetch_batch(rows, deadline):
    """Fetch in a thread pool until deadline. Returns (results, timed_out). Never blocks past the deadline."""
    ex = cf.ThreadPoolExecutor(config.THREADS)
    futs = {ex.submit(lambda r=r: (r, fetch_description(r[1]))): r for r in rows}
    done, pending = cf.wait(futs, timeout=max(0.0, deadline - time.time()))
    results = [f.result() for f in done]; timed_out = [futs[f] for f in pending]
    for f in pending: f.cancel()
    ex.shutdown(wait=False, cancel_futures=True)
    return results, timed_out

def write_batch(cur, results, timed_out=()):
    """Save fetched fields. Missing values never overwrite stored ones; a real posted date also becomes the sort date; 404/410 closes the job."""
    n_ok = 0
    for (key, url, title), r in results:
        d = r.get("description"); pdt = parse_posted(r.get("posted"))
        lo, hi = exp_range(title, d) if d else (None, None)
        gone = r["status"] == "gone"
        cur.execute("""UPDATE bridge_job SET description=COALESCE(%s, description), posted_at=COALESCE(%s, posted_at), sort_ts=COALESCE(%s, sort_ts),
                        company=COALESCE(%s, company), exp_min=COALESCE(exp_min, %s), exp_max=COALESCE(exp_max, %s),
                        desc_tried_at=now(), desc_status=%s, closed = closed OR %s WHERE job_key=%s""",
                    (d, pdt, pdt, clean_company(r.get("company")), lo, hi, r["status"], gone, key))
        n_ok += 1 if d else 0
    for key, url, title in timed_out:
        cur.execute("UPDATE bridge_job SET desc_tried_at=now(), desc_status='timeout' WHERE job_key=%s", (key,))
    return n_ok

def run(conn, shard=0, shards=1, limit=750, batch=150, max_minutes=30.0, terms=None, phase=1, db_limit_mb=500.0, batch_max_min=None):
    """Returns a summary dict. Short transactions only; fetching happens with no connection transaction open."""
    t0 = time.time(); batch_max_min = batch_max_min or config.BATCH_MAX_MIN
    S = dict(selected=0, with_description=0, gone=0, timed_out=0, failed=0, stopped=None)
    while S["selected"] < limit and time.time() - t0 < max_minutes * 60:
        with conn, conn.cursor() as cur:
            size = db_size_mb(cur)
            if size > db_limit_mb: S["stopped"] = f"storage gate: db {size:.0f} MB > {db_limit_mb:.0f} MB"; break
            rows = select_queue(cur, shard, shards, min(batch, limit - S["selected"]), terms, phase)
        if not rows: S["stopped"] = S["stopped"] or "queue empty"; break
        deadline = min(time.time() + batch_max_min * 60, t0 + (max_minutes + 5) * 60)
        results, timed_out = fetch_batch(rows, deadline)
        with conn, conn.cursor() as cur:
            S["with_description"] += write_batch(cur, results, timed_out)
        S["selected"] += len(rows); S["timed_out"] += len(timed_out)
        S["gone"] += sum(1 for _, r in results if r["status"] == "gone")
        S["failed"] += sum(1 for _, r in results if r["status"] not in ("ok", "no_desc", "gone"))
    S["secs"] = round(time.time() - t0)
    return S

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--phase", type=int, default=1, choices=(1, 2)); ap.add_argument("--limit", type=int, default=750); ap.add_argument("--batch", type=int, default=150)
    ap.add_argument("--max-minutes", type=float, default=30)
    a = ap.parse_args(argv)
    terms = [x for x in os.environ.get("CRAWLER_FETCH_ROLES", "product manager").split(",") if x.strip()]
    conn = db.connect()
    S = run(conn, a.shard, a.shards, a.limit, a.batch, a.max_minutes, terms, a.phase, float(os.environ.get("CRAWLER_FETCH_DB_LIMIT_MB", "500")))
    print(f"pagefetch shard {a.shard}/{a.shards} phase {a.phase}: {S}")
    sys.stdout.flush(); os._exit(0)      # abandoned slow-fetch threads must not hold the process open

if __name__ == "__main__": main()
