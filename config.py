"""Configuration for the ZLB scraper."""

BASE_URL = "https://agent.zlb.ink"
DATA_DIR = "data"

DOMAINS = [
    {"id": "gi", "name": "原神"},
    {"id": "hsr", "name": "崩坏星穹铁道"},
]

# Concurrent downloads per domain
MAX_CONCURRENT = 8

# Delay between requests (seconds) per domain to avoid rate limiting
REQUEST_DELAY = 0.1

# Max retries per failed download
MAX_RETRIES = 3

# Retry backoff base (seconds)
RETRY_BACKOFF = 1.0

# Progress file
PROGRESS_FILE = "progress.json"

# Request timeout (seconds)
REQUEST_TIMEOUT = 30
