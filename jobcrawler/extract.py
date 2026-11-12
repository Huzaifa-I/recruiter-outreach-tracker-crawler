"""Find jobs on a company career page. Order = confidence: ATS API > JSON-LD > WordPress/RSS > sitemap > page links."""
import re, json, html as H
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from .http import get, get_json
from .connectors import detect, fetch

TITLE = re.compile(r"\b(engineer|developer|manager|analyst|executive|officer|designer|specialist|associate|intern|lead|head of|director|consultant|architect|scientist|technician|coordinator|representative|supervisor|administrator|accountant|nurse|physician|operator|assistant|writer|recruiter|programmer|trainee|sales|marketing|product|qa|sde|devops)\b", re.I)
JOBPATH = re.compile(r"/(jobs?|openings?|positions?|vacanc(y|ies)|requisition|careers?/[\w%.-]{6,}|job-details|jobdetail|apply)/[\w%.-]{3,}", re.I)
BADTEXT = re.compile(r"^(careers?|jobs?|view (all )?(jobs|openings|positions)|apply( now)?|join us|open positions|see all|learn more|read more|all jobs|search jobs|current openings|sign in|login|home)$", re.I)
BADURL = re.compile(r"blog|news|press|about|privacy|terms|cookie|contact|insight|salary|/go/", re.I)
BADTXT2 = re.compile(r"salary|outlook|insight|careers?$|click to visit|job-areas|trainee-programmes", re.I)

class _A(HTMLParser):
    def __init__(s): super().__init__(); s.l = []; s.cur = None; s.t = ""
    def handle_starttag(s, t, a):
        if t == "a": s.cur = dict(a).get("href"); s.t = ""
    def handle_data(s, d):
        if s.cur is not None: s.t += d
    def handle_endtag(s, t):
        if t == "a" and s.cur is not None: s.l.append((s.cur, " ".join(s.t.split()))); s.cur = None

def links(html):
    p = _A()
    try: p.feed(html)
    except Exception: pass
    return p.l

def _same_org(base, u):
    a = re.sub(r"[^a-z0-9]", "", (urlparse(base).netloc.split(".")[-2:-1] or [""])[0].lower())
    h = urlparse(u).netloc.lower()
    return bool(a) and a in re.sub(r"[^a-z0-9]", "", h)

def page_links(base, html):
    out = {}
    for h, t in links(html):
        if not h or h.startswith(("#", "mailto:", "javascript:", "tel:")): continue
        u = urljoin(base, h)
        if not t or len(t) < 6 or len(t) > 120 or BADTEXT.match(t): continue
        if TITLE.search(t) and JOBPATH.search(u) and not BADURL.search(u) and not BADTXT2.search(t + " " + u) and _same_org(base, u):
            out[u] = t
    return out

def jsonld_jobs(html):
    """JobPosting objects with the fields already in the page: description, datePosted, hiringOrganization, jobLocation. Zero extra fetches."""
    out = {}
    for m in re.finditer(r"<script[^>]*ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try: d = json.loads(m.group(1))
        except Exception: continue
        st = [d]
        while st:
            x = st.pop()
            if isinstance(x, list): st += x
            elif isinstance(x, dict):
                if x.get("@type") == "JobPosting" and x.get("title") and "career" not in str(x["title"]).lower():
                    loc = x.get("jobLocation"); loc = loc[0] if isinstance(loc, list) and loc else loc
                    a = (loc or {}).get("address") if isinstance(loc, dict) else None
                    a = a if isinstance(a, dict) else {}
                    place = ", ".join(str(a[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if isinstance(a.get(k), str) and a.get(k))
                    key = str(x.get("url") or x["title"])
                    out[key] = dict(ext=key, title=x["title"], url=key, loc=place, posted=x.get("datePosted"), description=x.get("description"), company=x.get("hiringOrganization"))
                st += list(x.get("@graph", []))
    return out

def jsonld(html):
    return {k: v["title"] for k, v in jsonld_jobs(html).items()}

def sitemap_jobs(base_url):
    """Filtered: >=3 job-path URLs with title words in the slug, >=40% of candidates."""
    p = urlparse(base_url); base = f"{p.scheme}://{p.netloc}"
    s, b, _, _ = get(base + "/robots.txt", limit=100000)
    sm = re.findall(r"(?im)^sitemap:\s*(\S+)", b.decode("utf8", "ignore")) or [base + "/sitemap.xml", base + "/wp-sitemap.xml"]
    urls, seen, q, n = [], set(), sm[:4], 0
    while q and n < 5:
        x = q.pop(0); n += 1
        if x in seen: continue
        seen.add(x); s, b, _, _ = get(x, limit=2_000_000)
        if s != 200: continue
        for l in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", b.decode("utf8", "ignore")):
            if l.endswith(".xml") or "sitemap" in l.lower():
                if re.search(r"job|career|vacan|opening|position|post|page", l, re.I): q.append(l)
            else: urls.append(l)
    strict = re.compile(r"/(jobs?|job-listing|openings?|vacanc(y|ies)|positions?|careers?)/", re.I)
    cand = [u for u in urls if strict.search(u) and not re.search(r"blog|news|category|tag", u, re.I)]
    good = [u for u in cand if TITLE.search(re.sub(r"[-_/]", " ", u.split("?")[0]))]
    if len(good) >= 3 and len(good) / max(1, len(cand)) >= 0.4:
        return {u: u.rstrip("/").split("/")[-1].split("?")[0].replace("-", " ") for u in good}
    from .tiers import sitemap_id_jobs
    return sitemap_id_jobs(urls)

def wp_jobs(base_url, html):
    if "wp-content" not in html: return {}
    p = urlparse(base_url); base = f"{p.scheme}://{p.netloc}"
    s, d = get_json(base + "/wp-json/wp/v2/types")
    if s != 200 or not isinstance(d, dict): return {}
    for k, v in d.items():
        if re.search(r"job|career|vacanc|opening|position", k, re.I) and v.get("rest_base"):
            s, d2 = get_json(base + f"/wp-json/wp/v2/{v['rest_base']}?per_page=50&_fields=link,title")
            if s == 200 and isinstance(d2, list) and d2: return {x["link"]: H.unescape(x["title"]["rendered"]) for x in d2}
    return {}

def successfactors(base, html):
    host = urlparse(base).netloc; cats = []
    for h, t in links(html):
        u = urljoin(base, h or "")
        if urlparse(u).netloc == host and re.search(r"/go/[\w-]+/\d+", u) and u not in cats: cats.append(u)
    out = {}
    for c in cats[:6]:
        s, b, _, f = get(c)
        for h, t in links(b.decode("utf8", "ignore")):
            if h and re.search(r"/job/[^/]+/\d+", h) and t and len(t) > 4: out[urljoin(f, h)] = t
    return out

def rss_jobs(base, html):
    out = {}
    for m in re.finditer(r'<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*>', html, re.I):
        h = re.search(r'href="([^"]+)"', m.group(0))
        if not h or not re.search(r"job|career|vacan|opening|position", h.group(1), re.I): continue
        s, b, _, _ = get(urljoin(base, h.group(1)), limit=1_000_000)
        for it in re.findall(r"<item>.*?</item>", b.decode("utf8", "ignore"), re.S):
            tt = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S); ll = re.search(r"<link>(.*?)</link>", it)
            if tt and ll: out[ll.group(1).strip()] = H.unescape(tt.group(1))
    return out

def extract(career_url, budget=25.0):
    import time; t0 = time.time()
    """Return dict(method, ats, jobs=[{ext,title,url,loc,posted}], confidence, ats_token)."""
    s, b, _, final = get(career_url, limit=3_000_000)
    html = b.decode("utf8", "ignore")
    ats, tok = detect(final)
    if not ats: ats, tok = detect(html)
    if not ats:  # one hop to careers.* / jobs.* host
        dom = ".".join(urlparse(final).netloc.replace("www.", "").split(".")[-2:])
        for h, t in links(html):
            if not h: continue
            hu = urljoin(final, h); pu = urlparse(hu)
            if pu.netloc.endswith(dom) and re.match(r"(careers?|jobs?)\.", pu.netloc) and hu != final:
                s2, b2, _, f2 = get(hu); h2 = b2.decode("utf8", "ignore")
                ats, tok = detect(f2)
                if not ats: ats, tok = detect(h2)
                html += h2; final = f2
                break
    if ats:
        arg = tok if ats in ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee") else (final if ats != "zoho" else final + " " + html[:0])
        if ats in ("workday", "oracle", "zoho", "icims"):
            m = re.search(r"https?://[\w.-]*(?:myworkdayjobs|oraclecloud|zohorecruit|icims)[^\s\"'<>]+", html + " " + final)
            arg = m.group(0) if m else final
        jobs, st = fetch(ats, arg)
        if jobs: return dict(method="ats:" + ats, ats=ats, ats_token=arg, jobs=jobs, confidence=3)
    ld = jsonld_jobs(html)
    if ld: return dict(method="jsonld", ats=None, ats_token=None, jobs=list(ld.values()), confidence=2)
    from . import tiers
    s_ok = s
    for name, fn, conf in (("keka", lambda: {j["url"]: j["title"] for j in tiers.keka(html)}, 3), ("icims", lambda: {j["url"]: j["title"] for j in tiers.icims_host(html, final)}, 3), ("inline_json", lambda: {j["url"] or j["ext"]: j["title"] for j in tiers.tier1_inline(html)}, 2), ("wp_rest", lambda: wp_jobs(final, html), 2), ("rss", lambda: rss_jobs(final, html), 2), ("successfactors", lambda: successfactors(final, html) if re.search(r"/go/[\w-]+/\d+", html) else {}, 3), ("sitemap", lambda: sitemap_jobs(final), 1), ("page_links", lambda: page_links(final, html), 1), ("hidden_api", lambda: {j["url"] or j["ext"]: j["title"] for j in tiers.tier2_api(final, html)[0]}, 1)):
        if time.time() - t0 > budget: break      # latency budget per company
        g = fn()
        if g: return dict(method=name, ats=None, ats_token=None, jobs=[dict(ext=u, title=t, url=u, loc="", posted=None) for u, t in g.items()], confidence=conf)
    from .textlist import parse_text_list
    tl = parse_text_list(html)
    if tl: return dict(method="text_list", ats=None, ats_token=None, jobs=[dict(ext="t:" + t.lower(), title=t, url=final, loc="", posted=None) for t in tl], confidence=0)
    return dict(method="none", ats=ats, ats_token=None, jobs=[], confidence=0, diag=dict(status=s_ok, html_len=len(html), final=final, ats=ats))
