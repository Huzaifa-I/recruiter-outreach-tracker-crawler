import re
PM = re.compile(r"\b(product (manager|owner|lead)|apm|associate product|technical product|group product|product management)\b", re.I)
SEN = re.compile(r"\b(senior|sr\.?|staff|principal|head|director|vp|lead|group|chief)\b", re.I)
JR = re.compile(r"\b(associate|junior|apm|intern|graduate|trainee)\b", re.I)
IN = re.compile(r"\b(india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai|kolkata)\b", re.I)
REM = re.compile(r"\bremote\b", re.I)
EXP = re.compile(r"(\d{1,2})\s*(?:\+|-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b", re.I)
FRESH = re.compile(r"\b(fresher|freshers|intern|internship|trainee|graduate trainee|entry[- ]level|campus)\b", re.I)

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
