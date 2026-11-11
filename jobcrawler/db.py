import hashlib, datetime as dt
import psycopg2, psycopg2.extras
from . import config
from .dates import parse_posted

def connect():
    if not config.DATABASE_URL: raise SystemExit("DATABASE_URL is not set")
    return psycopg2.connect(config.DATABASE_URL, connect_timeout=15)

def job_key(portal_id, ext): return hashlib.sha1(f"{portal_id}|{ext}".encode()).hexdigest()[:20]

def claim_due(cur, n, shard, shards):
    """SKIP LOCKED claim, sharded by portal_id so parallel runners never overlap."""
    cur.execute("""
      SELECT portal_id, career_url, ats, ats_token, tier, fail_count, job_hash FROM bridge_crawl
      WHERE next_check <= now() AND fail_count < 5 AND portal_id %% %s = %s
      ORDER BY tier, next_check LIMIT %s FOR UPDATE SKIP LOCKED""", (shards, shard, n))
    return cur.fetchall()

INTERVAL = {1: dt.timedelta(hours=1), 2: dt.timedelta(days=1), 3: dt.timedelta(days=7)}

def save_result(cur, portal_id, tier, fail, res, classify, cap):
    now = dt.datetime.now(dt.timezone.utc)
    jobs = res["jobs"][:cap]
    h = hashlib.sha1("|".join(sorted(j["ext"] for j in jobs)).encode()).hexdigest()
    if not jobs and res["method"] == "none":
        nxt = now + INTERVAL[3]
        cur.execute("UPDATE bridge_crawl SET method='none', next_check=%s, last_ok=%s, n_jobs=0 WHERE portal_id=%s", (nxt, now, portal_id)); return 0
    rows = []
    for j in jobs:
        c = classify(j["title"], j["loc"])
        sts = parse_posted(j.get("posted"), now) or now
        rows.append((job_key(portal_id, j["ext"]), portal_id, j["title"][:300], j["url"][:1000], (j["loc"] or "")[:200], c["role"], c["level"], c["india"], c["remote"], res["confidence"], sts, now, now))
    psycopg2.extras.execute_values(cur, """
      INSERT INTO bridge_job (job_key,portal_id,title,url,loc,role,level,india,remote,confidence,sort_ts,first_seen,last_seen)
      VALUES %s ON CONFLICT (job_key) DO UPDATE SET last_seen=EXCLUDED.last_seen, closed=false""", rows)
    keys = [r[0] for r in rows]
    cur.execute("UPDATE bridge_job SET closed=true WHERE portal_id=%s AND NOT closed AND job_key <> ALL(%s)", (portal_id, keys))
    cur.execute("""UPDATE bridge_crawl SET ats=%s, ats_token=%s, method=%s, confidence=%s, next_check=%s, fail_count=0, last_ok=%s, job_hash=%s, n_jobs=%s WHERE portal_id=%s""",
                (res.get("ats"), res.get("ats_token"), res["method"], res["confidence"], now + INTERVAL[tier], now, h, len(jobs), portal_id))
    return len(rows)

def save_failure(cur, portal_id, fail):
    back = dt.timedelta(minutes=30 * 2 ** min(fail, 6))
    cur.execute("UPDATE bridge_crawl SET fail_count=fail_count+1, next_check=now()+%s WHERE portal_id=%s", (back, portal_id))

MATCH = """NOT j.closed AND j.role=%(role)s AND (j.india>=%(india)s OR j.remote>=%(remote)s) AND (%(senior)s OR j.level<>'senior')
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
