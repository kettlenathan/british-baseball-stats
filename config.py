"""Central configuration for the British Baseball Stats Explorer."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_CACHE_DIR = DATA_DIR / "raw_cache"
DB_PATH = DATA_DIR / "stats.db"
DB_URL = f"sqlite:///{DB_PATH}"

BASE_URL = "https://stats.britishbaseball.org.uk"

# This repo, used both by the Feedback page (app/pages/10_Feedback.py) to
# file GitHub issues and by db/storage.py to publish/fetch data/stats.db as
# a release asset. Feedback auth is a GITHUB_TOKEN secret (Community Cloud
# dashboard or local .streamlit/secrets.toml); publishing auth is a
# GITHUB_TOKEN *environment* variable (CLI context, not a Streamlit one) —
# never committed either way.
GITHUB_REPO = "kettlenathan/british-baseball-stats"


def is_deployed() -> bool:
    """True only when the Community Cloud dashboard's Secrets box sets
    IS_DEPLOYED = true. Never set locally, so local runs always see False.
    Lives here (rather than only in app/env.py) so db/storage.py can also
    use it without app/ code depending on db/ — see that module's docstring
    for why that distinction matters for safe local-dev behavior."""
    try:
        import streamlit as st

        return bool(st.secrets.get("IS_DEPLOYED", False))
    except Exception:
        return False

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
