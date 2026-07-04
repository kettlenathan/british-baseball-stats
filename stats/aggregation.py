"""Aggregate game-level fact rows into season totals.

Re-runnable at any time after new games are scraped — never scraped
directly, always derived from batting_game_lines / pitching_game_lines.
"""

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session

from db.models import (
    BattingGameLine,
    BattingSeasonStats,
    PitchingGameLine,
    PitchingSeasonStats,
    PlayerSeason,
    TeamSeason,
)
from db.upsert import upsert

_BATTING_COUNT_FIELDS = [
    "pa", "ab", "r", "h", "doubles", "triples", "hr", "rbi", "bb", "ibb",
    "hbp", "so", "sf", "sh", "sb", "cs", "gdp",
    "field_po", "field_a", "field_e", "field_dp",
    "risp_ab", "risp_h",
]

_PITCHING_COUNT_FIELDS = ["outs_recorded", "h", "r", "er", "bb", "ibb", "so", "hr", "hbp", "bf"]


def _player_seasons_for_league_season(session: Session, league_season_id: int | None) -> list[int]:
    query = select(PlayerSeason.id)
    if league_season_id is not None:
        query = query.join(TeamSeason).where(TeamSeason.league_season_id == league_season_id)
    return [row[0] for row in session.execute(query)]


def aggregate_batting(session: Session, league_season_id: int | None = None) -> int:
    player_season_ids = _player_seasons_for_league_season(session, league_season_id)
    count = 0
    for ps_id in player_season_ids:
        cols = [func.sum(getattr(BattingGameLine, f)).label(f) for f in _BATTING_COUNT_FIELDS]
        row = session.execute(
            select(*cols).where(BattingGameLine.player_season_id == ps_id)
        ).one()
        totals = {f: (getattr(row, f) or 0) for f in _BATTING_COUNT_FIELDS}
        if totals["pa"] == 0:
            continue
        upsert(session, BattingSeasonStats, {"player_season_id": ps_id, **totals}, ["player_season_id"])
        count += 1
    session.commit()
    return count


def aggregate_pitching(session: Session, league_season_id: int | None = None) -> int:
    player_season_ids = _player_seasons_for_league_season(session, league_season_id)
    count = 0
    for ps_id in player_season_ids:
        cols = [func.sum(getattr(PitchingGameLine, f)).label(f) for f in _PITCHING_COUNT_FIELDS]
        cols += [
            func.sum(cast(PitchingGameLine.win, Integer)).label("wins"),
            func.sum(cast(PitchingGameLine.loss, Integer)).label("losses"),
            func.sum(cast(PitchingGameLine.save, Integer)).label("saves"),
        ]
        row = session.execute(
            select(*cols).where(PitchingGameLine.player_season_id == ps_id)
        ).one()
        totals = {f: (getattr(row, f) or 0) for f in _PITCHING_COUNT_FIELDS}
        if totals["bf"] == 0 and totals["outs_recorded"] == 0:
            continue
        totals["wins"] = row.wins or 0
        totals["losses"] = row.losses or 0
        totals["saves"] = row.saves or 0

        upsert(session, PitchingSeasonStats, {"player_season_id": ps_id, **totals}, ["player_season_id"])
        count += 1
    session.commit()
    return count
