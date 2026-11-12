"""Batched notification digest. Standalone: it builds the text and records what was sent; the app owner plugs in the channel (email, WhatsApp, in-app).
Links only. Nothing here applies to a job or contacts anyone but the user."""
from . import db

def build_digest(rows, max_items=15):
    """rows from db.digest: (title, url, loc, sort_ts, job_key). Returns (subject, body) or None when empty."""
    if not rows: return None
    shown = rows[:max_items]
    lines = [f"- {t}{' (' + l + ')' if l else ''}: {u}" for t, u, l, ts, k in shown]
    extra = len(rows) - len(shown)
    if extra > 0: lines.append(f"...and {extra} more in the app.")
    return f"{len(rows)} new matching job{'s' if len(rows) != 1 else ''}", "\n".join(lines)

def run_for_user(conn, user_id, prof, send, max_items=15, limit=50):
    """Send one batched digest. Order is safe against a failed send: rows are only marked seen after send() returns without raising.
    `send(user_id, subject, body)` is supplied by the app. Returns the number of jobs notified."""
    with conn, conn.cursor() as cur:
        rows = db.digest(cur, user_id, prof, limit=limit, mark=False)
        msg = build_digest(rows, max_items)
        if not msg: return 0
        send(user_id, *msg)                  # raises -> transaction rolls back, nothing marked seen, retried next run
        import psycopg2.extras
        psycopg2.extras.execute_values(cur, "INSERT INTO bridge_seen (user_id, job_key) VALUES %s ON CONFLICT DO NOTHING", [(user_id, r[4]) for r in rows])
        return len(rows)
