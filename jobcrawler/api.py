"""Framework-agnostic read API over bridge_job. No HTTP, no app imports: wrap list_jobs in any FastAPI/Flask/Express-bridge route.
All functions take an open psycopg2 cursor and return plain dicts, so the app owner decides auth, routing and connection pooling.
Read-only SQL. Newest first, keyset pagination (no OFFSET), unknown experience is shown, never hidden."""
import base64, datetime as dt
from . import db

MAX_LIMIT = 100

def encode_cursor(sort_ts, job_key):
    return base64.urlsafe_b64encode(f"{sort_ts.isoformat()}|{job_key}".encode()).decode()

def decode_cursor(c):
    try:
        ts, key = base64.urlsafe_b64decode(c.encode()).decode().split("|", 1)
        return dt.datetime.fromisoformat(ts), key
    except Exception:
        raise ValueError("bad cursor")

def profile_from_user(roles, india=True, remote=True, allow_senior=False, exp_max=4, any_role=False, exp_lo=0):
    """roles: the role(s) the user searches or has in their profile, a string or list. The feed shows only those roles.
    any_role=True is only for an explicit 'any role' choice; without roles and without it, ValueError (never a silent all-roles feed)."""
    return db.profile_params(roles, india, remote, allow_senior, exp_max, any_role, exp_lo)

def list_jobs(cur, prof, cursor=None, limit=50):
    """One page of matching open jobs, newest first. Returns {"items": [...], "next_cursor": str|None}."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    after = decode_cursor(cursor) if cursor else None
    rows = db.page_matches(cur, prof, after, limit + 1)          # one extra row tells us whether another page exists
    more = len(rows) > limit; rows = rows[:limit]
    items = [dict(title=r[0], url=r[1], location=r[2], sort_ts=r[3].isoformat(), job_key=r[4], first_seen=r[5].isoformat(), company=r[6], posted_at=r[7].isoformat() if r[7] else None) for r in rows]
    return {"items": items, "next_cursor": encode_cursor(rows[-1][3], rows[-1][4]) if more and rows else None}

def count_new_since(cur, prof, since):
    """Badge counter: matching jobs first seen after `since` (datetime)."""
    cur.execute(f"SELECT count(*) FROM bridge_job j WHERE {db.MATCH} AND j.first_seen > %(since)s", dict(prof, since=since))
    return cur.fetchone()[0]

def get_job(cur, job_key):
    """Detail view: includes the description (kept out of list pages to keep them small). Returns None if unknown."""
    cur.execute("SELECT title,url,loc,company,posted_at,first_seen,description,closed FROM bridge_job WHERE job_key=%s", (job_key,))
    r = cur.fetchone()
    if not r: return None
    return dict(title=r[0], url=r[1], location=r[2], company=r[3], posted_at=r[4].isoformat() if r[4] else None, first_seen=r[5].isoformat(), description=r[6], closed=r[7])
