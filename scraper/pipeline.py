"""CLI orchestrator: discovery -> schedule -> box scores.

Usage:
    uv run python -m scraper.pipeline --leagues nbl --years 2026
    uv run python -m scraper.pipeline --leagues nbl,d2 --years 2024-2026 --force-refresh
"""

import argparse
import datetime as dt
import time

from sqlalchemy import select

from db.engine import get_session
from db.models import Game
from scraper.scrape_boxscores import scrape_boxscore
from scraper.scrape_schedule import scrape_schedule

CURRENT_YEAR = dt.date.today().year

# Observed during Milestone 7 historical scraping: after a long sustained
# session (several full pipeline runs back to back) the site started
# returning 403 for 100+ consecutive requests in a row — this looks like a
# cumulative rate/request-count threshold, not a per-request one, and
# per-request retry backoff (scraper/http_client.py) just burns time
# hammering the same wall instead of helping. A circuit breaker pauses much
# longer after a run of consecutive failures, on the theory that whatever
# threshold triggered it needs real wall-clock time to reset — see
# scraper/recon/findings.md.
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_SECONDS = 180
CIRCUIT_BREAKER_MAX_TRIPS = 3
RETRY_COOLDOWN_SECONDS = 60


def _parse_years(spec: str) -> list[int]:
    if "-" in spec:
        start, end = spec.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(y) for y in spec.split(",")]


def run(
    league_codes: list[str],
    years: list[int],
    force_refresh: bool = False,
    since: dt.date | None = None,
) -> list[int]:
    """Returns the league_season ids touched, for the caller to recompute stats on.

    `since`, if given, limits box-score fetching to final games on or after
    that date — the schedule scrape itself always covers the whole season (a
    single cheap request that must see every game to detect newly-final
    ones); only the expensive per-game box-score loop is windowed."""
    session = get_session()
    league_season_ids = []
    failed_boxscores: list[tuple[str, int, int, bool]] = []
    try:
        for code in league_codes:
            for year in years:
                is_current = year >= CURRENT_YEAR
                print(f"Scraping schedule for {year}-{code} ...")
                try:
                    league_season_id, final_game_ids = scrape_schedule(
                        code,
                        year,
                        session,
                        force_refresh=force_refresh,
                        is_current_season=is_current,
                    )
                except Exception as exc:
                    # A single competition-year that 404s or fails outright
                    # (e.g. it never existed) shouldn't abort the rest of the
                    # scrape — log and move on to the next league/year.
                    print(f"  FAILED to scrape {year}-{code}: {exc}")
                    continue

                league_season_ids.append(league_season_id)
                if since is not None:
                    windowed_ids = set(
                        session.execute(
                            select(Game.source_id).where(
                                Game.league_season_id == league_season_id,
                                Game.status == "final",
                                Game.game_date >= since,
                            )
                        ).scalars()
                    )
                    final_game_ids = [gid for gid in final_game_ids if gid in windowed_ids]
                print(f"  {len(final_game_ids)} final games to fetch box scores for")
                consecutive_failures = 0
                circuit_breaker_trips = 0
                for i, game_id in enumerate(final_game_ids, start=1):
                    print(f"  [{i}/{len(final_game_ids)}] box score {game_id}")
                    try:
                        scrape_boxscore(
                            code,
                            year,
                            game_id,
                            session,
                            force_refresh=force_refresh,
                            is_current_season=is_current,
                        )
                        consecutive_failures = 0
                    except Exception as exc:
                        print(f"    FAILED box score {game_id}: {exc}")
                        failed_boxscores.append((code, year, game_id, is_current))
                        consecutive_failures += 1
                        if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                            circuit_breaker_trips += 1
                            if circuit_breaker_trips > CIRCUIT_BREAKER_MAX_TRIPS:
                                print(
                                    f"  Circuit breaker tripped {circuit_breaker_trips} times for "
                                    f"{year}-{code} — giving up on the rest of this season for now."
                                )
                                break
                            print(
                                f"  {consecutive_failures} consecutive failures — pausing "
                                f"{CIRCUIT_BREAKER_COOLDOWN_SECONDS}s (trip {circuit_breaker_trips}"
                                f"/{CIRCUIT_BREAKER_MAX_TRIPS}) ..."
                            )
                            time.sleep(CIRCUIT_BREAKER_COOLDOWN_SECONDS)
                            consecutive_failures = 0
                        continue

        if failed_boxscores:
            print(
                f"Retrying {len(failed_boxscores)} failed box score(s) after a "
                f"{RETRY_COOLDOWN_SECONDS}s cooldown ..."
            )
            time.sleep(RETRY_COOLDOWN_SECONDS)
            still_failed = []
            for code, year, game_id, is_current in failed_boxscores:
                try:
                    scrape_boxscore(
                        code, year, game_id, session, force_refresh=True, is_current_season=is_current
                    )
                    print(f"  retry succeeded: {year}-{code} box score {game_id}")
                except Exception as exc:
                    print(f"  retry still failed: {year}-{code} box score {game_id}: {exc}")
                    still_failed.append((code, year, game_id))
            if still_failed:
                print(f"{len(still_failed)} box score(s) permanently failed: {still_failed}")
    finally:
        session.close()
    return league_season_ids


def _resolve_since(args: argparse.Namespace) -> dt.date | None:
    if args.last_week:
        return dt.date.today() - dt.timedelta(days=7)
    if args.last_month:
        return dt.date.today() - dt.timedelta(days=30)
    return None


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
    run(league_codes, years, force_refresh=args.force_refresh, since=_resolve_since(args))


if __name__ == "__main__":
    main()
