"""Self-calibrated league-average inputs for one league_season.

This is what makes WAR reflect this league's own run environment rather
than assuming MLB's — everything here is computed from this league_season's
own scraped data. Only the linear-weight *coefficients* used elsewhere
(stats/constants.py) are borrowed fixed values; the context here is not.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import (
    BattingSeasonStats,
    Game,
    LeagueSeasonContext,
    PitchingSeasonStats,
    PlayerSeason,
    TeamSeason,
)
from db.upsert import upsert
from stats import constants
from stats.rate_stats import obp, outs_to_ip, slg


def _league_batting_totals(session: Session, league_season_id: int) -> dict[str, int]:
    fields = ["pa", "ab", "h", "doubles", "triples", "hr", "bb", "ibb", "hbp", "so", "sf", "r"]
    cols = [func.sum(getattr(BattingSeasonStats, f)).label(f) for f in fields]
    row = (
        session.execute(
            select(*cols)
            .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id)
        )
    ).one()
    return {f: (getattr(row, f) or 0) for f in fields}


def _league_pitching_totals(session: Session, league_season_id: int) -> dict[str, int]:
    fields = ["outs_recorded", "h", "r", "er", "bb", "hbp", "so", "hr"]
    cols = [func.sum(getattr(PitchingSeasonStats, f)).label(f) for f in fields]
    row = (
        session.execute(
            select(*cols)
            .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id)
        )
    ).one()
    return {f: (getattr(row, f) or 0) for f in fields}


def _league_runs_per_game(session: Session, league_season_id: int) -> float | None:
    row = session.execute(
        select(func.sum(Game.home_score + Game.away_score), func.count())
        .where(Game.league_season_id == league_season_id, Game.status == "final")
    ).one()
    total_runs, game_count = row
    if not game_count:
        return None
    return (total_runs or 0) / (2 * game_count)


def compute_league_context(session: Session, league_season_id: int) -> int:
    bat = _league_batting_totals(session, league_season_id)
    pitch = _league_pitching_totals(session, league_season_id)

    lg_obp = obp(bat["h"], bat["bb"], bat["hbp"], bat["ab"], bat["sf"])
    lg_slg = slg(bat["h"], bat["doubles"], bat["triples"], bat["hr"], bat["ab"])

    woba_num = (
        constants.WOBA_WEIGHT_UBB * (bat["bb"] - bat["ibb"])
        + constants.WOBA_WEIGHT_HBP * bat["hbp"]
        + constants.WOBA_WEIGHT_1B * (bat["h"] - bat["doubles"] - bat["triples"] - bat["hr"])
        + constants.WOBA_WEIGHT_2B * bat["doubles"]
        + constants.WOBA_WEIGHT_3B * bat["triples"]
        + constants.WOBA_WEIGHT_HR * bat["hr"]
    )
    woba_denom = bat["ab"] + bat["bb"] - bat["ibb"] + bat["sf"] + bat["hbp"]
    lg_woba = (woba_num / woba_denom) if woba_denom else None

    ip = outs_to_ip(pitch["outs_recorded"])
    lg_era = (pitch["er"] * 9 / ip) if ip else None
    if ip and lg_era is not None:
        raw_fip = (
            constants.FIP_WEIGHT_HR * pitch["hr"]
            + constants.FIP_WEIGHT_BB_HBP * (pitch["bb"] + pitch["hbp"])
            - constants.FIP_WEIGHT_SO * pitch["so"]
        ) / ip
        fip_constant = lg_era - raw_fip
        lg_fip = raw_fip + fip_constant  # == lg_era by construction
    else:
        fip_constant = None
        lg_fip = None

    runs_per_pa = (bat["r"] / bat["pa"]) if bat["pa"] else None

    runs_per_game = _league_runs_per_game(session, league_season_id)
    runs_per_win = (
        constants.REFERENCE_RUNS_PER_WIN * (runs_per_game / constants.REFERENCE_RUNS_PER_GAME)
        if runs_per_game
        else constants.REFERENCE_RUNS_PER_WIN
    )

    replacement_runs_per_pa = constants.REPLACEMENT_RUNS_PER_600_PA / 600.0
    replacement_fip_delta = constants.REPLACEMENT_FIP_RUNS_PER_9

    context_id = upsert(
        session,
        LeagueSeasonContext,
        {
            "league_season_id": league_season_id,
            "lg_obp": lg_obp,
            "lg_slg": lg_slg,
            "lg_woba": lg_woba,
            "lg_era": lg_era,
            "lg_fip": lg_fip,
            "fip_constant": fip_constant,
            "runs_per_pa": runs_per_pa,
            "runs_per_win": runs_per_win,
            "replacement_runs_per_pa": replacement_runs_per_pa,
            "replacement_fip_delta": replacement_fip_delta,
        },
        ["league_season_id"],
    )
    session.commit()
    return context_id
