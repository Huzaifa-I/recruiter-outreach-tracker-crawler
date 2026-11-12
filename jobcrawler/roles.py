"""Role search expansion. A user searching "data analyst" also wants "BI analyst" and "business intelligence analyst".
expand(term) -> (role_keys, regex_patterns). Patterns are Postgres case-insensitive regexes (~*) with word boundaries,
so "ba" never matches inside "Bakery" and "sde" only matches the word SDE. Any term not in the lists matches literally (plus an optional plural s)."""
import re

SYNONYMS = {
    "product manager": ["product manager", "product management", "product owner", "apm", "associate product manager", "technical product manager", "group product manager", "product lead", "product head"],
    "data analyst": ["data analyst", "business intelligence analyst", "bi analyst", "bi developer", "business intelligence", "analytics analyst", "reporting analyst", "insights analyst", "data analytics", "mis analyst", "mis executive"],
    "business analyst": ["business analyst", "systems analyst", "functional analyst", "process analyst", "business systems analyst"],
    "data scientist": ["data scientist", "applied scientist", "machine learning engineer", "ml engineer", "research scientist", "ai engineer", "nlp engineer", "ai scientist"],
    "data engineer": ["data engineer", "etl developer", "big data engineer", "analytics engineer", "data platform engineer"],
    "software engineer": ["software engineer", "software developer", "sde", "software development engineer", "backend developer", "backend engineer", "back end developer", "frontend developer", "frontend engineer", "front end developer", "full stack", "fullstack", "web developer", "application developer", "programmer", "java developer", "python developer", "node developer", ".net developer", "react developer"],
    "devops engineer": ["devops", "site reliability engineer", "sre", "platform engineer", "cloud engineer", "infrastructure engineer"],
    "qa engineer": ["qa engineer", "quality assurance", "test engineer", "sdet", "software tester", "automation tester", "qa analyst", "qa lead"],
    "designer": ["designer", "ux designer", "ui designer", "product designer", "ui/ux", "ux researcher", "visual designer", "graphic designer"],
    "marketing manager": ["marketing manager", "digital marketing", "growth marketing", "performance marketing", "content marketing", "seo", "marketing executive", "brand manager", "growth manager"],
    "sales": ["sales executive", "sales manager", "business development", "account executive", "sales representative", "bdr", "sdr", "inside sales", "account manager"],
    "customer success": ["customer success", "customer support", "customer service", "support engineer", "client success", "customer experience"],
    "hr": ["hr executive", "hr manager", "human resources", "talent acquisition", "recruiter", "people operations", "hr business partner", "hrbp"],
    "finance": ["accountant", "finance manager", "financial analyst", "accounts executive", "chartered accountant", "fp&a", "finance executive", "auditor"],
    "project manager": ["project manager", "program manager", "delivery manager", "scrum master", "project coordinator", "project lead"],
    "operations": ["operations manager", "operations executive", "operations analyst", "supply chain", "logistics", "operations lead"],
    "content writer": ["content writer", "copywriter", "technical writer", "content creator", "editor", "content strategist"],
}
PM_CANON = {"product manager"}
_ALIAS = {}
for canon, al in SYNONYMS.items():
    for a in [canon] + al: _ALIAS.setdefault(a, canon)
# short forms people actually type
for a, c in {"pm": "product manager", "product": "product manager", "bi": "data analyst", "analyst": "data analyst", "developer": "software engineer", "engineer": "software engineer",
             "swe": "software engineer", "ux": "designer", "ui": "designer", "ml": "data scientist", "marketing": "marketing manager", "recruiting": "hr", "accounting": "finance"}.items():
    _ALIAS[a] = c

def norm(term):
    return " ".join(re.sub(r"[-_/]+", " ", (term or "").lower()).split())

_SPECIAL = set(".^$*+?()[]{}|\\")
def _esc(word): return "".join("\\" + ch if ch in _SPECIAL else ch for ch in word)

def pattern(term):
    """Postgres ARE: word boundaries, optional plural s, spaces, hyphens, underscores or nothing between words ("full stack", "full-stack", "fullstack")."""
    words = [w for w in re.split(r"[\s\-]+", term.strip().lower()) if w]
    body = "[^[:alnum:]]?".join(_esc(w) for w in words)
    return rf"(^|[^[:alnum:]]){body}s?([^[:alnum:]]|$)"

def expand(term):
    """Returns (role_keys, patterns) for one search term."""
    t = norm(term)
    if not t: return [], []
    canon = _ALIAS.get(t)
    terms = list(dict.fromkeys([t] + (SYNONYMS[canon] if canon else [])))
    keys = ["pm"] if canon in PM_CANON else []
    return keys, [pattern(x) for x in terms]

def expand_many(terms):
    keys, pats = [], []
    for t in terms:
        k, p = expand(t); keys += k; pats += p
    return list(dict.fromkeys(keys)), list(dict.fromkeys(pats))
