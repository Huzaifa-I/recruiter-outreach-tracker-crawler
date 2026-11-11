import hashlib, datetime as dt
import psycopg2, psycopg2.extras
from . import config

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
        rows.append((job_key(portal_id, j["ext"]), portal_id, j["title"][:300], j["url"][:1000], (j["loc"] or "")[:200], c["role"], c["level"], c["india"], c["remote"], res["confidence"], now, now, now))
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

def rematch_user(cur, user_id, role, india, remote, allow_senior):
    """Profile change: rebuild one user's feed from stored jobs in one statement (no recrawl)."""
    cur.execute("DELETE FROM bridge_feed WHERE user_id=%s", (user_id,))
    cur.execute("""INSERT INTO bridge_feed (user_id, job_key, sort_ts)
      SELECT %s, job_key, sort_ts FROM bridge_job
      WHERE NOT closed AND role=%s AND (india >= %s OR remote >= %s) AND (%s OR level <> 'senior')
      ON CONFLICT DO NOTHING""", (user_id, role, int(india), int(remote), allow_senior))
    return cur.rowcount

def page_feed(cur, user_id, after=None, limit=50):
    """Newest first, keyset pagination on (sort_ts, job_key)."""
    if after:
        cur.execute("""SELECT j.title,j.url,j.loc,f.sort_ts,f.job_key FROM bridge_feed f JOIN bridge_job j USING (job_key)
          WHERE f.user_id=%s AND (f.sort_ts, f.job_key) < (%s,%s) ORDER BY f.sort_ts DESC, f.job_key DESC LIMIT %s""", (user_id, after[0], after[1], limit))
    else:
        cur.execute("""SELECT j.title,j.url,j.loc,f.sort_ts,f.job_key FROM bridge_feed f JOIN bridge_job j USING (job_key)
          WHERE f.user_id=%s ORDER BY f.sort_ts DESC, f.job_key DESC LIMIT %s""", (user_id, limit))
    return cur.fetchall()
