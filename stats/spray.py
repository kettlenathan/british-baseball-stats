"""Batter pull/center/oppo tendency, derived from PlateAppearance's batted-
ball proxies (see db/models.py:PlateAppearance) bucketed against fixed
thirds of the true 90-degree fair-territory fan (see
app/components/charts.py's PULL_FAN_HALF_WIDTH_DEGREES, the same +/-45
degree geometry the spray charts draw) rather than a self-calibrated
percentile split — a real ballpark's foul lines don't move with this
league's own batted-ball distribution, so neither should "pulled".

Re-runnable at any time after plate_appearances is populated — never
scraped directly. No longer depends on league_season_context; ordering
relative to compute_league_context doesn't matter.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import BatterSpraySeasonStats, PlateAppearance, Player, PlayerSeason, TeamSeason
from db.upsert import upsert

# Each half of the fan (0 to 45 degrees off dead-center, toward either foul
# line) splits into three equal segments — matching spray_heatmap's 9-bin
# fan (each bin 10 degrees wide, so 3 bins = 30 degrees = one third of the
# 90-degree fan's own half-width... concretely: the middle third is the
# center 30 degrees (+/-15), the outer thirds are the remaining 15-45
# degrees on each side.
PULL_THIRD_DEGREES = 45 / 3  # 15


def compute_batter_spray(session: Session, league_season_id: int) -> int:
    """Labels each batter's season tendency by whichever of pull/center/oppo
    holds a plurality of their batted balls. A ball is "pulled" if it's hit
    into the outer third of the fan on the batter's pull side (right field
    for a LHH, left field for a RHH), "oppo" if it's hit into the outer
    third on the other side, and "center" for the middle third regardless
    of handedness. Switch hitters and unknown-handedness players are
    skipped entirely — no per-PA batting-side data exists to know which
    side they actually hit from."""
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
        if adj_pull > PULL_THIRD_DEGREES:
            bucket = "pull"
        elif adj_pull < -PULL_THIRD_DEGREES:
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
