import hashlib, datetime as dt
import psycopg2, psycopg2.extras
from . import config
from .dates import parse_posted

def connect():
    if not config.DATABASE_URL: raise SystemExit("DATABASE_URL is not set")
    return psycopg2.connect(config.DATABASE_URL, connect_timeout=15, keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5)

def job_key(portal_id, ext): return hashlib.sha1(f"{portal_id}|{ext}".encode()).hexdigest()[:20]

def claim_due(cur, n, shard, shards):
    """SKIP LOCKED claim, sharded by portal_id so parallel runners never overlap."""
    cur.execute("""
      SELECT portal_id, career_url, ats, ats_token, tier, fail_count, job_hash FROM bridge_crawl
      WHERE next_check <= now() AND fail_count < 5 AND portal_id %% %s = %s
      ORDER BY tier, next_check LIMIT %s FOR UPDATE SKIP LOCKED""", (shards, shard, n))
    return cur.fetchall()

LEASE_MINUTES = 15

def lease(cur, ids, minutes=LEASE_MINUTES):
    """Push next_check out so claimed rows are not claimable by another worker while we crawl without a transaction.
    A crashed worker's rows become due again after the lease. save_result/save_failure overwrite it."""
    if ids: cur.execute("UPDATE bridge_crawl SET next_check = now() + %s WHERE portal_id = ANY(%s)", (dt.timedelta(minutes=minutes), list(ids)))

INTERVAL = {1: dt.timedelta(hours=1), 2: dt.timedelta(days=1), 3: dt.timedelta(days=7)}

def save_result(cur, portal_id, tier, fail, res, classify, cap):
    now = dt.datetime.now(dt.timezone.utc)
    jobs = res["jobs"][:cap]
    h = hashlib.sha1("|".join(sorted(set(str(j["ext"]) for j in jobs))).encode()).hexdigest()
    if not jobs and res["method"] == "none":
        # a low-confidence text_list portal that now yields nothing (filters tightened): close its stale rows. Other methods keep theirs (a transient 'none' must not wipe real jobs).
        cur.execute("UPDATE bridge_job SET closed=true WHERE portal_id=%s AND NOT closed AND EXISTS (SELECT 1 FROM bridge_crawl WHERE portal_id=%s AND method='text_list')", (portal_id, portal_id))
        nxt = now + INTERVAL[3]
        cur.execute("UPDATE bridge_crawl SET method='none', next_check=%s, last_ok=%s, n_jobs=0 WHERE portal_id=%s", (nxt, now, portal_id)); return 0
    rows = []; seen = set()
    for j in jobs:
        k = job_key(portal_id, j["ext"])
        if k in seen: continue        # same ext twice in one result: keep first (ON CONFLICT cannot touch a row twice)
        seen.add(k)
        c = classify(j["title"], j["loc"])
        sts = parse_posted(j.get("posted"), now) or now
        rows.append((k, portal_id, j["title"][:300], j["url"][:1000], (j["loc"] or "")[:200], c["role"], c["level"], c["india"], c["remote"], c["exp_min"], res["confidence"], sts, now, now))
    if rows: psycopg2.extras.execute_values(cur, """
      INSERT INTO bridge_job (job_key,portal_id,title,url,loc,role,level,india,remote,exp_min,confidence,sort_ts,first_seen,last_seen)
      VALUES %s ON CONFLICT (job_key) DO UPDATE SET last_seen=EXCLUDED.last_seen, closed=false, exp_min=EXCLUDED.exp_min""", rows)
    keys = [r[0] for r in rows]
    cur.execute("UPDATE bridge_job SET closed=true WHERE portal_id=%s AND NOT closed AND job_key <> ALL(%s)", (portal_id, keys))
    cur.execute("""UPDATE bridge_crawl SET ats=%s, ats_token=%s, method=%s, confidence=%s, next_check=%s, fail_count=0, last_ok=%s, job_hash=%s, n_jobs=%s WHERE portal_id=%s""",
                (res.get("ats"), res.get("ats_token"), res["method"], res["confidence"], now + INTERVAL[tier], now, h, len(rows), portal_id))
    return len(rows)

def save_timeout(cur, portal_id):
    """Site did not finish inside the batch cap: back off 1h, 2h, 4h ... up to 7 days. Not a failure (fail_count unchanged)."""
    cur.execute("""UPDATE bridge_crawl SET slow_count = slow_count + 1,
        next_check = now() + LEAST(interval '7 days', interval '1 hour' * power(2, LEAST(slow_count, 8))), last_secs = NULL WHERE portal_id=%s""", (portal_id,))

def record_secs(cur, portal_id, secs):
    cur.execute("UPDATE bridge_crawl SET last_secs=%s, slow_count=CASE WHEN %s < 30 THEN 0 ELSE slow_count END WHERE portal_id=%s", (round(secs, 1), secs, portal_id))

def save_failure(cur, portal_id, fail):
    back = dt.timedelta(minutes=30 * 2 ** min(fail, 6))
    cur.execute("UPDATE bridge_crawl SET fail_count=fail_count+1, next_check=now()+%s WHERE portal_id=%s", (back, portal_id))

MATCH = """NOT j.closed AND (%(role)s IS NULL OR j.role=%(role)s) AND (j.india>=%(india)s OR j.remote>=%(remote)s) AND (%(senior)s OR j.level<>'senior')
  AND (j.exp_min IS NULL OR (j.exp_min<=%(exp_max)s))"""

def profile_params(role="pm", india=True, remote=True, allow_senior=False, exp_max=4):
    return dict(role=role, india=int(india), remote=int(remote), senior=allow_senior, exp_max=exp_max)

def page_matches(cur, prof, after=None, limit=50):
    """Compute-at-read: newest first, keyset on (sort_ts, job_key). A profile change needs no re-match job,
    the next read already uses the new profile. Unknown experience (NULL exp_min) is shown, never hidden."""
    q = dict(prof, limit=limit)
    cond = MATCH
    if after: cond += " AND (j.sort_ts, j.job_key) < (%(a_ts)s, %(a_key)s)"; q.update(a_ts=after[0], a_key=after[1])
    cur.execute(f"""SELECT j.title,j.url,j.loc,j.sort_ts,j.job_key,j.first_seen FROM bridge_job j
      WHERE {cond} ORDER BY j.sort_ts DESC, j.job_key DESC LIMIT %(limit)s""", q)
    return cur.fetchall()

def digest(cur, user_id, prof, limit=50, mark=True):
    """Batched notification: matches this user has not been told about yet. Links only, never auto-apply.
    Stores one small row per notified job (bridge_seen), not one per user x job."""
    q = dict(prof, uid=user_id, limit=limit)
    cur.execute(f"""SELECT j.title,j.url,j.loc,j.sort_ts,j.job_key FROM bridge_job j
      WHERE {MATCH} AND NOT EXISTS (SELECT 1 FROM bridge_seen s WHERE s.user_id=%(uid)s AND s.job_key=j.job_key)
      ORDER BY j.sort_ts DESC, j.job_key DESC LIMIT %(limit)s""", q)
    rows = cur.fetchall()
    if mark and rows:
        psycopg2.extras.execute_values(cur, "INSERT INTO bridge_seen (user_id, job_key) VALUES %s ON CONFLICT DO NOTHING", [(user_id, r[4]) for r in rows])
    return rows
