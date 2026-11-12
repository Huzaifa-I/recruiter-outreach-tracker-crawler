import re, html as H
MAX_DESC = 8000
_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr|section)\b[^>]*>", re.I)

def clean_description(v, limit=MAX_DESC):
    """HTML (possibly entity-escaped twice, as Greenhouse does) -> plain text, bullets kept as lines, truncated on a word boundary."""
    if not v or not isinstance(v, str): return None
    s = H.unescape(H.unescape(v)) if "&lt;" in v else v
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<li[^>]*>", "\n- ", s, flags=re.I)
    s = _BLOCK.sub("\n", s); s = re.sub(r"<[^>]+>", " ", s); s = H.unescape(s)
    s = re.sub(r"[ \t\r\f\v\u00a0]+", " ", s); s = re.sub(r" ?\n ?", "\n", s); s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if len(s) < 40: return None          # too short to be a job description (nav fragment, "Apply now")
    if len(s) > limit:
        cut = s[:limit]; sp = cut.rfind(" ")
        s = (cut[:sp] if sp > limit * 0.8 else cut).rstrip() + "..."
    return s.replace("\x00", "")

def clean_company(v):
    if isinstance(v, dict): v = v.get("name")
    if not isinstance(v, str): return None
    v = " ".join(H.unescape(v).split())
    return v[:200] if 1 < len(v) else None
