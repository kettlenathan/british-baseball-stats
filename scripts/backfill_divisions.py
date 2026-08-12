"""Backfill divisions and game phase for already-scraped league-seasons.

Everything this needs is already on disk: the schedule payload carrying each
game's `wbsc_tournament_group_id` and round is in data/raw_cache/schedule,
and the standings pages are in data/raw_cache/standings. So this replays
cached responses rather than re-scraping — no network, no rate-limit
exposure, and no risk of a partial live scrape landing in a database that is
about to be published.

It writes through the same functions the live scraper uses
(scrape_standings.apply_divisions), so backfilled rows and freshly-scraped
rows cannot diverge.

Usage:
    uv run python -m scripts.backfill_divisions              # offline, all league-seasons
    uv run python -m scripts.backfill_divisions --allow-fetch  # fetch anything not cached
    uv run python -m scripts.backfill_divisions --league-season-id 8
"""

import argparse

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from config import BASE_URL
from db.engine import get_session
from db.models import Game, League, LeagueSeason, Season
from scraper import cache
from scraper.discovery import resolve_fetch_code
from scraper.http_client import extract_inertia_page, fetch_html
from scraper.scrape_schedule import _game_phase, _modal_round_id
from scraper.scrape_standings import apply_divisions, parse_standings_divisions

# Long enough that nothing already cached is ever considered stale: this
# script's whole point is to read what is on disk, not to judge its age.
_NEVER_EXPIRE_HOURS = 24 * 365 * 100


def _cached_or_fetch(url: str, entity_type: str, slug: str, allow_fetch: bool) -> str | None:
    body = cache.get(entity_type, url, _NEVER_EXPIRE_HOURS)
    if body is not None:
        return body
    if not allow_fetch:
        return None
    return fetch_html(url, entity_type, source_id=slug, is_current_season=False)


def backfill_league_season(
    session: Session, league_season_id: int, slug: str, allow_fetch: bool = False
) -> dict[str, int | str]:
    """Backfill one league-season. Returns a small result summary for the CLI."""
    result: dict[str, int | str] = {"slug": slug, "games": 0, "playoffs": 0, "divisions": 0}

    schedule_body = _cached_or_fetch(
        f"{BASE_URL}/en/events/{slug}/schedule-and-results", "schedule", slug, allow_fetch
    )
    if schedule_body is None:
        result["note"] = "schedule not cached"
    else:
        page = extract_inertia_page(schedule_body)
        games = (page or {}).get("props", {}).get("games", [])
        modal_round_id = _modal_round_id(games)
        playoffs = 0
        for g in games:
            phase = _game_phase(
                g.get("gametypelabel"), g.get("wbsc_tournament_round_id"), modal_round_id
            )
            playoffs += phase == "playoff"
            # Updated by source_id rather than upserted: these games already
            # exist and only the two new columns are being filled in, so
            # there is no reason to rewrite scores or status from a payload
            # that may be older than the last real scrape.
            session.execute(
                update(Game)
                .where(Game.source_id == g["id"])
                .values(source_group_id=g.get("wbsc_tournament_group_id"), phase=phase)
            )
        result["games"] = len(games)
        result["playoffs"] = playoffs
        session.commit()

    standings_body = _cached_or_fetch(
        f"{BASE_URL}/en/events/{slug}/standings", "standings", slug, allow_fetch
    )
    if standings_body is None:
        result["note"] = f"{result.get('note', '')} standings not cached".strip()
    else:
        blocks = parse_standings_divisions(standings_body)
        result["divisions"] = apply_divisions(session, league_season_id, blocks)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-season-id", type=int, default=None)
    parser.add_argument(
        "--allow-fetch",
        action="store_true",
        help="Fetch from the site when a response isn't cached (default: skip it)",
    )
    args = parser.parse_args()

    session = get_session()
    try:
        query = (
            select(LeagueSeason.id, League.code, Season.year)
            .join(League, League.id == LeagueSeason.league_id)
            .join(Season, Season.id == LeagueSeason.season_id)
            .order_by(Season.year, League.code)
        )
        if args.league_season_id is not None:
            query = query.where(LeagueSeason.id == args.league_season_id)

        rows = session.execute(query).all()
        totals = {"games": 0, "playoffs": 0, "divisions": 0}
        for league_season_id, code, year in rows:
            slug = f"{year}-{resolve_fetch_code(code, year)}"
            result = backfill_league_season(session, league_season_id, slug, args.allow_fetch)
            for key in totals:
                totals[key] += int(result[key])
            note = f"  [{result['note']}]" if result.get("note") else ""
            print(
                f"{slug:10s} games={result['games']:4d} playoff={result['playoffs']:3d} "
                f"divisions={result['divisions']}{note}"
            )
        print(
            f"\nTotal: {totals['games']} games ({totals['playoffs']} playoff), "
            f"{totals['divisions']} divisions across {len(rows)} league-season(s)."
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
