"""Self-calibrated league-average inputs, at two scopes.

This is what makes WAR reflect this league's own run environment rather
than assuming MLB's — everything here is computed from this league_season's
own scraped data. Only the linear-weight *coefficients* used elsewhere
(stats/constants.py) are borrowed fixed values; the context here is not.

Two scopes are computed, and the app shows both:

* **league-season** (LeagueSeasonContext) — every team in the competition,
  pooled. Unchanged from when it was the only scope.
* **division** (DivisionContext) — one regional division's own games only.

They answer different questions and neither replaces the other. Divisions
inside a single league are not the same run environment: 2026 Division 4
ranges from 11.34 runs per team per game (North, league wOBA .513) to 7.52
(London, .415), so a pooled mean misstates both ends. But judging every
player against their own division alone would erase real quality gaps, since
a weak division's hitters face weak pitching and would come out looking
average. Keeping both is what lets the app say which effect it is showing.

The two scopes run through one set of formulas (`_context_values`) on
purpose — the whole point is that a division's context is the same
calibration applied to a narrower slice, so the two must not be able to
drift apart.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import (
    BattingGameLine,
    BattingSeasonStats,
    Division,
    DivisionContext,
    Game,
    LeagueSeasonContext,
    PitchingGameLine,
    PitchingSeasonStats,
    PlayerSeason,
    TeamSeason,
)
from db.upsert import upsert
from stats import constants
from stats.rate_stats import obp, outs_to_ip, slg

_BATTING_FIELDS = ["pa", "ab", "h", "doubles", "triples", "hr", "bb", "ibb", "hbp", "so", "sf", "r"]
_PITCHING_FIELDS = ["outs_recorded", "h", "r", "er", "bb", "hbp", "so", "hr"]


def _context_values(
    bat: dict[str, int], pitch: dict[str, int], runs_per_game: float | None
) -> dict[str, float | None]:
    """The self-calibration formulas themselves, over pre-summed totals.

    Shared by both scopes so a division's context is provably the same
    calibration as the league's, just over a narrower slice of games.
    """
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

    runs_per_win = (
        constants.REFERENCE_RUNS_PER_WIN * (runs_per_game / constants.REFERENCE_RUNS_PER_GAME)
        if runs_per_game
        else constants.REFERENCE_RUNS_PER_WIN
    )

    return {
        "lg_obp": lg_obp,
        "lg_slg": lg_slg,
        "lg_woba": lg_woba,
        "lg_era": lg_era,
        "lg_fip": lg_fip,
        "fip_constant": fip_constant,
        "runs_per_pa": (bat["r"] / bat["pa"]) if bat["pa"] else None,
        "runs_per_win": runs_per_win,
        "replacement_runs_per_pa": constants.REPLACEMENT_RUNS_PER_600_PA / 600.0,
        "replacement_fip_delta": constants.REPLACEMENT_FIP_RUNS_PER_9,
    }


def _league_batting_totals(session: Session, league_season_id: int) -> dict[str, int]:
    fields = _BATTING_FIELDS
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
    fields = _PITCHING_FIELDS
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
    runs_per_game = _league_runs_per_game(session, league_season_id)

    context_id = upsert(
        session,
        LeagueSeasonContext,
        {
            "league_season_id": league_season_id,
            **_context_values(bat, pitch, runs_per_game),
        },
        ["league_season_id"],
    )
    session.commit()
    return context_id


# --------------------------------------------------------------------------
# Division scope
# --------------------------------------------------------------------------
#
# Division totals are summed from *game lines* joined to Game, not from the
# season-stats tables the league scope uses. That is deliberate: a player's
# season totals include every game they played, so summing them by their
# team's division would fold in playoff and cross-division results and
# quietly credit them to a division whose environment they were not part of.
#
# The filter is regular-season *and* intra-division. Both halves are needed.
# Cross-division games carry no division_id at all (scrape_standings.py), so
# division_id alone would already drop those — but it would keep a playoff
# between two teams of the *same* division while dropping one between teams
# of different divisions, which makes a division's run environment depend on
# how the bracket happened to fall. Excluding playoffs outright is uniform,
# and it also keeps the calibration to the games that actually define who
# this division's teams spent the season playing.
#
# Consumers wanting a different slice have both columns to filter on
# independently; that orthogonality is why phase is its own column rather
# than being folded into division_id.


def _division_batting_totals(session: Session, division_id: int) -> dict[str, int]:
    cols = [func.sum(getattr(BattingGameLine, f)).label(f) for f in _BATTING_FIELDS]
    row = session.execute(
        select(*cols)
        .join(Game, Game.id == BattingGameLine.game_id)
        .where(
            Game.division_id == division_id,
            Game.status == "final",
            Game.phase == "regular",
        )
    ).one()
    return {f: (getattr(row, f) or 0) for f in _BATTING_FIELDS}


def _division_pitching_totals(session: Session, division_id: int) -> dict[str, int]:
    cols = [func.sum(getattr(PitchingGameLine, f)).label(f) for f in _PITCHING_FIELDS]
    row = session.execute(
        select(*cols)
        .join(Game, Game.id == PitchingGameLine.game_id)
        .where(
            Game.division_id == division_id,
            Game.status == "final",
            Game.phase == "regular",
        )
    ).one()
    return {f: (getattr(row, f) or 0) for f in _PITCHING_FIELDS}


def _division_runs_per_game(session: Session, division_id: int) -> tuple[float | None, int]:
    """Returns (runs per team per game, games) so the caller can record how
    much data the calibration rests on."""
    total_runs, game_count = session.execute(
        select(func.sum(Game.home_score + Game.away_score), func.count()).where(
            Game.division_id == division_id,
            Game.status == "final",
            Game.phase == "regular",
        )
    ).one()
    if not game_count:
        return None, 0
    return (total_runs or 0) / (2 * game_count), game_count


def compute_division_contexts(session: Session, league_season_id: int) -> int:
    """Compute one context per division in this league_season.

    Returns the number written. A league_season with no divisions recorded
    (2021's NBL, or any season whose standings page could not be read)
    writes none and is not an error — every consumer treats a missing
    division context as "fall back to the league scope".
    """
    division_ids = session.execute(
        select(Division.id).where(Division.league_season_id == league_season_id)
    ).scalars().all()

    for division_id in division_ids:
        bat = _division_batting_totals(session, division_id)
        pitch = _division_pitching_totals(session, division_id)
        runs_per_game, games = _division_runs_per_game(session, division_id)
        upsert(
            session,
            DivisionContext,
            {
                "division_id": division_id,
                **_context_values(bat, pitch, runs_per_game),
                "games": games,
                "pa": bat["pa"],
            },
            ["division_id"],
        )
    session.commit()
    return len(division_ids)
