"""python -m jobcrawler.worker --shard 0 --shards 4 [--max-batches 5]
Three phases per batch, no transaction open while crawling (Neon kills idle-in-transaction connections):
 1. short tx: claim due portals (SKIP LOCKED) and lease them (next_check = now()+15 min), commit
 2. crawl with no open transaction
 3. fresh connection, short write tx: save each portal under a SAVEPOINT, commit
Writes bridge_* tables only."""
import argparse, signal, concurrent.futures as cf, time, sys
from . import config, db
from .classify import classify
from .extract import extract

def crawl(row):
    pid, url, ats, tok, tier, fail, h = row
    try: return row, extract(url), None
    except Exception as e: return row, None, str(e)[:200]

def claim_and_lease(n, shard, shards):
    conn = db.connect()
    try:
        with conn, conn.cursor() as cur:
            rows = db.claim_due(cur, n, shard, shards)
            db.lease(cur, [r[0] for r in rows])
        return rows
    finally: conn.close()

def write_results(results, dry_run=False):
    ok = err = jobs = 0
    conn = db.connect()
    try:
        with conn, conn.cursor() as cur:
            for row, res, e in results:
                if res is None:
                    err += 1
                    if not dry_run: db.save_failure(cur, row[0], row[5])
                    continue
                ok += 1
                if dry_run: continue
                cur.execute("SAVEPOINT p")      # one bad portal must not roll back the whole batch
                try:
                    jobs += db.save_result(cur, row[0], row[4], row[5], res, classify, config.MAX_JOBS_PER_COMPANY); cur.execute("RELEASE SAVEPOINT p")
                except Exception as ex:
                    cur.execute("ROLLBACK TO SAVEPOINT p"); err += 1; ok -= 1
                    print(f"portal {row[0]} save failed: {str(ex)[:120]}", file=sys.stderr)
                    db.save_failure(cur, row[0], row[5])
    finally: conn.close()
    return ok, err, jobs

def log_run(shard, shards, secs, portals, ok, err, jobs, status, run_id=None):
    """Best effort: a logging failure never fails the crawl."""
    try:
        conn = db.connect()
        with conn, conn.cursor() as cur:
            if run_id is None:
                cur.execute("INSERT INTO bridge_run (shard,shards,status) VALUES (%s,%s,'running') RETURNING id", (shard, shards)); return cur.fetchone()[0]
            cur.execute("UPDATE bridge_run SET secs=%s,portals=%s,ok=%s,err=%s,jobs=%s,status=%s WHERE id=%s", (secs, portals, ok, err, jobs, status, run_id))
        conn.close()
    except Exception as e: print(f"run log skipped: {str(e)[:100]}", file=sys.stderr)

class Interrupted(BaseException): pass

STATE = {}
def _on_term(signum, frame):
    # Actions gives ~7 s after cancel: record the interruption now and exit; the thread pool would otherwise block until crawls finish.
    import os
    st = STATE
    if st.get("rid"): log_run(st["a"].shard, st["a"].shards, time.time() - st["t0"], st["L"]["total"], st["L"]["ok"], st["L"]["err"], st["L"]["jobs"], "interrupted", st["rid"])
    os._exit(143)

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--max-batches", type=int, default=1); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=90, help="stop claiming new batches after this long, so a shard ends cleanly before the 110 min job limit")
    a = ap.parse_args(argv); total = ok = err = jobs = 0; t0 = time.time()
    rid = None if a.dry_run else log_run(a.shard, a.shards, 0, 0, 0, 0, 0, 'running')
    locals_ = dict(total=0, ok=0, err=0, jobs=0)
    STATE.update(a=a, L=locals_, t0=t0, rid=rid)
    signal.signal(signal.SIGTERM, _on_term); signal.signal(signal.SIGINT, _on_term)   # Actions cancel / timeout
    try: _loop(a, locals_, t0)
    except BaseException as e:
        st = 'interrupted' if isinstance(e, Interrupted) else 'failed'
        if rid: log_run(a.shard, a.shards, time.time() - t0, locals_['total'], locals_['ok'], locals_['err'], locals_['jobs'], st, rid)
        raise
    L = locals_; print(f"shard {a.shard}/{a.shards}: portals={L['total']} ok={L['ok']} err={L['err']} jobs_written={L['jobs']} secs={time.time()-t0:.0f}")
    if rid: log_run(a.shard, a.shards, time.time() - t0, L['total'], L['ok'], L['err'], L['jobs'], 'ok', rid)

def _loop(a, L, t0):
    for _ in range(a.max_batches):
        if time.time() - t0 > a.max_minutes * 60: break
        rows = claim_and_lease(config.BATCH, a.shard, a.shards)
        if not rows: break
        with cf.ThreadPoolExecutor(config.THREADS) as ex: results = list(ex.map(crawl, rows))
        o, e, j = write_results(results, a.dry_run)
        L['total'] += len(rows); L['ok'] += o; L['err'] += e; L['jobs'] += j

if __name__ == "__main__": main()
