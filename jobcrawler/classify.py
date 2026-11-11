import re
PM = re.compile(r"\b(product (manager|owner|lead)|apm|associate product|technical product|group product|product management)\b", re.I)
SEN = re.compile(r"\b(senior|sr\.?|staff|principal|head|director|vp|lead|group|chief)\b", re.I)
JR = re.compile(r"\b(associate|junior|apm|intern|graduate|trainee)\b", re.I)
IN = re.compile(r"\b(india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|noida|hyderabad|pune|chennai|kolkata)\b", re.I)
REM = re.compile(r"\bremote\b", re.I)
EXP = re.compile(r"(\d{1,2})\s*(?:\+|-|to)?\s*(\d{1,2})?\s*(?:years|yrs)", re.I)

def classify(title, loc):
    """Rule based. exp_min is None when unknown (shown, never hidden)."""
    role = "pm" if PM.search(title or "") else "other"
    level = "senior" if SEN.search(title or "") else ("junior" if JR.search(title or "") else "unspecified")
    return dict(role=role, level=level, india=int(bool(IN.search(loc or ""))), remote=int(bool(REM.search(loc or "") or REM.search(title or ""))))
