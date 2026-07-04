"""Raw-response disk cache so re-running the scraper doesn't re-hit the
network unless force_refresh is passed — see scraper/recon/findings.md and
the plan's resumability requirement."""

import hashlib
import json
import time
from pathlib import Path

from config import (
    CACHE_TTL_CURRENT_SEASON_HOURS,
    CACHE_TTL_HISTORICAL_SEASON_HOURS,
    RAW_CACHE_DIR,
)


def _cache_path(entity_type: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    directory = RAW_CACHE_DIR / entity_type
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.json"


def get(entity_type: str, key: str, ttl_hours: float = CACHE_TTL_HISTORICAL_SEASON_HOURS) -> str | None:
    path = _cache_path(entity_type, key)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    age_hours = (time.time() - payload["fetched_at"]) / 3600
    if age_hours > ttl_hours:
        return None
    return payload["body"]


def put(entity_type: str, key: str, body: str) -> Path:
    path = _cache_path(entity_type, key)
    payload = {"url": key, "fetched_at": time.time(), "body": body}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def ttl_for(is_current_season: bool) -> float:
    return CACHE_TTL_CURRENT_SEASON_HOURS if is_current_season else CACHE_TTL_HISTORICAL_SEASON_HOURS
