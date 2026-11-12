import re
PM = re.compile(r"\b(product (manager|owner|lead)|apm|associate product|technical product|group product|product management)\b", re.I)
SEN = re.compile(r"\b(senior|sr\.?|staff|principal|head|director|vp|lead|group|chief)\b", re.I)
JR = re.compile(r"\b(associate|junior|apm|intern|graduate|trainee)\b", re.I)
IN = re.compile(r"\b(india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai|kolkata)\b", re.I)
REM = re.compile(r"\bremote\b", re.I)
EXP = re.compile(r"(\d{1,2})\s*(?:\+|-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b", re.I)
FRESH = re.compile(r"\b(fresher|freshers|intern|internship|trainee|graduate trainee|entry[- ]level|campus)\b", re.I)

_CTX = re.compile(r"experience|\bexp\b|\bexp\.", re.I)
_RANGE = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
_PLUS = re.compile(r"(?<![\d.])(\d{1,2})\s*\+\s*(?:years?|yrs?)\b", re.I)
_MIN = re.compile(r"\b(?:minimum|min\.?|at least|atleast|over|more than)\s*(?:of\s*)?(\d{1,2})\s*(?:years?|yrs?)\b", re.I)
_YRS = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:years?|yrs?)\b", re.I)

def exp_from_text(text, window=70):
    """(min, max) years from job description text, (None, None) if not stated. Only mentions with 'experience' nearby count, so
    '10 years of company history' is ignored. The earliest qualifying mention wins (the headline requirement comes first in most JDs).
    max is set only for explicit ranges ('3-5 years'). Approximate by nature."""
    t = text or ""
    best = None
    for rx, kind in ((_RANGE, "r"), (_PLUS, "p"), (_MIN, "m"), (_YRS, "y")):
        for m in rx.finditer(t):
            if not _CTX.search(t[max(0, m.start() - window): m.end() + window]): continue
            lo = int(m.group(1)); hi = int(m.group(2)) if kind == "r" else None
            if lo > 30 or (hi is not None and (hi < lo or hi - lo > 10)): continue
            if best is None or m.start() < best[0]: best = (m.start(), float(lo), float(hi) if hi is not None else None)
    return (best[1], best[2]) if best else (None, None)

def exp_range(title, desc=None):
    """Title hint first, then the description. (None, None) = unknown, shown never hidden."""
    t = title or ""
    m = _RANGE.search(t)
    if m and int(m.group(1)) <= 30 and int(m.group(2)) >= int(m.group(1)): return float(m.group(1)), float(m.group(2))
    lo = exp_min(t)
    if lo is not None: return lo, None
    return exp_from_text(desc)

def exp_min(title):
    """Title-only hint, years of experience required at minimum. None = unknown (never hidden). Only explicit statements count:
    '2-4 years', '3+ yrs', 'Fresher', 'Intern'. Seniority words alone do not guess a number."""
    t = title or ""
    m = EXP.search(t)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 30: return float(v)
    if FRESH.search(t): return 0.0
    return None

def classify(title, loc):
    """Rule based. exp_min is None when unknown (shown, never hidden)."""
    role = "pm" if PM.search(title or "") else "other"
    level = "senior" if SEN.search(title or "") else ("junior" if JR.search(title or "") else "unspecified")
    return dict(exp_min=exp_min(title), role=role, level=level, india=int(bool(IN.search(loc or ""))), remote=int(bool(REM.search(loc or "") or REM.search(title or ""))))
