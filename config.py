"""Central configuration for the British Baseball Stats Explorer."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_CACHE_DIR = DATA_DIR / "raw_cache"
DB_PATH = DATA_DIR / "stats.db"
DB_URL = f"sqlite:///{DB_PATH}"

BASE_URL = "https://stats.britishbaseball.org.uk"

# Repo that the Feedback page (app/pages/10_Feedback.py) files GitHub issues
# against. Auth is a GITHUB_TOKEN secret (Community Cloud dashboard or local
# .streamlit/secrets.toml), never committed.
GITHUB_FEEDBACK_REPO = "kettlenathan/british-baseball-stats"

# Politeness settings for the scraper — this is a small federation's server,
# not a CDN-backed site built to withstand scraping load. Bumped up from an
# initial 1.5s after a sustained multi-hundred-request session (several full
# pipeline runs back to back) triggered ~100+ consecutive 403s — see
# scraper/recon/findings.md. Looks like a cumulative rate/request-count
# threshold rather than a per-request one, hence also the circuit breaker in
# scraper/pipeline.py.
REQUEST_DELAY_SECONDS = 3.0
REQUEST_DELAY_JITTER_SECONDS = 1.0

# How long cached responses are considered fresh before a plain (non-forced)
# scrape run will re-fetch them. Completed historical seasons don't change,
# so they're cached indefinitely by giving them a very long TTL.
CACHE_TTL_CURRENT_SEASON_HOURS = 24
CACHE_TTL_HISTORICAL_SEASON_HOURS = 24 * 365 * 10

DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
