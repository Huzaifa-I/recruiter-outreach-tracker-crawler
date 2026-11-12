"""Retention for the 1 GB free cap. python -m jobcrawler.retention [--clear-descriptions] [--delete-closed --confirm-delete]
Not run by any workflow. Both steps are explicit, and deletion is DORMANT: it only counts (dry run) unless BOTH --confirm-delete is passed
AND the env var CRAWLER_ALLOW_DELETE=yes is set. last_seen of a closed job is the last time a crawl still saw it."""
import argparse, os
from . import db

def clear_old_descriptions(cur, days=30):
    cur.execute("UPDATE bridge_job SET description=NULL WHERE closed AND description IS NOT NULL AND last_seen < now() - %s * interval '1 day'", (days,))
    return cur.rowcount

def delete_old_closed(cur, days=90, confirm=False):
    """Returns (count, deleted_bool). Dry run (count only) unless confirm=True and CRAWLER_ALLOW_DELETE=yes."""
    cur.execute("SELECT count(*) FROM bridge_job WHERE closed AND last_seen < now() - %s * interval '1 day'", (days,))
    n = cur.fetchone()[0]
    if not (confirm and os.environ.get("CRAWLER_ALLOW_DELETE") == "yes"): return n, False
    cur.execute("""WITH d AS (DELETE FROM bridge_job WHERE closed AND last_seen < now() - %s * interval '1 day' RETURNING job_key)
                   DELETE FROM bridge_seen s USING d WHERE s.job_key = d.job_key""", (days,))
    return n, True

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--clear-descriptions", action="store_true"); ap.add_argument("--clear-days", type=int, default=30)
    ap.add_argument("--delete-closed", action="store_true"); ap.add_argument("--delete-days", type=int, default=90); ap.add_argument("--confirm-delete", action="store_true")
    a = ap.parse_args(argv); conn = db.connect()
    with conn, conn.cursor() as cur:
        if a.clear_descriptions: print(f"descriptions cleared on closed jobs older than {a.clear_days} days: {clear_old_descriptions(cur, a.clear_days)}")
        if a.delete_closed:
            n, done = delete_old_closed(cur, a.delete_days, a.confirm_delete)
            print(f"closed jobs older than {a.delete_days} days: {n} {'DELETED' if done else '(dry run, nothing deleted)'}")
    conn.close()

if __name__ == "__main__": main()
