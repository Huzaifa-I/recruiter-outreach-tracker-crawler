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
