"""Post-run checklist. Read-only. python -m jobcrawler.healthcheck [--since-minutes 120] [--baseline-bytes N] [--gate]
Prints a short summary plus ANOMALY lines; exit code 1 if any anomaly (so a workflow step can fail loudly).
--gate also evaluates the staged-ramp gate: OK to scale only if the last run per shard is clean."""
import argparse, sys
from . import db

MAX_ERR_RATE = 0.05          # ramp gate: at most 5% of portals may error
STALE_RUNNING_MIN = 120     # fallback when no heartbeat column (must exceed the 110 min job timeout)
HEARTBEAT_STALE_MIN = 15    # 'running' with no heartbeat for this long = interrupted

MAX_SHARD_SECS = 100 * 60    # a shard must finish well inside the 110 min workflow limit
DB_LIMIT_BYTES = 1_000_000_000   # Neon free project cap, warn at 50%

def checks(cur, since_minutes=120, baseline_bytes=None, gate=False):
    an, info, warn = [], [], []
    q = lambda sql, *a: (cur.execute(sql, a or None), cur.fetchall())[1]
    # 1. per-shard time and conclusion
    if not q("SELECT 1 FROM information_schema.columns WHERE table_name='bridge_run' AND column_name='updated_at'"): an.append("sql/005_heartbeat.sql not applied (bridge_run.updated_at missing)"); return info, an
    runs = q("""SELECT shard, shards,
                CASE WHEN status='running' AND coalesce(updated_at, started) < now() - %s * interval '1 minute' THEN 'interrupted' ELSE status END,
                secs, portals, ok, err, jobs, started FROM bridge_run
                WHERE started > now() - %s * interval '1 minute' ORDER BY started""", HEARTBEAT_STALE_MIN, since_minutes)
    for sh, shs, st, secs, po, ok, er, jb, started in runs:
        info.append(f"shard {sh}/{shs}: {st} {secs or 0:.0f}s portals={po} ok={ok} err={er} jobs={jb}")
        if st == "interrupted": warn.append(f"shard {sh} interrupted (cancelled or timed out); its unfinished portals stay leased 15 min, then are due again")
        elif st == "running": pass            # still in flight
        elif st != "ok": an.append(f"shard {sh} status={st}")
        if secs and secs > MAX_SHARD_SECS: an.append(f"shard {sh} slow: {secs:.0f}s")
        if po and er / po > MAX_ERR_RATE: an.append(f"shard {sh} error rate {er}/{po}")
    if runs and all(r[2] == "interrupted" for r in runs): an.append("every shard in the window was interrupted, nothing completed")
    if not runs: an.append("no bridge_run rows in window (did the workers run, is sql/004 applied?)")
    # 2. exact duplicates. text_list jobs legitimately share the careers-page url, so the key includes the title.
    d = q("SELECT count(*) FROM (SELECT portal_id, url, title FROM bridge_job WHERE NOT closed GROUP BY 1,2,3 HAVING count(*)>1) x")[0][0]
    info.append(f"exact duplicate (portal,url,title) groups: {d}")
    if d: an.append(f"{d} exact duplicate (portal,url,title) groups")
    big = q("SELECT count(*) FROM bridge_crawl WHERE method='text_list' AND n_jobs > 100")[0][0]
    info.append(f"text_list portals with >100 jobs: {big}")
    if big: an.append(f"{big} text_list portals with >100 jobs (likely archive/news junk)")
    # 3. fail counts
    f = q("SELECT count(*) FILTER (WHERE fail_count>=3), count(*) FILTER (WHERE fail_count>=5), count(*) FROM bridge_crawl")[0]
    info.append(f"portals={f[2]} fail>=3: {f[0]} dead(fail>=5): {f[1]}")
    if f[2] and f[0] / f[2] > 0.10: an.append(f"{f[0]} of {f[2]} portals have fail_count>=3")
    # 4. count consistency (also catches partial writes)
    bad = q("""SELECT count(*) FROM bridge_crawl c LEFT JOIN (SELECT portal_id, count(*) n FROM bridge_job WHERE NOT closed GROUP BY 1) j USING (portal_id)
               WHERE c.last_ok IS NOT NULL AND c.method IS DISTINCT FROM 'none' AND coalesce(c.n_jobs,0) <> coalesce(j.n,0)""")[0][0]
    orph = q("SELECT count(*) FROM bridge_job j WHERE NOT EXISTS (SELECT 1 FROM bridge_crawl c WHERE c.portal_id=j.portal_id)")[0][0]
    info.append(f"bridge_crawl.n_jobs mismatches: {bad}, orphan jobs: {orph}")
    if bad: an.append(f"{bad} portals where n_jobs != open job rows (partial write?)")
    if orph: an.append(f"{orph} jobs without a bridge_crawl row")
    # 5. running shards (an in-flight lease is expected only while one is running)
    running = q("SELECT count(*) FROM bridge_run WHERE status='running' AND coalesce(updated_at, started) > now() - interval '15 minutes'")[0][0]
    slow = q("SELECT count(*) FILTER (WHERE slow_count>0), count(*) FILTER (WHERE last_secs>30) FROM bridge_crawl")[0]
    info.append(f"slow portals: {slow[0]} backed off after timeouts, {slow[1]} last crawl >30s")
    info.append(f"running shards: {running}")
    # 6. DB growth
    size = q("SELECT pg_database_size(current_database())")[0][0]
    tb = q("SELECT relname, pg_total_relation_size(oid) FROM pg_class WHERE relname IN ('bridge_job','bridge_crawl','bridge_seen','bridge_run') AND relkind='r' ORDER BY 2 DESC")
    info.append(f"db size {size/1e6:.0f} MB; " + ", ".join(f"{n}={s/1e6:.1f}MB" for n, s in tb))
    if size > DB_LIMIT_BYTES * 0.5: an.append(f"db size {size/1e6:.0f} MB is over 50% of the 1 GB free cap")
    if baseline_bytes and size - baseline_bytes > 0.1 * DB_LIMIT_BYTES: an.append(f"db grew {(size-baseline_bytes)/1e6:.0f} MB since baseline")
    # 7. connection health
    idle = q("SELECT count(*) FROM pg_stat_activity WHERE state LIKE 'idle in transaction%' AND datname=current_database() AND pid<>pg_backend_pid()")[0][0]
    info.append(f"idle-in-transaction sessions: {idle}")
    if idle and not running: an.append(f"{idle} idle-in-transaction sessions with no shard running")
    # 8. ramp gate
    if gate:
        bad_runs = [r for r in runs if r[2] not in ("ok", "interrupted")]
        tot = sum(r[4] or 0 for r in runs); er = sum(r[6] or 0 for r in runs)
        done = sum(r[4] or 0 for r in runs if r[2] == "ok")
        okgate = done > 0 and not bad_runs and not an and (tot == 0 or er / tot <= MAX_ERR_RATE)   # interrupted shards warn but do not fail the gate
        info.append(f"RAMP GATE: {'PASS, safe to scale' if okgate else 'FAIL, do not scale'} (portals={tot}, errors={er})")
        if not okgate: an.append("ramp gate failed")
    info += ["WARNING: " + w for w in warn]
    return info, an

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--since-minutes", type=int, default=120)
    ap.add_argument("--baseline-bytes", type=int); ap.add_argument("--gate", action="store_true")
    a = ap.parse_args(argv); conn = db.connect(); conn.set_session(readonly=True, autocommit=True)
    info, an = checks(conn.cursor(), a.since_minutes, a.baseline_bytes, a.gate)
    print("\n".join(info)); print("\n".join("ANOMALY: " + x for x in an) if an else "no anomalies")
    sys.exit(1 if an else 0)

if __name__ == "__main__": main()
