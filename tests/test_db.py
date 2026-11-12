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
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql"): cls.cur.execute(open(os.path.join(SQL, f)).read())
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
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql"): c.cursor().execute(open(os.path.join(SQL, f)).read())
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
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql", "004_run_log.sql", "005_heartbeat.sql"): cur.execute(open(os.path.join(SQL, f)).read())
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
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql", "004_run_log.sql", "005_heartbeat.sql"): cur.execute(open(os.path.join(SQL, f)).read())
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
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql"): cur.execute(open(os.path.join(SQL, f)).read())
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
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql", "004_run_log.sql", "005_heartbeat.sql"): self.cur.execute(open(os.path.join(SQL, f)).read())
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

@unittest.skipIf(pgserver is None, "pgserver not installed")
class CapAndHeartbeatTests(unittest.TestCase):
    def setUp(self):
        from jobcrawler import healthcheck
        self.hc = healthcheck
        srv = pgserver.get_server(tempfile.mkdtemp()); self.uri = srv.get_uri(); self.c = psycopg2.connect(self.uri); self.c.autocommit = True; self.cur = self.c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql", "004_run_log.sql", "005_heartbeat.sql"): self.cur.execute(open(os.path.join(SQL, f)).read())
    def test_no_heartbeat_for_15_min_is_interrupted_but_recent_heartbeat_is_running(self):
        self.cur.execute("INSERT INTO bridge_run (shard,shards,secs,portals,ok,err,jobs,status) VALUES (0,2,5,50,50,0,1,'ok')")
        self.cur.execute("INSERT INTO bridge_run (shard,shards,started,updated_at,status) VALUES (1,2,now()-interval '30 minutes',now()-interval '20 minutes','running')")
        info, an = self.hc.checks(self.cur, gate=True); self.assertEqual(an, [], an); self.assertTrue(any("WARNING" in i and "interrupted" in i for i in info))
        self.cur.execute("UPDATE bridge_run SET updated_at=now()-interval '2 minutes' WHERE shard=1")
        info, an = self.hc.checks(self.cur); self.assertFalse(any("interrupted" in i for i in info))
    def test_missing_migration_is_reported(self):
        self.cur.execute("ALTER TABLE bridge_run DROP COLUMN updated_at")
        _, an = self.hc.checks(self.cur); self.assertTrue(any("005" in x for x in an))
    def test_crawl_batch_abandons_slow_sites_at_deadline(self):
        import time
        from jobcrawler import worker, config
        def fake(row):
            time.sleep(0.05 if row[0] < 3 else 8); return row, dict(method="x", confidence=1, jobs=[]), None, 0.05
        orig, th = worker.crawl, config.THREADS; worker.crawl = fake; config.THREADS = 4
        try:
            rows = [(i, "u", None, None, 2, 0, None) for i in range(1, 6)]; t = time.time()
            results, timed_out = worker.crawl_batch(rows, time.time() + 1.0)
        finally: worker.crawl, config.THREADS = orig, th
        self.assertLess(time.time() - t, 3); self.assertEqual(sorted(r[0][0] for r in results), [1, 2]); self.assertEqual(sorted(r[0] for r in timed_out), [3, 4, 5])
    def test_timeout_backs_off_and_does_not_count_as_failure(self):
        from jobcrawler import worker
        self.cur.execute("INSERT INTO bridge_crawl (portal_id,career_url) VALUES (1,'x'),(2,'y')")
        orig = db.connect; db.connect = lambda: psycopg2.connect(self.uri)
        try:
            row = (1, "x", None, None, 2, 0, None)
            worker.write_results([], timed_out=[row]); worker.write_results([], timed_out=[row])
        finally: db.connect = orig
        self.cur.execute("SELECT slow_count, fail_count, next_check > now() + interval '90 minutes' FROM bridge_crawl WHERE portal_id=1"); self.assertEqual(self.cur.fetchone(), (2, 0, True))
        self.cur.execute("SELECT slow_count FROM bridge_crawl WHERE portal_id=2"); self.assertEqual(self.cur.fetchone()[0], 0)
    def test_heartbeat_row_updates_while_running(self):
        import subprocess, sys, time, threading, http.server, signal as sg
        from jobcrawler import worker
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self): time.sleep(30)
            def log_message(self, *a): pass
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.cur.execute("INSERT INTO bridge_crawl (portal_id,career_url) VALUES (1,%s)", (f"http://127.0.0.1:{httpd.server_port}/careers",))
        env = dict(os.environ, DATABASE_URL=self.uri, CRAWLER_CONTACT="t", CRAWLER_BATCH_MAX_MIN="0.05", PYTHONPATH=os.path.join(os.path.dirname(__file__), ".."))
        t = time.time(); p = subprocess.run([sys.executable, "-m", "jobcrawler.worker"], env=env, capture_output=True, text=True, timeout=60); httpd.shutdown()
        self.assertLess(time.time() - t, 30)     # batch cap (3 s) ended the batch, process exited despite the hung site
        self.cur.execute("SELECT status, err, updated_at IS NOT NULL FROM bridge_run"); self.assertEqual(self.cur.fetchone(), ("ok", 1, True))
        self.cur.execute("SELECT slow_count FROM bridge_crawl"); self.assertEqual(self.cur.fetchone()[0], 1)
if __name__ == "__main__": unittest.main()

class ExpTests(unittest.TestCase):
    def test_exp_hints(self):
        from jobcrawler.classify import exp_min
        self.assertEqual([exp_min(t) for t in ("PM (2-4 years)", "PM 3+ yrs", "Fresher Sales", "Intern - Product", "Senior PM", "Engineer", "Room 2024 yearly")], [2.0, 3.0, 0.0, 0.0, None, None, None])

@unittest.skipIf(pgserver is None, "pgserver not installed")
class ApiDigestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv = pgserver.get_server(tempfile.mkdtemp()); cls.uri = srv.get_uri(); cls.c = psycopg2.connect(cls.uri); cls.c.autocommit = True; cls.cur = cls.c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql"): cls.cur.execute(open(os.path.join(SQL, f)).read())
        cls.cur.execute("INSERT INTO bridge_crawl (portal_id,career_url) VALUES (1,'x')")
        now = dt.datetime.now(dt.timezone.utc)
        jobs = [dict(ext=str(i), title=("Product Manager (5+ years)" if i == 3 else "Product Manager" if i % 2 else "Engineer"), url=f"u{i}", loc="Bengaluru, India", posted=(now - dt.timedelta(days=i)).isoformat()) for i in range(1, 31)]
        db.save_result(cls.cur, 1, 2, 0, dict(method="x", confidence=1, jobs=jobs), classify, 100)
    def test_paging_newest_first_no_repeat_and_exp_filter(self):
        from jobcrawler import api
        p = api.profile_from_user("product manager"); cur = None; seen = []; ts = []
        while True:
            r = api.list_jobs(self.cur, p, cur, 4); seen += [i["job_key"] for i in r["items"]]; ts += [i["sort_ts"] for i in r["items"]]; cur = r["next_cursor"]
            if not cur: break
        self.assertEqual(len(seen), len(set(seen))); self.assertEqual(ts, sorted(ts, reverse=True))
        self.assertEqual(len(seen), 14)          # 15 odd PM titles minus the 5+ years one (exp_min 5 > 4)
        self.assertEqual(len(api.list_jobs(self.cur, api.profile_from_user(None, any_role=True), None, 100)["items"]), 29)
        with self.assertRaises(ValueError): api.list_jobs(self.cur, p, "garbage")
    def test_digest_marks_seen_only_after_successful_send(self):
        from jobcrawler import digest
        p = db.profile_params(); sent = []
        def bad(u, s, b): raise RuntimeError("channel down")
        conn = psycopg2.connect(self.uri)
        with self.assertRaises(RuntimeError): digest.run_for_user(conn, 42, p, bad)
        n = digest.run_for_user(conn, 42, p, lambda u, s, b: sent.append((s, b)), max_items=10); n2 = digest.run_for_user(conn, 42, p, lambda u, s, b: sent.append(1))
        self.assertEqual((n, n2, len(sent)), (14, 0, 1)); self.assertIn("14 new matching jobs", sent[0][0]); self.assertIn("4 more in the app", sent[0][1])

@unittest.skipIf(pgserver is None, "pgserver not installed")
class RoleSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv = pgserver.get_server(tempfile.mkdtemp()); cls.c = psycopg2.connect(srv.get_uri()); cls.c.autocommit = True; cls.cur = cls.c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql", "006_title_search.sql"): cls.cur.execute(open(os.path.join(SQL, f)).read())
        cls.cur.execute("INSERT INTO bridge_crawl (portal_id,career_url) VALUES (1,'x')")
        titles = ["Product Manager", "Associate Product Manager", "Data Analyst", "Senior Data Analyst", "Software Engineer", "100% Sales_Lead", "Data Scientist"]
        jobs = [dict(ext=str(i), title=t, url=f"u{i}", loc="India", posted=None) for i, t in enumerate(titles)]
        db.save_result(cls.cur, 1, 2, 0, dict(method="x", confidence=1, jobs=jobs), classify, 100)
    def titles(self, prof):
        from jobcrawler import api
        return sorted(i["title"] for i in api.list_jobs(self.cur, prof, None, 100)["items"])
    def test_role_search_filters_to_that_role(self):
        from jobcrawler import api
        self.assertEqual(self.titles(api.profile_from_user("data analyst")), ["Data Analyst"])           # senior excluded by default
        self.assertEqual(self.titles(api.profile_from_user("data analyst", allow_senior=True)), ["Data Analyst", "Senior Data Analyst"])
        self.assertEqual(self.titles(api.profile_from_user(["product manager"])), ["Associate Product Manager", "Product Manager"])
        self.assertEqual(self.titles(api.profile_from_user(["data analyst", "software engineer"])), ["Data Analyst", "Software Engineer"])
        self.assertEqual(self.titles(api.profile_from_user("pm")), ["Associate Product Manager", "Product Manager"])
    def test_no_silent_all_roles(self):
        from jobcrawler import api
        for bad in (None, "", [], ["  "]):
            with self.assertRaises(ValueError): api.profile_from_user(bad)
        self.assertEqual(len(self.titles(api.profile_from_user(None, any_role=True))), 7 - 1)         # explicit any role: all but the senior one
    def test_like_wildcards_are_literal(self):
        from jobcrawler import api
        self.assertEqual(self.titles(api.profile_from_user("100% sales_lead")), ["100% Sales_Lead"])
        self.assertEqual(self.titles(api.profile_from_user("%")), ["100% Sales_Lead"])

class TextUtilTests(unittest.TestCase):
    def test_clean_description(self):
        from jobcrawler.textutil import clean_description, clean_company
        gh = "&lt;p&gt;We are hiring a &lt;strong&gt;Product Manager&lt;/strong&gt; to own roadmap &amp;amp; delivery.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;3+ years&lt;/li&gt;&lt;/ul&gt;"
        d = clean_description(gh); self.assertIn("Product Manager", d); self.assertIn("- 3+ years", d); self.assertNotIn("<", d); self.assertNotIn("&lt;", d)
        self.assertIsNone(clean_description("Apply now")); self.assertIsNone(clean_description(None)); self.assertIsNone(clean_description(123))
        long = clean_description("word " * 5000); self.assertLessEqual(len(long), 8003); self.assertTrue(long.endswith("..."))
        self.assertIn("ok", clean_description("<script>x()</script><p>" + "ok " * 30 + "</p>")); self.assertNotIn("x()", clean_description("<script>x()</script><p>" + "ok " * 30 + "</p>"))
        self.assertEqual(clean_company({"@type": "Organization", "name": " Acme  Corp "}), "Acme Corp"); self.assertIsNone(clean_company(None))
    def test_jsonld_jobs_fields(self):
        from jobcrawler.extract import jsonld_jobs, jsonld
        h = '<script type="application/ld+json">{"@type":"JobPosting","title":"Product Manager","url":"https://a.com/j/1","datePosted":"2026-11-01","description":"<p>' + "Own the roadmap. " * 5 + '</p>","hiringOrganization":{"@type":"Organization","name":"Acme"},"jobLocation":{"address":{"addressLocality":"Pune","addressCountry":"IN"}}}</script>'
        j = jsonld_jobs(h)["https://a.com/j/1"]; self.assertEqual((j["posted"], j["company"]["name"], j["loc"]), ("2026-11-01", "Acme", "Pune, IN")); self.assertEqual(list(jsonld(h)), ["https://a.com/j/1"])
    def test_ats_payload_mapping(self):
        from jobcrawler import connectors
        gh = {"jobs": [{"id": 7, "title": "PM", "absolute_url": "u", "location": {"name": "India"}, "first_published": "2026-10-01T00:00:00Z", "content": "&lt;p&gt;" + "Role text. " * 10 + "&lt;/p&gt;", "company_name": "Acme"}]}
        orig = connectors.get_json; seen = []
        connectors.get_json = lambda url, data=None: (seen.append(url), (200, gh))[1]
        try: jobs, s = connectors.greenhouse("acme")
        finally: connectors.get_json = orig
        self.assertEqual(len(seen), 1); self.assertIn("content=true", seen[0]); self.assertEqual((jobs[0]["company"], jobs[0]["posted"]), ("Acme", "2026-10-01T00:00:00Z")); self.assertIn("Role text", jobs[0]["description"] if "<" not in jobs[0]["description"] else "Role text")

@unittest.skipIf(pgserver is None, "pgserver not installed")
class EnrichStoreTests(unittest.TestCase):
    def test_store_enrichment_and_keep_on_recrawl(self):
        from jobcrawler import api
        srv = pgserver.get_server(tempfile.mkdtemp()); c = psycopg2.connect(srv.get_uri()); c.autocommit = True; cur = c.cursor()
        for f in ("001_bridge.sql", "002_compute_at_read.sql", "007_enrichment.sql"): cur.execute(open(os.path.join(SQL, f)).read())
        cur.execute(open(os.path.join(SQL, "007_enrichment.sql")).read())          # idempotent
        cur.execute("INSERT INTO bridge_crawl (portal_id,career_url) VALUES (1,'x')")
        j = dict(ext="a", title="Product Manager", url="u", loc="India", posted="2026-10-01T00:00:00Z", description="<p>" + "Own the roadmap. " * 800 + "</p>", company={"name": "Acme"})
        db.save_result(cur, 1, 2, 0, dict(method="x", confidence=3, jobs=[j]), classify, 100)
        d = api.get_job(cur, db.job_key(1, "a")); self.assertEqual((d["company"], d["posted_at"][:10]), ("Acme", "2026-10-01")); self.assertLessEqual(len(d["description"]), 8003)
        j2 = dict(ext="a", title="Product Manager", url="u", loc="India", posted=None, description=None, company=None)     # later crawl without the fields keeps them
        db.save_result(cur, 1, 2, 0, dict(method="x", confidence=1, jobs=[j2]), classify, 100)
        d = api.get_job(cur, db.job_key(1, "a")); self.assertEqual((d["company"], d["posted_at"][:10]), ("Acme", "2026-10-01")); self.assertTrue(d["description"])
        r = api.list_jobs(cur, api.profile_from_user("product manager"), None, 10)["items"][0]; self.assertEqual((r["company"], "description" in r), ("Acme", False))
        self.assertIsNone(api.get_job(cur, "nope"))
if __name__ == "__main__": unittest.main()
