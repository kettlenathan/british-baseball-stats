"""Simplified batting + pitching WAR.

DISCLAIMER (also surfaced as a UI caption wherever WAR is shown): this is an
approximation, not official bWAR/fWAR. It is missing:
  - park factors (no per-venue run environment data for this league)
  - batted-ball / defensive tracking data, so there is no defensive
    component at all (batting WAR is offense-only, pitching WAR is
    FIP-based only)
  - linear weights derived from this league's own run-expectancy matrix —
    the wOBA/FIP coefficients are fixed, published sabermetric constants
    (see stats/constants.py), not re-derived from play-by-play data (which
    this league doesn't have)

What IS self-calibrated to this league, season by season (see
stats/league_context.py): league-average wOBA/ERA/FIP, the FIP additive
constant, and the runs-per-win conversion rate — so a "0 WAR" player here
means league-average within this league's own actual run environment, not
relative to MLB.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    BattingSeasonStats,
    BattingWar,
    LeagueSeasonContext,
    PitchingSeasonStats,
    PitchingWar,
    PlayerSeason,
    TeamSeason,
)
from db.upsert import upsert
from stats import constants
from stats.advanced_stats import fip, woba
from stats.rate_stats import outs_to_ip

WAR_DISCLAIMER = (
    "WAR here is a simplified approximation (offense/pitching only, no "
    "park factors or defensive data) self-calibrated to this league's own "
    "run environment each season — not comparable to official bWAR/fWAR."
)


def _get_context(session: Session, league_season_id: int) -> LeagueSeasonContext:
    return session.execute(
        select(LeagueSeasonContext).where(LeagueSeasonContext.league_season_id == league_season_id)
    ).scalar_one()


def compute_batting_war(session: Session, league_season_id: int) -> int:
    ctx = _get_context(session, league_season_id)
    rows = session.execute(
        select(BattingSeasonStats)
        .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .where(TeamSeason.league_season_id == league_season_id)
    ).scalars()

    count = 0
    for row in rows:
        player_woba = woba(row)
        wraa = None
        war = None
        if player_woba is not None and ctx.lg_woba is not None:
            wraa = ((player_woba - ctx.lg_woba) / constants.WOBA_SCALE) * row.pa
            if ctx.replacement_runs_per_pa is not None and ctx.runs_per_win:
                replacement_runs = ctx.replacement_runs_per_pa * row.pa
                war = (wraa + replacement_runs) / ctx.runs_per_win

        upsert(
            session,
            BattingWar,
            {
                "player_season_id": row.player_season_id,
                "woba": player_woba,
                "wraa": wraa,
                "war": war,
                "formula_version": constants.FORMULA_VERSION,
            },
            ["player_season_id"],
        )
        count += 1
    session.commit()
    return count


def compute_pitching_war(session: Session, league_season_id: int) -> int:
    ctx = _get_context(session, league_season_id)
    rows = session.execute(
        select(PitchingSeasonStats)
        .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .where(TeamSeason.league_season_id == league_season_id)
    ).scalars()

    count = 0
    for row in rows:
        player_fip = fip(row, ctx.fip_constant)
        war = None
        ip = outs_to_ip(row.outs_recorded)
        if player_fip is not None and ctx.lg_fip is not None and ip and ctx.runs_per_win:
            runs_above_replacement = (
                (ctx.lg_fip - player_fip + ctx.replacement_fip_delta) / 9
            ) * ip
            war = runs_above_replacement / ctx.runs_per_win

        upsert(
            session,
            PitchingWar,
            {
                "player_season_id": row.player_season_id,
                "fip": player_fip,
                "war": war,
                "formula_version": constants.FORMULA_VERSION,
            },
            ["player_season_id"],
        )
        count += 1
    session.commit()
    return count
