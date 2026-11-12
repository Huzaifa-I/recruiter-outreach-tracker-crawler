"""python -m jobcrawler.worker --shard 0 --shards 4 [--max-batches 5] [--max-minutes 35]
Three phases per batch, no transaction open while crawling (Neon kills idle-in-transaction connections):
 1. short tx: claim due portals (SKIP LOCKED) and lease them (next_check = now()+15 min), commit
 2. crawl with no open transaction, under a hard wall-time cap; sites still running at the cap are abandoned
 3. fresh connection, short write tx: save each portal under a SAVEPOINT, commit
A heartbeat thread updates bridge_run.updated_at every 60 s so a killed run is detectable. Writes bridge_* tables only."""
import argparse, atexit, concurrent.futures as cf, os, signal, sys, threading, time
from . import config, db
from .classify import classify
from .extract import extract

HEARTBEAT_SECS = 60
STATE = {}

def crawl(row):
    pid, url, ats, tok, tier, fail, h = row
    t = time.time()
    try: return row, extract(url), None, time.time() - t
    except Exception as e: return row, None, str(e)[:200], time.time() - t

def claim_and_lease(n, shard, shards):
    conn = db.connect()
    try:
        with conn, conn.cursor() as cur:
            rows = db.claim_due(cur, n, shard, shards)
            db.lease(cur, [r[0] for r in rows])
        return rows
    finally: conn.close()

def crawl_batch(rows, deadline):
    """Crawl in a thread pool until `deadline` (epoch secs). Returns (results, timed_out_rows). Never blocks past the deadline."""
    ex = cf.ThreadPoolExecutor(config.THREADS)
    futs = {ex.submit(crawl, r): r for r in rows}
    done, pending = cf.wait(futs, timeout=max(0.0, deadline - time.time()))
    results = [f.result() for f in done]
    timed_out = [futs[f] for f in pending]
    for f in pending: f.cancel()
    ex.shutdown(wait=False, cancel_futures=True)   # threads still inside a slow site are abandoned; the process exits with os._exit
    return results, timed_out

def write_results(results, dry_run=False, timed_out=()):
    ok = err = jobs = 0
    conn = db.connect()
    try:
        with conn, conn.cursor() as cur:
            for item in results:
                row, res, e = item[:3]; secs = item[3] if len(item) > 3 else None
                if res is None:
                    err += 1
                    if not dry_run: db.save_failure(cur, row[0], row[5])
                    continue
                ok += 1
                if dry_run: continue
                cur.execute("SAVEPOINT p")      # one bad portal must not roll back the whole batch
                try:
                    jobs += db.save_result(cur, row[0], row[4], row[5], res, classify, config.MAX_JOBS_PER_COMPANY)
                    if secs is not None: db.record_secs(cur, row[0], secs)
                    cur.execute("RELEASE SAVEPOINT p")
                except Exception as ex:
                    cur.execute("ROLLBACK TO SAVEPOINT p"); err += 1; ok -= 1
                    print(f"portal {row[0]} save failed: {str(ex)[:120]}", file=sys.stderr)
                    db.save_failure(cur, row[0], row[5])
            for row in timed_out:
                err += 1
                if not dry_run: db.save_timeout(cur, row[0])
    finally: conn.close()
    return ok, err, jobs

def log_run(shard, shards, secs, portals, ok, err, jobs, status, run_id=None):
    """Best effort (short connect timeout): a logging failure never fails the crawl. Every call also refreshes the heartbeat."""
    try:
        conn = db.connect()
        try:
            with conn, conn.cursor() as cur:
                if run_id is None:
                    cur.execute("INSERT INTO bridge_run (shard,shards,status,updated_at) VALUES (%s,%s,'running',now()) RETURNING id", (shard, shards)); return cur.fetchone()[0]
                cur.execute("UPDATE bridge_run SET secs=%s,portals=%s,ok=%s,err=%s,jobs=%s,status=%s,updated_at=now() WHERE id=%s AND status IN ('running','interrupted')", (secs, portals, ok, err, jobs, status, run_id))
        finally: conn.close()
    except Exception as e: print(f"run log skipped: {str(e)[:100]}", file=sys.stderr)

def _snapshot(status):
    st = STATE
    if st.get("rid") and not st.get("final"):
        L = st["L"]; log_run(st["a"].shard, st["a"].shards, time.time() - st["t0"], L["total"], L["ok"], L["err"], L["jobs"], status, st["rid"])

def _heartbeat():
    while not STATE.get("final"):
        time.sleep(HEARTBEAT_SECS)
        if not STATE.get("final"): _snapshot("running")

def _on_term(signum, frame):
    _snapshot("interrupted"); STATE["final"] = True
    os._exit(143)

def _atexit():
    if not STATE.get("final"): _snapshot("interrupted")

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--max-batches", type=int, default=1); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=35, help="stop claiming new batches after this long; the batch in flight is abandoned at max-minutes+10")
    a = ap.parse_args(argv); t0 = time.time(); L = dict(total=0, ok=0, err=0, jobs=0)
    rid = None if a.dry_run else log_run(a.shard, a.shards, 0, 0, 0, 0, 0, 'running')
    STATE.update(a=a, L=L, t0=t0, rid=rid, final=False)
    signal.signal(signal.SIGTERM, _on_term); signal.signal(signal.SIGINT, _on_term)
    atexit.register(_atexit)
    if rid: threading.Thread(target=_heartbeat, daemon=True).start()
    status, code = "ok", 0
    try: _loop(a, L, t0)
    except BaseException as e:
        status, code = "failed", 1; print(f"worker failed: {str(e)[:200]}", file=sys.stderr)
    STATE["final"] = True
    print(f"shard {a.shard}/{a.shards}: portals={L['total']} ok={L['ok']} err={L['err']} jobs_written={L['jobs']} secs={time.time()-t0:.0f} status={status}")
    if rid: log_run(a.shard, a.shards, time.time() - t0, L['total'], L['ok'], L['err'], L['jobs'], status, rid)
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(code)      # abandoned slow-site threads must not hold the process open

def _loop(a, L, t0):
    for _ in range(a.max_batches):
        if time.time() - t0 > a.max_minutes * 60: break
        rows = claim_and_lease(config.BATCH, a.shard, a.shards)
        if not rows: break
        deadline = min(time.time() + config.BATCH_MAX_MIN * 60, t0 + (a.max_minutes + 10) * 60)
        results, timed_out = crawl_batch(rows, deadline)
        o, e, j = write_results(results, a.dry_run, timed_out)
        L['total'] += len(rows); L['ok'] += o; L['err'] += e; L['jobs'] += j
        if timed_out: print(f"batch cap hit: {len(timed_out)} sites abandoned (backoff)", file=sys.stderr)
        _snapshot("running")

if __name__ == "__main__": main()
