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

# d2/d3/d4 were renamed for 2026 — confirmed via cached /editions pages that
# 2021-2025 used "aaa"/"aa"/"a" for what are now "d2"/"d3"/"d4". Hitting
# "a"'s own /editions page (unlike d4's) cleanly lists exactly 2021-2025 with
# no unrelated competitions mixed in, and 2023's "a" standings share far more
# teams with 2026's d4 (7, e.g. Richmond Barons, Bristol Buccaneers) than
# with any other 2026 division or with the regional "eebl"/"nebl" comps that
# d4's own /editions page incorrectly mixes in — see the historical-backfill
# investigation. d5 has no pre-2026 history at all (its /editions page lists
# only 2026), so it's deliberately left unmapped.
HISTORICAL_CODE_OVERRIDES: dict[str, str] = {"d2": "aaa", "d3": "aa", "d4": "a"}
HISTORICAL_CODE_CUTOFF_YEAR = 2026  # first year the current d2/d3/d4 codes were used

# Canonical display names for codes that changed name across the rename —
# without this, League.name would flap between "AAA"/"AA"/"A" and "Division
# 2"/"Division 3"/"Division 4" depending on which year happened to be scraped
# last (see db/upsert.py: upsert() overwrites all non-key columns on every
# call).
CANONICAL_DISPLAY_NAMES: dict[str, str] = {"d2": "Division 2", "d3": "Division 3", "d4": "Division 4"}


def resolve_fetch_code(canonical_code: str, year: int) -> str:
    """Map a canonical League.code to the on-site URL code for a given year.

    Identity (returns canonical_code unchanged) for every code/year with no
    override — d5, nbl, dev, friendlies always, and d2/d3/d4 from 2026 on.
    """
    if year < HISTORICAL_CODE_CUTOFF_YEAR and canonical_code in HISTORICAL_CODE_OVERRIDES:
        return HISTORICAL_CODE_OVERRIDES[canonical_code]
    return canonical_code


def discover_years(league_code: str, known_year: int, session: Session | None = None, force_refresh: bool = False) -> list[int]:
    slug = f"{known_year}-{league_code}"
    url = f"{BASE_URL}/en/events/{slug}/editions"
    html = fetch_html(url, "editions", session=session, source_id=slug, force_refresh=force_refresh)
    pattern = re.compile(rf"events/(\d{{4}})-{re.escape(league_code)}/")
    years = sorted({int(y) for y in pattern.findall(html)})
    return years
