"""python -m jobcrawler.worker --shard 0 --shards 4 [--max-batches 5]
Claims due portals, extracts jobs, writes bridge_* tables only. One transaction per batch."""
import argparse, concurrent.futures as cf, time, sys
from . import config, db
from .classify import classify
from .extract import extract

def crawl(row):
    pid, url, ats, tok, tier, fail, h = row
    try: return row, extract(url), None
    except Exception as e: return row, None, str(e)[:200]

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--max-batches", type=int, default=1); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv); conn = db.connect(); total = ok = err = jobs = 0; t0 = time.time()
    for _ in range(a.max_batches):
        with conn, conn.cursor() as cur:
            rows = db.claim_due(cur, config.BATCH, a.shard, a.shards)
            if not rows: break
            with cf.ThreadPoolExecutor(config.THREADS) as ex: results = list(ex.map(crawl, rows))
            for row, res, e in results:
                total += 1
                if res is None: err += 1; db.save_failure(cur, row[0], row[5]); continue
                ok += 1
                if not a.dry_run:
                    cur.execute("SAVEPOINT p")      # one bad portal must not roll back the whole batch
                    try: jobs += db.save_result(cur, row[0], row[4], row[5], res, classify, config.MAX_JOBS_PER_COMPANY); cur.execute("RELEASE SAVEPOINT p")
                    except Exception as e:
                        cur.execute("ROLLBACK TO SAVEPOINT p"); err += 1; ok -= 1; print(f"portal {row[0]} save failed: {str(e)[:120]}", file=sys.stderr)
                        db.save_failure(cur, row[0], row[5])
            if a.dry_run: conn.rollback()
    print(f"shard {a.shard}/{a.shards}: portals={total} ok={ok} err={err} jobs_written={jobs} secs={time.time()-t0:.0f}")

if __name__ == "__main__": main()
