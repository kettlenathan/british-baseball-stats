"""Batter pull/center/oppo tendency, derived from PlateAppearance's batted-
ball proxies (see db/models.py:PlateAppearance) bucketed against this
league-season's self-calibrated pull tertiles (see
stats/league_context.py's _pull_tertiles).

Re-runnable at any time after plate_appearances/league_season_context are
populated — never scraped directly. Must run after compute_league_context,
since it depends on that league-season's tertile cutoffs already existing.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import BatterSpraySeasonStats, LeagueSeasonContext, PlateAppearance, Player, PlayerSeason, TeamSeason
from db.upsert import upsert


def compute_batter_spray(session: Session, league_season_id: int) -> int:
    """Labels each batter's season tendency by whichever of pull/center/oppo
    holds a plurality of their batted balls. Switch hitters and unknown-
    handedness players are skipped entirely — no per-PA batting-side data
    exists to know which side they actually hit from (see
    stats/league_context.py's _pull_tertiles docstring)."""
    ctx = session.execute(
        select(LeagueSeasonContext).where(LeagueSeasonContext.league_season_id == league_season_id)
    ).scalar_one_or_none()
    if ctx is None or ctx.pull_tertile_low is None or ctx.pull_tertile_high is None:
        return 0

    rows = session.execute(
        select(PlateAppearance.batter_player_season_id, PlateAppearance.hitpull, Player.bats)
        .join(PlayerSeason, PlayerSeason.id == PlateAppearance.batter_player_season_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .join(Player, Player.id == PlayerSeason.player_id)
        .where(
            TeamSeason.league_season_id == league_season_id,
            PlateAppearance.hitpull.is_not(None),
        )
    ).all()

    counts: dict[int, dict[str, int]] = defaultdict(lambda: {"pull": 0, "center": 0, "oppo": 0})
    for ps_id, hitpull, bats in rows:
        if bats not in ("L", "R"):
            continue
        adj_pull = -hitpull if bats == "R" else hitpull
        if adj_pull > ctx.pull_tertile_high:
            bucket = "pull"
        elif adj_pull < ctx.pull_tertile_low:
            bucket = "oppo"
        else:
            bucket = "center"
        counts[ps_id][bucket] += 1

    count = 0
    for ps_id, bucket_counts in counts.items():
        tendency_label = max(bucket_counts, key=bucket_counts.get)
        upsert(
            session,
            BatterSpraySeasonStats,
            {
                "player_season_id": ps_id,
                "pull_count": bucket_counts["pull"],
                "center_count": bucket_counts["center"],
                "oppo_count": bucket_counts["oppo"],
                "tendency_label": tendency_label,
            },
            ["player_season_id"],
        )
        count += 1
    session.commit()
    return count
