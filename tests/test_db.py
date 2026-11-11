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
