"""Discover which years a competition has data for.

Per findings.md, a competition's /editions page links to itself in every
other year it ran (confirmed: 2026-nbl's editions page lists 2021-2026).
"""

import re

from sqlalchemy.orm import Session

from config import BASE_URL
from scraper.http_client import fetch_html

# Senior competition codes confirmed present in the 2026 calendar — see
# scraper/recon/findings.md. `dev` and `friendlies` are structurally
# identical but arguably not "senior league" in spirit; kept here but
# callers may want to exclude them (see build-order Milestone 7 note).
SENIOR_LEAGUE_CODES = ["nbl", "d2", "d3", "d4", "d5", "dev", "friendlies"]


def discover_years(league_code: str, known_year: int, session: Session | None = None, force_refresh: bool = False) -> list[int]:
    slug = f"{known_year}-{league_code}"
    url = f"{BASE_URL}/en/events/{slug}/editions"
    html = fetch_html(url, "editions", session=session, source_id=slug, force_refresh=force_refresh)
    pattern = re.compile(rf"events/(\d{{4}})-{re.escape(league_code)}/")
    years = sorted({int(y) for y in pattern.findall(html)})
    return years
