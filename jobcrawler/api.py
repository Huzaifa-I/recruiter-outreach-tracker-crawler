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

def profile_from_user(role="pm", india=True, remote=True, allow_senior=False, exp_max=4, all_roles=False):
    """Map an app user's profile to filter params. all_roles=True drops the role filter (toggle in the UI)."""
    return db.profile_params(None if all_roles else role, india, remote, allow_senior, exp_max)

def list_jobs(cur, prof, cursor=None, limit=50):
    """One page of matching open jobs, newest first. Returns {"items": [...], "next_cursor": str|None}."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    after = decode_cursor(cursor) if cursor else None
    rows = db.page_matches(cur, prof, after, limit + 1)          # one extra row tells us whether another page exists
    more = len(rows) > limit; rows = rows[:limit]
    items = [dict(title=r[0], url=r[1], location=r[2], posted_at=r[3].isoformat(), job_key=r[4], first_seen=r[5].isoformat()) for r in rows]
    return {"items": items, "next_cursor": encode_cursor(rows[-1][3], rows[-1][4]) if more and rows else None}

def count_new_since(cur, prof, since):
    """Badge counter: matching jobs first seen after `since` (datetime)."""
    cur.execute(f"SELECT count(*) FROM bridge_job j WHERE {db.MATCH} AND j.first_seen > %(since)s", dict(prof, since=since))
    return cur.fetchone()[0]
