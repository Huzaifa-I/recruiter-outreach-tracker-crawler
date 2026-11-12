"""Low confidence fallback: job-title-like items in a plain careers page. No per-job URLs (jobs link to the careers page)."""
import re,html as H
from .tiers import TITLE
BLOCK=re.compile(r'<(h[2-5]|li|strong|td|p)[^>]*>(.{0,3000}?)</\1>',re.S|re.I)  # bounded: unbounded lazy dot is quadratic on unclosed tags and froze a worker (GIL held in re)
NAV=re.compile(r'(privacy|cookie|terms|login|sign in|contact|about|home|menu|subscribe|copyright|follow us|read more|learn more|apply now|why join|our (culture|values|team)|benefits|how to apply|equal opportunity|we are|we offer|you will|you are|experience|responsib|qualif|skills|salary)',re.I)
JUNK=re.compile(r'\b(complaint|disclosure|newsletter|press release|annual report|circular|notice|agm|webinar|blog)\b',re.I)
MAX_ITEMS=60
def parse_text_list(b):
    b=re.sub(r'<(script|style|nav|header|footer|noscript)[^>]*>.{0,60000}?</\1>','',b,flags=re.S|re.I)  # bounded, same reason
    out=[]
    for m in BLOCK.finditer(b):
        t=' '.join(H.unescape(re.sub(r'<[^>]+>',' ',m.group(2))).split())
        if 8<=len(t)<=70 and TITLE.search(t) and not NAV.search(t) and len(t.split())<=9 and t[0].isupper() and not t.endswith(('.',':','?')):out.append(t)
    out=list(dict.fromkeys(out))
    # repeated-pattern filter: "Complaint March 2024", "Complaint April 2024"... are archives, not openings
    def norm(t): return re.sub(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\d+','#',t.lower()).strip()
    from collections import Counter
    c=Counter(norm(t) for t in out)
    out=[t for t in out if c[norm(t)]<=3 and not JUNK.search(t)]
    if len(out)>MAX_ITEMS: return []   # a real openings list on a plain page is short; a long one is an archive or menu
    return out if len(out)>=3 else []   # need a list, not a lone phrase
