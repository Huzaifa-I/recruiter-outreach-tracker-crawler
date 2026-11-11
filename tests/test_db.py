import os, tempfile, unittest, datetime as dt
try:
    import pgserver, psycopg2
except ImportError:
    pgserver = None
from jobcrawler import db
from jobcrawler.classify import classify
from jobcrawler.dates import parse_posted

SQL = os.path.join(os.path.dirname(__file__), "..", "sql")

@unittest.skipIf(pgserver is None, "pgserver not installed")
class DbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = pgserver.get_server(tempfile.mkdtemp())
        cls.conn = psycopg2.connect(cls.srv.get_uri()); cls.conn.autocommit = True; cls.cur = cls.conn.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql"): cls.cur.execute(open(os.path.join(SQL, f)).read())
        cls.cur.execute("INSERT INTO bridge_crawl (portal_id, career_url) VALUES (1,'x')")
    def res(self, jobs): return dict(method="x", confidence=1, jobs=jobs)
    def test_duplicate_ext_in_one_batch(self):
        j = lambda e, t: dict(ext=e, title=t, url="u" + str(e), loc="", posted=None)
        n = db.save_result(self.cur, 1, 2, 0, self.res([j("a", "Product Manager"), j("a", "Product Manager"), j("b", "Engineer")]), classify, 100)
        self.assertEqual(n, 2)
        self.cur.execute("SELECT count(*) FROM bridge_job WHERE portal_id=1"); self.assertEqual(self.cur.fetchone()[0], 2)
        self.cur.execute("SELECT n_jobs FROM bridge_crawl WHERE portal_id=1"); self.assertEqual(self.cur.fetchone()[0], 2)
        db.save_result(self.cur, 1, 2, 0, self.res([j("a", "Product Manager"), j("a", "x")]), classify, 100)  # re-save, still no error
    def test_digest_only_once(self):
        p = db.profile_params()
        self.cur.execute("INSERT INTO bridge_job (job_key,portal_id,title,url,role,level,india,remote,sort_ts,first_seen,last_seen) VALUES ('k1',9,'PM','u','pm','unspecified',1,0,now(),now(),now())")
        self.assertEqual(len(db.digest(self.cur, 5, p)), 1); self.assertEqual(len(db.digest(self.cur, 5, p)), 0)
    def test_parse_posted(self):
        self.assertIsNotNone(parse_posted("Posted 3 Days Ago")); self.assertIsNone(parse_posted("2999-01-01")); self.assertIsNone(parse_posted("junk"))
        self.assertIsNotNone(parse_posted(1700000000000))
if __name__ == "__main__": unittest.main()

@unittest.skipIf(pgserver is None, "pgserver not installed")
class LeaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = pgserver.get_server(tempfile.mkdtemp()); cls.uri = cls.srv.get_uri()
        c = psycopg2.connect(cls.uri); c.autocommit = True
        for f in ("001_bridge.sql", "002_compute_at_read.sql"): c.cursor().execute(open(os.path.join(SQL, f)).read())
        cls.c = c
    def seed(self, n):
        cur = self.c.cursor(); cur.execute("TRUNCATE bridge_crawl, bridge_job")
        cur.execute("INSERT INTO bridge_crawl (portal_id, career_url) SELECT g, 'http://x/' || g FROM generate_series(1,%s) g", (n,))
    def claim(self, n=100):
        conn = psycopg2.connect(self.uri)
        try:
            with conn, conn.cursor() as cur:
                rows = db.claim_due(cur, n, 0, 1); db.lease(cur, [r[0] for r in rows])
            return {r[0] for r in rows}
        finally: conn.close()
    def test_lease_prevents_double_claim(self):
        self.seed(10); a = self.claim(6); b = self.claim(6)
        self.assertEqual(len(a), 6); self.assertEqual(len(b), 4); self.assertFalse(a & b)
    def test_crashed_worker_rows_due_after_lease(self):
        self.seed(5); self.assertEqual(len(self.claim()), 5); self.assertEqual(len(self.claim()), 0)
        self.c.cursor().execute("UPDATE bridge_crawl SET next_check = now() - interval '1 second'")  # lease expired
        self.assertEqual(len(self.claim()), 5)
    def test_no_open_transaction_during_crawl_and_results_overwrite_lease(self):
        import time
        from jobcrawler import worker
        self.seed(3); rows_ids = self.claim(); self.assertEqual(len(rows_ids), 3)
        # "crawl": no connection of ours is open; server must show no idle-in-transaction sessions from us
        time.sleep(0.2)
        cur = self.c.cursor(); cur.execute("SELECT count(*) FROM pg_stat_activity WHERE state LIKE 'idle in transaction%'"); self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT portal_id,career_url,ats,ats_token,tier,fail_count,job_hash FROM bridge_crawl ORDER BY portal_id"); rows = cur.fetchall()
        orig = db.connect; db.connect = lambda: psycopg2.connect(self.uri)
        try:
            res = dict(method="x", confidence=1, jobs=[dict(ext="a", title="Product Manager", url="u", loc="", posted=None)])
            ok, err, jobs = worker.write_results([(rows[0], res, None), (rows[1], None, "boom"), (rows[2], res, None)])
        finally: db.connect = orig
        self.assertEqual((ok, err, jobs), (2, 1, 2))
        cur.execute("SELECT portal_id, next_check > now() + interval '20 hours', fail_count FROM bridge_crawl ORDER BY portal_id"); r = cur.fetchall()
        self.assertTrue(r[0][1] and r[2][1]); self.assertEqual(r[1][2], 1)   # real next_check (daily) replaced the 15 min lease
if __name__ == "__main__": unittest.main()

@unittest.skipIf(pgserver is None, "pgserver not installed")
class HealthTests(unittest.TestCase):
    def test_healthcheck_detects_anomalies(self):
        from jobcrawler import healthcheck
        srv = pgserver.get_server(tempfile.mkdtemp()); c = psycopg2.connect(srv.get_uri()); c.autocommit = True; cur = c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "004_run_log.sql"): cur.execute(open(os.path.join(SQL, f)).read())
        cur.execute("INSERT INTO bridge_crawl (portal_id,career_url,last_ok,method,n_jobs) VALUES (1,'x',now(),'sitemap',2)")
        cur.execute("INSERT INTO bridge_run (shard,shards,secs,portals,ok,err,jobs,status) VALUES (0,2,10,100,100,0,2,'ok'),(1,2,10,100,50,50,0,'failed')")
        cur.execute("INSERT INTO bridge_job (job_key,portal_id,title,url,sort_ts,first_seen,last_seen) VALUES ('a',1,'t','u',now(),now(),now()),('b',1,'t','u',now(),now(),now()),('c',7,'t','u2',now(),now(),now())")
        info, an = healthcheck.checks(cur, gate=True); text = " | ".join(an)
        self.assertIn("shard 1 status=failed", text); self.assertIn("exact duplicate", text); self.assertIn("without a bridge_crawl row", text); self.assertIn("ramp gate failed", text)
        cur.execute("DELETE FROM bridge_run WHERE shard=1"); cur.execute("DELETE FROM bridge_job WHERE job_key IN ('b','c')")
        cur.execute("UPDATE bridge_crawl SET n_jobs=1")
        info, an = healthcheck.checks(cur, gate=True); self.assertEqual(an, [], an); self.assertTrue(any("PASS" in i for i in info))
if __name__ == "__main__": unittest.main()

@unittest.skipIf(pgserver is None, "pgserver not installed")
class DupCheckTests(unittest.TestCase):
    def test_same_url_distinct_titles_pass_exact_dup_fails(self):
        from jobcrawler import healthcheck
        srv = pgserver.get_server(tempfile.mkdtemp()); c = psycopg2.connect(srv.get_uri()); c.autocommit = True; cur = c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "004_run_log.sql"): cur.execute(open(os.path.join(SQL, f)).read())
        ins = "INSERT INTO bridge_job (job_key,portal_id,title,url,sort_ts,first_seen,last_seen) VALUES (%s,1,%s,'https://a/careers',now(),now(),now())"
        cur.execute(ins, ("k1", "Analyst")); cur.execute(ins, ("k2", "Designer"))
        _, an = healthcheck.checks(cur); self.assertFalse([x for x in an if "duplicate" in x])
        cur.execute(ins, ("k3", "Analyst"))
        _, an = healthcheck.checks(cur); self.assertTrue([x for x in an if "exact duplicate (portal,url,title)" in x])

class TextListTests(unittest.TestCase):
    def test_archive_and_long_lists_filtered(self):
        from jobcrawler.textlist import parse_text_list
        arch = "".join(f"<li>Research Analyst Complaint {m} 2024</li>" for m in ("January","February","March","April","May","June"))
        good = "<li>Product Manager</li><li>Senior Engineer</li><li>Sales Executive</li>"
        self.assertEqual(parse_text_list("<body>" + arch + "</body>"), [])
        self.assertEqual(parse_text_list("<body>" + arch + good + "</body>"), ["Product Manager", "Senior Engineer", "Sales Executive"])
        many = "".join(f"<li>Sales Executive Team{chr(97+i%26)}{chr(97+i//26)}</li>" for i in range(80))
        self.assertEqual(parse_text_list("<body>" + many + "</body>"), [])
if __name__ == "__main__": unittest.main()

@unittest.skipIf(pgserver is None, "pgserver not installed")
class TextListCloseTests(unittest.TestCase):
    def test_text_list_none_closes_stale_rows_others_kept(self):
        srv = pgserver.get_server(tempfile.mkdtemp()); c = psycopg2.connect(srv.get_uri()); c.autocommit = True; cur = c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql"): cur.execute(open(os.path.join(SQL, f)).read())
        cur.execute("INSERT INTO bridge_crawl (portal_id,career_url,method) VALUES (1,'x','text_list'),(2,'y','sitemap')")
        cur.execute("INSERT INTO bridge_job (job_key,portal_id,title,url,sort_ts,first_seen,last_seen) VALUES ('a',1,'t','u',now(),now(),now()),('b',2,'t','u',now(),now(),now())")
        none = dict(method="none", confidence=0, jobs=[])
        db.save_result(cur, 1, 3, 0, none, classify, 100); db.save_result(cur, 2, 3, 0, none, classify, 100)
        cur.execute("SELECT job_key, closed FROM bridge_job ORDER BY 1"); self.assertEqual(cur.fetchall(), [("a", True), ("b", False)])
if __name__ == "__main__": unittest.main()

@unittest.skipIf(pgserver is None, "pgserver not installed")
class InterruptedTests(unittest.TestCase):
    def setUp(self):
        from jobcrawler import healthcheck
        self.hc = healthcheck
        srv = pgserver.get_server(tempfile.mkdtemp()); self.uri = srv.get_uri(); self.c = psycopg2.connect(self.uri); self.c.autocommit = True; self.cur = self.c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "004_run_log.sql"): self.cur.execute(open(os.path.join(SQL, f)).read())
    def test_stale_running_is_warning_not_anomaly(self):
        self.cur.execute("INSERT INTO bridge_run (shard,shards,secs,portals,ok,err,jobs,status) VALUES (0,3,100,300,300,0,50,'ok')")
        self.cur.execute("INSERT INTO bridge_run (shard,shards,started,status) VALUES (1,3,now()-interval '70 minutes','running'),(2,3,now()-interval '70 minutes','running')")
        info, an = self.hc.checks(self.cur, since_minutes=180, gate=True)
        self.assertEqual(an, [], an); self.assertTrue(any("WARNING" in i and "interrupted" in i for i in info)); self.assertTrue(any("PASS" in i for i in info))
    def test_fresh_running_is_not_interrupted_and_all_interrupted_fails(self):
        self.cur.execute("INSERT INTO bridge_run (shard,shards,status) VALUES (0,1,'running')")
        info, an = self.hc.checks(self.cur, gate=False); self.assertEqual(an, [], an); self.assertFalse(any("interrupted" in i for i in info))
        self.cur.execute("UPDATE bridge_run SET started=now()-interval '90 minutes'")
        info, an = self.hc.checks(self.cur, since_minutes=180, gate=True); self.assertTrue(any("every shard" in x for x in an))
    def test_failed_still_anomaly(self):
        self.cur.execute("INSERT INTO bridge_run (shard,shards,secs,portals,ok,err,status) VALUES (0,1,5,10,5,5,'failed')")
        _, an = self.hc.checks(self.cur); self.assertTrue(any("status=failed" in x for x in an))
    def test_sigterm_marks_run_interrupted(self):
        import subprocess, sys, time, threading, http.server, signal as sg
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self): time.sleep(30)
            def log_message(self, *a): pass
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.cur.execute("INSERT INTO bridge_crawl (portal_id,career_url) VALUES (1,%s)", (f"http://127.0.0.1:{httpd.server_port}/careers",))
        env = dict(os.environ, DATABASE_URL=self.uri, CRAWLER_CONTACT="test", PYTHONPATH=os.path.join(os.path.dirname(__file__), ".."))
        p = subprocess.Popen([sys.executable, "-m", "jobcrawler.worker"], env=env, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        for _ in range(50):
            self.cur.execute("SELECT count(*) FROM bridge_run"); time.sleep(0.2)
            if self.cur.fetchone()[0]: break
        time.sleep(1.5); p.send_signal(sg.SIGTERM); p.wait(10); httpd.shutdown()
        self.cur.execute("SELECT status FROM bridge_run"); self.assertEqual(self.cur.fetchone()[0], "interrupted")
if __name__ == "__main__": unittest.main()
