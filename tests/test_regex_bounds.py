"""Bounds on HTML regexes: a pathological page must not go quadratic.

Regression for the f0ccb22 freeze class: unbounded lazy-dot patterns hold the GIL
for the whole match, so one bad page starves every worker thread incl. the
heartbeat. These cases must finish in well under a second each.
"""
import time, unittest
from jobcrawler.textutil import clean_description
from jobcrawler.tiers import tier1_inline


def timed(fn, *a):
    t0 = time.monotonic(); r = fn(*a); return r, time.monotonic() - t0


class T(unittest.TestCase):
    def test_clean_description_unclosed_script_huge(self):
        # 2 MB blob: closed script, then an unclosed <script> tail (broken template)
        blob = ("<p>Senior Engineer, Platform team. You will build and own core "
                "services, work with product and design, and ship weekly.</p>"
                "<script>var x=1;</script><p>Second paragraph of the role.</p>"
                "<script>" + "x" * 1_000_000 + "<script>" + "y" * 1_000_000 + "<script>" + "z" * 1_000_000)
        r, dt = timed(clean_description, blob)
        self.assertLess(dt, 3.0)
        self.assertIsNotNone(r)
        self.assertIn("Senior Engineer", r)

    def test_clean_description_keeps_text_and_strips_script(self):
        blob = "<p>Product Manager, Payments.</p><script>evil()</script><p>Own the roadmap end to end, partner with engineering.</p>"
        r = clean_description(blob)
        self.assertIsNotNone(r)
        self.assertNotIn("evil", r)
        self.assertIn("Product Manager", r)

    def test_tier1_inline_unclosed_window_blobs(self):
        # many window.* assignments, no closing </script> anywhere (directory page)
        html = "".join("<script>window.__STATE__ = {" + '"a":' + '1,' * 5000
                       for _ in range(400))
        r, dt = timed(tier1_inline, html)
        self.assertLess(dt, 3.0)
        self.assertEqual(r, [])

    def test_tier1_inline_still_extracts_next_data(self):
        html = ('<script id="__NEXT_DATA__" type="application/json">'
                '{"props":{"jobs":[{"title":"Software Engineer","id":"j1","url":"/jobs/j1"},'
                '{"title":"Data Analyst","id":"j2","url":"/jobs/j2"}]}}</script>')
        r, dt = timed(tier1_inline, html)
        self.assertLess(dt, 3.0)
        self.assertTrue(any("Software Engineer" in j.get("title", "") for j in r))


if __name__ == "__main__":
    unittest.main()
