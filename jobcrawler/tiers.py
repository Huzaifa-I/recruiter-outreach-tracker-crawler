"""Tier 1: inline JSON state. Tier 2: hidden API discovery from page scripts. Tier 3: headless render (optional, Playwright)."""
import re, json, html as H
from urllib.parse import urljoin, urlparse
from .http import get
from .extract import TITLE, links

TKEYS = ("title", "jobTitle", "job_title", "postingTitle", "Posting_Title", "position", "designation", "name", "text")
JOBKEYS = ("location", "locations", "department", "jobId", "job_id", "requisitionId", "applyUrl", "apply_url", "slug", "employmentType", "city", "id")

PKEYS = ("datePosted", "date_posted", "postedDate", "posted_at", "postedOn", "publishedAt", "published_at", "createdAt")
DKEYS = ("description", "jobDescription", "job_description", "descriptionHtml", "descriptionPlain", "summary")
CKEYS = ("company", "companyName", "company_name", "hiringOrganization", "organization")
def _first(x, keys):
    for k in keys:
        v = x.get(k)
        if isinstance(v, (str, int, float, dict)) and v != "": return v

def job_lists(obj, depth=0):
    """Yield lists of dicts that look like job postings: >=2 items, >=60% have a title that contains a job word."""
    if depth > 8: return
    if isinstance(obj, list):
        ds = [x for x in obj if isinstance(x, dict)]
        if len(ds) >= 2 and len(ds) >= 0.8 * len(obj):
            def tt(x):
                for k in TKEYS:
                    v = x.get(k)
                    if isinstance(v, str) and 3 < len(v) < 160: return v
            ts = [tt(x) for x in ds]
            ok = [t for t, x in zip(ts, ds) if t and TITLE.search(t) and any(k in x for k in JOBKEYS)]
            if len(ok) >= 2 and len(ok) >= 0.6 * len(ds):
                yield [dict(ext=str(x.get("id") or x.get("slug") or t), title=t, url=str(x.get("url") or x.get("applyUrl") or x.get("apply_url") or x.get("absolute_url") or ""), loc=str(x.get("location") or x.get("city") or ""), posted=_first(x, PKEYS), description=_first(x, DKEYS), company=_first(x, CKEYS)) for t, x in zip(ts, ds) if t and TITLE.search(t)]
        for x in obj[:200]: yield from job_lists(x, depth + 1)
    elif isinstance(obj, dict):
        for v in list(obj.values())[:200]: yield from job_lists(v, depth + 1)

def tier1_inline(html):
    """__NEXT_DATA__, window.__STATE__ style blobs, <script type=application/json>."""
    blobs = []
    for m in re.finditer(r"<script[^>]*(?:id=\"__NEXT_DATA__\"|type=\"application/json\")[^>]*>(.{0,2000000}?)</script>", html, re.S | re.I): blobs.append(m.group(1))  # bounded: unbounded lazy dot is quadratic on unclosed tags (f0ccb22 class)
    for m in re.finditer(r"window\.(?:__[A-Z_]+__|\w*[Ss]tate\w*|\w*[Dd]ata\w*)\s*=\s*(\{.{0,200000}?\});?\s*</script>", html, re.S): blobs.append(m.group(1))  # bounded, same reason
    for m in re.finditer(r"value=\"(\[\{&quot;.{0,200000}?\}\])\"", html, re.S): blobs.append(H.unescape(m.group(1)))  # bounded, same reason
    for b in blobs:
        try: d = json.loads(b)
        except Exception: continue
        for jl in job_lists(d):
            if jl: return jl
    return []

API_HINT = re.compile(r"""["'`]((?:https?:)?//[\w.-]+/[^"'`\s<>]*?(?:job|career|opening|vacanc|position|requisition|posting)[^"'`\s<>]*?|/[\w./-]*?(?:api|wp-json|graphql|ajax)[^"'`\s<>]*?(?:job|career|opening|vacanc|position|posting)[^"'`\s<>]*?|/(?:api|_next/data|wp-json)/[^"'`\s<>]+)["'`]""", re.I)
SKIP = re.compile(r"\.(js|css|png|jpe?g|svg|gif|woff2?|ico|pdf|webp)(\?|$)|google|facebook|linkedin|twitter|gtag|analytics|cdn|schema\.org|w3\.org", re.I)

def tier2_api(base, html, max_scripts=6, max_cands=8):
    cands, srcs = [], []
    for m in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, re.I):
        u = urljoin(base, m.group(1))
        if urlparse(u).netloc == urlparse(base).netloc and not re.search(r"jquery|bootstrap|gtag|analytics|polyfill|vendor|runtime", u, re.I): srcs.append(u)
    texts = [html]
    for u in srcs[:max_scripts]:
        s, b, _, _ = get(u, limit=800_000, json_api=True)
        if s == 200: texts.append(b.decode("utf8", "ignore"))
    for t in texts:
        for m in API_HINT.finditer(t):
            u = m.group(1)
            if SKIP.search(u) or len(u) > 200: continue
            u = urljoin(base, u if not u.startswith("//") else "https:" + u)
            if u not in cands: cands.append(u)
    for u in cands[:max_cands]:
        s, b, _, _ = get(u, limit=2_000_000, json_api=True)
        if s != 200: continue
        try: d = json.loads(b)
        except Exception: continue
        for jl in job_lists(d):
            if jl: return jl, u
    return [], None

def keka(html):
    m = re.search(r"https?://([\w-]+)\.keka\.com/careers/api/embedjobs/js/([0-9a-f-]{36})", html)
    if not m: return []
    s, b, _, _ = get(f"https://{m.group(1)}.keka.com/careers/api/embedjobs/default/active/{m.group(2)}", json_api=True)
    try: d = json.loads(b)
    except Exception: return []
    return [dict(ext=str(x["id"]), title=x["title"], url=f"https://{m.group(1)}.keka.com/careers/jobdetails/{x['id']}", loc="", posted=None) for x in d if isinstance(x, dict) and x.get("title")]

def icims_host(html, base):
    if "icims.com" not in html and "icims" not in html.lower(): return []
    host = urlparse(base).netloc
    m = re.search(r"https?://([\w.-]*icims\.com)", html)
    for h in ([m.group(1)] if m else []) + [host]:
        s, b, _, _ = get(f"https://{h}/jobs/search?ss=1&in_iframe=1", limit=1_500_000)
        if s != 200: continue
        out = {}
        for hr, tx in links(b.decode("utf8", "ignore")):
            if hr and re.search(r"/jobs/\d+/", hr) and tx: out[urljoin(f"https://{h}", hr.split("?")[0])] = re.sub(r"^.*?Title\s*", "", tx)[:100]
        if out: return [dict(ext=u, title=t, url=u, loc="", posted=None) for u, t in out.items()]
    return []

def sitemap_id_jobs(urls):
    """Accept ID-style job URLs (/job/<slug>/<id>/ or /jobs/<id>) when >=10 share one pattern."""
    pat = re.compile(r"/(job|jobs|requisition|jobdetails?)/(?:[^/?#]+/)?\d{4,}", re.I)
    c = [u for u in urls if pat.search(u)]
    if len(c) >= 10: return {u: re.sub(r"[-_]+", " ", (re.search(r"/job/([^/]+)/\d", u) or re.search(r"/([^/]+)/?$", u)).group(1)) for u in c}
    return {}

def tier3_render(url, timeout=30000):
    """Headless render. Optional: needs `pip install playwright` and a Chromium/Chrome binary. Captures JSON XHR responses + rendered DOM."""
    from playwright.sync_api import sync_playwright
    found = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])  # in CI: `playwright install --with-deps chromium`
        pg = b.new_page()
        def on_resp(r):
            try:
                if "json" in (r.headers.get("content-type") or "") and r.status == 200:
                    d = r.json()
                    for jl in job_lists(d):
                        if jl: found.append(jl)
            except Exception: pass
        pg.on("response", on_resp)
        try: pg.goto(url, timeout=timeout, wait_until="networkidle")
        except Exception: pass
        html = pg.content(); b.close()
    if found: return max(found, key=len), html, "xhr"
    return [], html, "dom"
