import re, datetime as dt
def parse_posted(v, now=None):
    """Best-effort posted date -> aware datetime, or None. Future dates and junk return None."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if v in (None, "", 0): return None
    d = None
    try:
        if isinstance(v, (int, float)):
            d = dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, dt.timezone.utc)
        else:
            s = str(v).strip(); low = s.lower()
            if "today" in low: d = now
            elif "yesterday" in low: d = now - dt.timedelta(days=1)
            else:
                m = re.search(r"(\d+)\+?\s*days?\s*ago", low)
                if m: d = now - dt.timedelta(days=int(m.group(1)))
                else:
                    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00")[:32])
                    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    except Exception: return None
    if d is None or d > now + dt.timedelta(hours=1) or d.year < 2000: return None
    return d
