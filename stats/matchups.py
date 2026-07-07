"""Aggregated batter-vs-pitcher plate-appearance results for one
league_season — see db/models.py:BatterPitcherMatchup. No minimum-PA filter
is applied here; career totals are summed across these rows at read time
(app/components/data_access.py), not stored separately.

Re-runnable at any time after plate_appearances is populated — never
scraped directly. No ordering dependency on compute_league_context.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import BatterPitcherMatchup, Game, PlateAppearance
from db.upsert import upsert

_COUNT_FIELDS = ["ab", "h", "doubles", "triples", "hr", "bb", "so", "hbp"]


def compute_matchups(session: Session, league_season_id: int) -> int:
    plate_appearances = (
        session.execute(
            select(PlateAppearance)
            .join(Game, Game.id == PlateAppearance.game_id)
            .where(
                Game.league_season_id == league_season_id,
                PlateAppearance.pitcher_player_season_id.is_not(None),
            )
        )
        .scalars()
        .all()
    )

    totals: dict[tuple[int, int], dict[str, int]] = defaultdict(
        lambda: {"pa": 0, **{f: 0 for f in _COUNT_FIELDS}}
    )
    for pa in plate_appearances:
        entry = totals[(pa.batter_player_season_id, pa.pitcher_player_season_id)]
        entry["pa"] += 1
        for f in _COUNT_FIELDS:
            entry[f] += getattr(pa, f)

    count = 0
    for (batter_ps_id, pitcher_ps_id), values in totals.items():
        upsert(
            session,
            BatterPitcherMatchup,
            {
                "batter_player_season_id": batter_ps_id,
                "pitcher_player_season_id": pitcher_ps_id,
                **values,
            },
            ["batter_player_season_id", "pitcher_player_season_id"],
        )
        count += 1
    session.commit()
    return count
