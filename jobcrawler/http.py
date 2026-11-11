import json, time, threading, urllib.request, urllib.error, urllib.robotparser, ssl
from urllib.parse import urlparse
from . import config

_ctx = ssl.create_default_context()
_last = {}
_lock = threading.Lock()
_robots = {}

def _wait(host):
    with _lock:
        t = _last.get(host, 0.0); now = time.time()
        gap = config.PER_HOST_DELAY - (now - t)
        _last[host] = now + max(gap, 0)
    if gap > 0: time.sleep(gap)

def robots_ok(url):
    """Honour robots.txt for HTML pages. Public ATS JSON APIs are not checked."""
    p = urlparse(url); base = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            body = urllib.request.urlopen(urllib.request.Request(base + "/robots.txt", headers={"User-Agent": config.USER_AGENT}), timeout=8, context=_ctx).read(200000).decode("utf8", "ignore")
            rp.parse(body.splitlines())
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return rp.can_fetch(config.USER_AGENT, url)

def get(url, data=None, etag=None, limit=3_000_000, json_api=False, retries=3):
    """Return (status, body_bytes, etag, final_url). Backoff on 429/503."""
    host = urlparse(url).netloc
    h = {"User-Agent": config.USER_AGENT, "Accept": "application/json" if json_api else "text/html,*/*"}
    if etag: h["If-None-Match"] = etag
    if data is not None: h["Content-Type"] = "application/json"
    if not json_api and not robots_ok(url): return 999, b"", None, url
    for attempt in range(retries):
        _wait(host)
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=h, data=data), timeout=config.PAGE_TIMEOUT, context=_ctx)
            return r.status, r.read(limit), r.headers.get("ETag"), r.geturl()
        except urllib.error.HTTPError as e:
            if e.code == 304: return 304, b"", etag, url
            if e.code in (429, 503):
                time.sleep(min(2 ** attempt * 2, 30)); continue
            return e.code, b"", None, url
        except Exception:
            time.sleep(1 + attempt)
    return 0, b"", None, url

def get_json(url, data=None):
    s, b, e, _ = get(url, data=data, json_api=True)
    try: return s, json.loads(b)
    except Exception: return s, None
