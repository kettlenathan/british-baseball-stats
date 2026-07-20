"""End-to-end refresh: scrape then recompute derived stats.

Usage:
    uv run python -m scripts.refresh_data --leagues nbl --years 2026
    uv run python -m scripts.refresh_data --leagues nbl,d2 --years 2024-2026 --force-refresh
    uv run python -m scripts.refresh_data --leagues nbl --years 2026 --last-week
"""

import argparse
import sys

from db.engine import get_session
from scraper.pipeline import _parse_years, _resolve_since, run
from stats.recompute import recompute_league_season


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leagues", required=True, help="Comma-separated league codes, e.g. nbl,d2")
    parser.add_argument("--years", required=True, help="Single year, comma-separated list, or range like 2024-2026")
    parser.add_argument("--force-refresh", action="store_true")
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--last-week", action="store_true", help="Only fetch box scores for games in the last 7 days"
    )
    window.add_argument(
        "--last-month", action="store_true", help="Only fetch box scores for games in the last 30 days"
    )
    args = parser.parse_args()

    league_codes = [c.strip() for c in args.leagues.split(",")]
    years = _parse_years(args.years)

    league_season_ids = run(
        league_codes, years, force_refresh=args.force_refresh, since=_resolve_since(args)
    )

    if not league_season_ids:
        # Every requested league-season failed at the schedule-scrape stage
        # (e.g. the site is blocking this host outright). Exit non-zero so a
        # scheduled CI run fails visibly and skips republishing an unchanged
        # database, instead of reporting a green no-op.
        print(
            "ERROR: no league-seasons were scraped — every schedule fetch failed. "
            "Nothing to recompute or publish.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print("Recomputing derived stats ...")
    session = get_session()
    try:
        for league_season_id in set(league_season_ids):
            recompute_league_season(session, league_season_id)
    finally:
        session.close()
    print("Done.")


if __name__ == "__main__":
    main()
