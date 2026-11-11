"""python -m jobcrawler.worker --shard 0 --shards 4 [--max-batches 5]
Three phases per batch, no transaction open while crawling (Neon kills idle-in-transaction connections):
 1. short tx: claim due portals (SKIP LOCKED) and lease them (next_check = now()+15 min), commit
 2. crawl with no open transaction
 3. fresh connection, short write tx: save each portal under a SAVEPOINT, commit
Writes bridge_* tables only."""
import argparse, concurrent.futures as cf, time, sys
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

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--max-batches", type=int, default=1); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv); total = ok = err = jobs = 0; t0 = time.time()
    for _ in range(a.max_batches):
        rows = claim_and_lease(config.BATCH, a.shard, a.shards)
        if not rows: break
        with cf.ThreadPoolExecutor(config.THREADS) as ex: results = list(ex.map(crawl, rows))
        o, e, j = write_results(results, a.dry_run)
        total += len(rows); ok += o; err += e; jobs += j
    print(f"shard {a.shard}/{a.shards}: portals={total} ok={ok} err={err} jobs_written={jobs} secs={time.time()-t0:.0f}")

if __name__ == "__main__": main()
