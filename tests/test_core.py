import unittest
from jobcrawler.classify import classify
from jobcrawler.connectors import detect
from jobcrawler.extract import page_links, jsonld

class T(unittest.TestCase):
    def test_classify(self):
        c = classify("Senior Product Manager, Payments", "Bengaluru, India (Remote)")
        self.assertEqual((c["role"], c["level"], c["india"], c["remote"]), ("pm", "senior", 1, 1))
        self.assertEqual(classify("Software Engineer", "Berlin")["role"], "other")
    def test_detect(self):
        self.assertEqual(detect("https://boards.greenhouse.io/acme"), ("greenhouse", "acme"))
        self.assertEqual(detect("https://x.wd5.myworkdayjobs.com/en-US/Site"), ("workday", "x"))
        self.assertEqual(detect("https://boards.greenhouse.io/embed/job_board?for=acme"), ("greenhouse", "acme"))
        self.assertEqual(detect("nothing here"), (None, None))
    def test_page_links_filters_hubs(self):
        html = '<a href="/jobs/senior-engineer-12">Senior Engineer</a><a href="/careers/salary">Salary guide manager</a><a href="https://other.com/jobs/engineer-x">Engineer role</a>'
        r = page_links("https://acme.com/careers", html)
        self.assertEqual(list(r), ["https://acme.com/jobs/senior-engineer-12"])
    def test_jsonld_requires_title(self):
        h = '<script type="application/ld+json">{"@type":"JobPosting","title":"Analyst","url":"https://a.com/j/1"}</script><script type="application/ld+json">{"@type":"JobPosting","title":"Careers at A"}</script>'
        self.assertEqual(list(jsonld(h)), ["https://a.com/j/1"])
if __name__ == "__main__": unittest.main()
