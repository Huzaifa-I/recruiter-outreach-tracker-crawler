import os
# All configuration comes from environment variables. Nothing is hard coded.
DATABASE_URL = os.environ.get("DATABASE_URL", "")          # required for worker (secret, set in CI secrets)
CONTACT = os.environ.get("CRAWLER_CONTACT", "")            # contact URL or email shown in User-Agent
THREADS = int(os.environ.get("CRAWLER_THREADS", "12"))
BATCH = int(os.environ.get("CRAWLER_BATCH", "300"))
PAGE_TIMEOUT = int(os.environ.get("CRAWLER_TIMEOUT", "8"))
MAX_JOBS_PER_COMPANY = int(os.environ.get("CRAWLER_MAX_JOBS", "200"))
PER_HOST_DELAY = float(os.environ.get("CRAWLER_HOST_DELAY", "1.0"))
USER_AGENT = f"JobCrawler/0.1 (+{CONTACT})" if CONTACT else "JobCrawler/0.1"
