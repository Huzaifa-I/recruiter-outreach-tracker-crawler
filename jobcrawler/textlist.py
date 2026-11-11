"""Low confidence fallback: job-title-like items in a plain careers page. No per-job URLs (jobs link to the careers page)."""
import re,html as H
from .tiers import TITLE
BLOCK=re.compile(r'<(h[2-5]|li|strong|td|p)[^>]*>(.*?)</\1>',re.S|re.I)
NAV=re.compile(r'(privacy|cookie|terms|login|sign in|contact|about|home|menu|subscribe|copyright|follow us|read more|learn more|apply now|why join|our (culture|values|team)|benefits|how to apply|equal opportunity|we are|we offer|you will|you are|experience|responsib|qualif|skills|salary)',re.I)
def parse_text_list(b):
    b=re.sub(r'<(script|style|nav|header|footer|noscript)[^>]*>.*?</\1>','',b,flags=re.S|re.I)
    out=[]
    for m in BLOCK.finditer(b):
        t=' '.join(H.unescape(re.sub(r'<[^>]+>',' ',m.group(2))).split())
        if 8<=len(t)<=70 and TITLE.search(t) and not NAV.search(t) and len(t.split())<=9 and t[0].isupper() and not t.endswith(('.',':','?')):out.append(t)
    out=list(dict.fromkeys(out))
    return out if len(out)>=3 else []   # need a list, not a lone phrase
