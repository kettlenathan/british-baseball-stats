"""Orchestrates the stats pipeline in dependency order: aggregation ->
league context -> batter spray / matchups -> WAR. Safe to re-run any time
after new games are scraped; never touches raw fact tables.

Usage: uv run python -m stats.recompute [--league-season-id N]
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.engine import get_session
from db.models import LeagueSeason
from stats.aggregation import aggregate_batting, aggregate_pitching
from stats.league_context import compute_league_context
from stats.matchups import compute_matchups
from stats.spray import compute_batter_spray
from stats.war import compute_batting_war, compute_pitching_war


def recompute_league_season(session: Session, league_season_id: int) -> None:
    aggregate_batting(session, league_season_id)
    aggregate_pitching(session, league_season_id)
    compute_league_context(session, league_season_id)
    # compute_batter_spray and compute_matchups both only need
    # plate_appearances, not compute_league_context's output — order among
    # these three doesn't matter, they're just grouped here for one pass.
    compute_batter_spray(session, league_season_id)
    compute_matchups(session, league_season_id)
    compute_batting_war(session, league_season_id)
    compute_pitching_war(session, league_season_id)


def recompute_all(session: Session) -> None:
    league_season_ids = [row[0] for row in session.execute(select(LeagueSeason.id))]
    for league_season_id in league_season_ids:
        recompute_league_season(session, league_season_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-season-id", type=int, default=None)
    args = parser.parse_args()

    session = get_session()
    try:
        if args.league_season_id is not None:
            recompute_league_season(session, args.league_season_id)
        else:
            recompute_all(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
