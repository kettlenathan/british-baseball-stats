"""End-to-end refresh: scrape then recompute derived stats.

Usage:
    uv run python -m scripts.refresh_data --leagues nbl --years 2026
    uv run python -m scripts.refresh_data --leagues nbl,d2 --years 2024-2026 --force-refresh
"""

import argparse

from db.engine import get_session
from scraper.pipeline import _parse_years, run
from stats.recompute import recompute_league_season


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leagues", required=True, help="Comma-separated league codes, e.g. nbl,d2")
    parser.add_argument("--years", required=True, help="Single year, comma-separated list, or range like 2024-2026")
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    league_codes = [c.strip() for c in args.leagues.split(",")]
    years = _parse_years(args.years)

    league_season_ids = run(league_codes, years, force_refresh=args.force_refresh)

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
