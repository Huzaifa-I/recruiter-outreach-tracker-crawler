"""One function per source. Each returns (jobs, status) where jobs is a list of
dict(ext, title, url, loc, posted). Only public endpoints are used."""
import re, json, html as H
from urllib.parse import urljoin, urlparse
from .http import get, get_json

PATTERNS = [
 ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
 ("lever", r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)"),
 ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
 ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)"),
 ("workable", r"apply\.workable\.com/([\w-]+)"),
 ("recruitee", r"([\w-]+)\.recruitee\.com"),
 ("workday", r"([\w-]+)\.wd\d+\.myworkdayjobs\.com"),
 ("oracle", r"([\w-]+\.fa\.[\w]+\.oraclecloud\.com)"),
 ("zoho", r"([\w-]+)\.zohorecruit\.(?:com|in|eu)"),
 ("icims", r"([\w-]+)\.icims\.com"),
]
BAD_TOKENS = {"embed", "js", "v0", "widget", "job_board"}

def detect(text):
    for k, p in PATTERNS:
        m = re.search(p, text, re.I)
        if m and m.group(1).lower() not in BAD_TOKENS: return k, m.group(1)
    return None, None

def greenhouse(tok):
    s, d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs")
    if s != 200 or not d: return [], s
    return [dict(ext=str(j["id"]), title=j["title"], url=j["absolute_url"], loc=(j.get("location") or {}).get("name", ""), posted=j.get("first_published") or j.get("updated_at")) for j in d["jobs"]], s

def lever(tok):
    s, d = get_json(f"https://api.lever.co/v0/postings/{tok}?mode=json")
    if s != 200 or not isinstance(d, list): return [], s
    return [dict(ext=j["id"], title=j["text"], url=j["hostedUrl"], loc=(j.get("categories") or {}).get("location") or "", posted=j.get("createdAt")) for j in d], s

def ashby(tok):
    s, d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")
    if s != 200 or not d: return [], s
    return [dict(ext=j["id"], title=j["title"], url=j["jobUrl"], loc=j.get("location") or "", posted=j.get("publishedAt")) for j in d.get("jobs", [])], s

def smartrecruiters(tok):
    out, off, s = [], 0, 200
    while off < 1000:
        s, d = get_json(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?limit=100&offset={off}")
        if s != 200 or not d: break
        for j in d.get("content", []):
            l = j.get("location") or {}
            out.append(dict(ext=j["id"], title=j["name"], url=f"https://jobs.smartrecruiters.com/{tok}/{j['id']}", loc=", ".join(filter(None, [l.get("city"), l.get("country")])), posted=j.get("releasedDate")))
        off += 100
        if off >= d.get("totalFound", 0): break
    return out, s

def workable(tok):
    s, d = get_json(f"https://apply.workable.com/api/v3/accounts/{tok}/jobs", data=b'{"query":"","location":[],"department":[],"worktype":[],"remote":[]}')
    if s != 200 or not d: return [], s
    return [dict(ext=j["shortcode"], title=j["title"], url=f"https://apply.workable.com/{tok}/j/{j['shortcode']}/", loc=", ".join(filter(None, [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("country")])), posted=j.get("published")) for j in d.get("results", [])], s

def recruitee(tok):
    s, d = get_json(f"https://{tok}.recruitee.com/api/offers/")
    if s != 200 or not d: return [], s
    return [dict(ext=str(j["id"]), title=j["title"], url=j["careers_url"], loc=j.get("location") or "", posted=j.get("published_at")) for j in d.get("offers", [])], s

def workday(url):
    m = re.search(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", url)
    if not m: return [], 0
    t, wd, site = m.groups()
    out, off, s, total = [], 0, 200, None
    while off < 400:   # first 400 postings; cap protects storage and latency
        s, d = get_json(f"https://{t}.{wd}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs", data=json.dumps({"appliedFacets": {}, "limit": 20, "offset": off, "searchText": ""}).encode())
        if s != 200 or not d or not d.get("jobPostings"): break
        out += [dict(ext=j.get("externalPath"), title=j.get("title"), url=f"https://{t}.{wd}.myworkdayjobs.com/{site}{j.get('externalPath','')}", loc=j.get("locationsText", ""), posted=None) for j in d["jobPostings"]]
        off += 20
        if total is None: total = d.get("total", 0)   # Workday only returns total on the first page
        if off >= total: break
    return out, s

def oracle(url):
    m = re.search(r"https?://([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/\w+/sites/(\w+)", url)
    if not m: return [], 0
    h, site = m.groups()
    s, d = get_json(f"https://{h}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},limit=50,offset=0")
    if s != 200 or not d or not d.get("items"): return [], s
    return [dict(ext=str(x["Id"]), title=x["Title"], url=f"https://{h}/hcmUI/CandidateExperience/en/sites/{site}/job/{x['Id']}", loc=x.get("PrimaryLocation", ""), posted=x.get("PostedDate")) for x in d["items"][0].get("requisitionList", [])], s

def zoho(host_url):
    m = re.search(r"https?://([\w-]+)\.zohorecruit\.(com|in|eu)", host_url)
    if not m: return [], 0
    base = f"https://{m.group(1)}.zohorecruit.{m.group(2)}"
    s, b, _, _ = get(base + "/jobs/Careers", limit=4_000_000)
    t = H.unescape(b.decode("utf8", "ignore")); i = t.find('"Posting_Title"')
    if s != 200 or i < 0: return [], s
    try: d, _ = json.JSONDecoder().raw_decode(t[t.rfind("[{", 0, i):])
    except Exception: return [], s
    return [dict(ext=x["id"], title=x["Posting_Title"], url=f"{base}/jobs/Careers/{x['id']}", loc=x.get("City", ""), posted=None) for x in d if x.get("Posting_Title")], s

def icims(host_url):
    m = re.search(r"https?://([\w-]+)\.icims\.com", host_url)
    if not m: return [], 0
    base = f"https://{m.group(1)}.icims.com"
    s, b, _, _ = get(base + "/jobs/search?ss=1&in_iframe=1", limit=1_500_000)
    if s != 200: return [], s
    from .extract import links
    out = {}
    for h, tx in links(b.decode("utf8", "ignore")):
        if h and re.search(r"/jobs/\d+/", h) and tx: out[urljoin(base, h.split("?")[0])] = re.sub(r"^.*?Title\s*", "", tx)[:100]
    return [dict(ext=u, title=t, url=u, loc="", posted=None) for u, t in out.items()], s

def fetch(ats, token_or_url):
    fn = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, smartrecruiters=smartrecruiters, workable=workable, recruitee=recruitee,
              workday=workday, oracle=oracle, zoho=zoho, icims=icims).get(ats)
    return fn(token_or_url) if fn else ([], -1)
