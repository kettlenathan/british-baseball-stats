"""Empirical-Bayes shrinkage of season rate stats toward the league-season
mean, weighted by sample size ("true talent" estimation).

An amateur league produces many player-seasons with well under 100 PA/IP,
where the raw observed wOBA/FIP is mostly sampling noise. This module
estimates, from this league-season's own player population, how much to
trust an observed rate vs. the league mean:

    shrunk = (n * observed + k * league_mean) / (n + k)

`k` (the "stabilization point", in PA or IP units) is derived via a
method-of-moments variance decomposition: `k = V_e / tau^2`, where `V_e` is
the within-player sampling variance (estimated analytically from league-wide
event rates, treating each wOBA/FIP linear-weight event type as an
independent Poisson process — the standard simplifying assumption in
stabilization-point literature) and `tau^2` is the between-player
true-talent variance (method-of-moments over players/pitchers clearing a
minimum-sample floor). When a league-season's own population can't support
that estimate (too few qualifying players, or the variance decomposition
goes non-positive), this falls back to a published stabilization-point
constant instead — see FALLBACK_* below.
"""

import statistics

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    BattingSeasonStats,
    BattingTrueTalent,
    LeagueSeasonContext,
    PitchingSeasonStats,
    PitchingTrueTalent,
    PlayerSeason,
    TeamSeason,
)
from db.upsert import upsert
from stats import constants
from stats.advanced_stats import fip as fip_formula
from stats.advanced_stats import woba as woba_formula
from stats.league_context import _league_batting_totals, _league_pitching_totals
from stats.rate_stats import outs_to_ip

MIN_PA_FOR_VARIANCE = 20
MIN_IP_FOR_VARIANCE = 10.0
MIN_QUALIFYING_PLAYERS = 8

# Fallback stabilization points for when this league-season's own data can't
# support a self-calibrated estimate — published sabermetric stabilization-
# point research (FanGraphs / Russell Carleton), not derived from this
# league's own data like everything else in stats/league_context.py.
FALLBACK_BATTING_STABILIZATION_PA = 120.0
FALLBACK_PITCHING_STABILIZATION_IP = 60.0


def shrink_rate(observed: float | None, n: float, league_mean: float | None, k: float) -> float | None:
    """Empirical-Bayes posterior mean. Returns league_mean when there's no
    observed rate to shrink (n=0) or no league mean to shrink toward is
    unavailable falls back to the raw observed value."""
    if league_mean is None:
        return observed
    if observed is None or not n:
        return league_mean
    return (n * observed + k * league_mean) / (n + k)


def _batting_component_variance(totals: dict, league_pa: int) -> float | None:
    """Approximates within-player sampling variance per PA by treating each
    wOBA linear-weight event as an independent Poisson-rate process at the
    league's own observed rate (Var(weight * Bernoulli(p)) ~ weight^2 * p for
    small p) — mutually-exclusive PA outcomes aren't truly independent, but
    this is the standard simplification in stabilization-point literature."""
    if not league_pa:
        return None
    singles = totals["h"] - totals["doubles"] - totals["triples"] - totals["hr"]
    ubb = totals["bb"] - totals["ibb"]
    weighted_counts = {
        constants.WOBA_WEIGHT_UBB: ubb,
        constants.WOBA_WEIGHT_HBP: totals["hbp"],
        constants.WOBA_WEIGHT_1B: singles,
        constants.WOBA_WEIGHT_2B: totals["doubles"],
        constants.WOBA_WEIGHT_3B: totals["triples"],
        constants.WOBA_WEIGHT_HR: totals["hr"],
    }
    return sum(weight**2 * (count / league_pa) for weight, count in weighted_counts.items())


def _pitching_component_variance(totals: dict, league_ip: float) -> float | None:
    """Pitching-side counterpart, over FIP's linear-weight events per IP."""
    if not league_ip:
        return None
    return (
        constants.FIP_WEIGHT_HR**2 * (totals["hr"] / league_ip)
        + constants.FIP_WEIGHT_BB_HBP**2 * ((totals["bb"] + totals["hbp"]) / league_ip)
        + constants.FIP_WEIGHT_SO**2 * (totals["so"] / league_ip)
    )


def _estimate_stabilization(
    rows: list[tuple[float, float | None]], v_e: float | None, min_n: float, fallback: float
) -> tuple[float, bool]:
    qualifying = [(n, obs) for n, obs in rows if obs is not None and n >= min_n]
    if v_e is None or len(qualifying) < MIN_QUALIFYING_PLAYERS:
        return fallback, False

    observed = [obs for _, obs in qualifying]
    mean_inv_n = sum(1 / n for n, _ in qualifying) / len(qualifying)
    tau2 = statistics.pvariance(observed) - v_e * mean_inv_n
    if tau2 <= 0:
        return fallback, False
    return v_e / tau2, True


def estimate_batting_stabilization_pa(rows: list[tuple[float, float | None]], v_e: float | None) -> tuple[float, bool]:
    """rows: (pa, observed_woba) per qualifying player. Returns (k, self_calibrated)."""
    return _estimate_stabilization(rows, v_e, MIN_PA_FOR_VARIANCE, FALLBACK_BATTING_STABILIZATION_PA)


def estimate_pitching_stabilization_ip(rows: list[tuple[float, float | None]], v_e: float | None) -> tuple[float, bool]:
    """rows: (ip, observed_fip) per qualifying pitcher. Returns (k, self_calibrated)."""
    return _estimate_stabilization(rows, v_e, MIN_IP_FOR_VARIANCE, FALLBACK_PITCHING_STABILIZATION_IP)


def compute_batting_true_talent(session: Session, league_season_id: int) -> int:
    context = session.execute(
        select(LeagueSeasonContext).where(LeagueSeasonContext.league_season_id == league_season_id)
    ).scalar_one_or_none()
    if context is None or context.lg_woba is None:
        return 0

    totals = _league_batting_totals(session, league_season_id)
    v_e = _batting_component_variance(totals, totals["pa"])

    stats_rows = (
        session.execute(
            select(BattingSeasonStats)
            .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id)
        )
        .scalars()
        .all()
    )
    observed_by_row = {row.id: woba_formula(row) for row in stats_rows}
    k, self_calibrated = estimate_batting_stabilization_pa(
        [(row.pa, observed_by_row[row.id]) for row in stats_rows], v_e
    )

    count = 0
    for row in stats_rows:
        observed = observed_by_row[row.id]
        upsert(
            session,
            BattingTrueTalent,
            {
                "player_season_id": row.player_season_id,
                "pa": row.pa,
                "observed_woba": observed,
                "shrunk_woba": shrink_rate(observed, row.pa, context.lg_woba, k),
                "reliability": row.pa / (row.pa + k),
                "stabilization_pa": k,
                "k_self_calibrated": self_calibrated,
            },
            ["player_season_id"],
        )
        count += 1
    session.commit()
    return count


def compute_pitching_true_talent(session: Session, league_season_id: int) -> int:
    context = session.execute(
        select(LeagueSeasonContext).where(LeagueSeasonContext.league_season_id == league_season_id)
    ).scalar_one_or_none()
    if context is None or context.lg_fip is None or context.fip_constant is None:
        return 0

    totals = _league_pitching_totals(session, league_season_id)
    league_ip = outs_to_ip(totals["outs_recorded"])
    v_e = _pitching_component_variance(totals, league_ip)

    stats_rows = (
        session.execute(
            select(PitchingSeasonStats)
            .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id)
        )
        .scalars()
        .all()
    )
    observed_by_row = {row.id: fip_formula(row, context.fip_constant) for row in stats_rows}
    ip_by_row = {row.id: outs_to_ip(row.outs_recorded) for row in stats_rows}
    k, self_calibrated = estimate_pitching_stabilization_ip(
        [(ip_by_row[row.id], observed_by_row[row.id]) for row in stats_rows], v_e
    )

    count = 0
    for row in stats_rows:
        observed = observed_by_row[row.id]
        ip = ip_by_row[row.id]
        upsert(
            session,
            PitchingTrueTalent,
            {
                "player_season_id": row.player_season_id,
                "ip": ip,
                "observed_fip": observed,
                "shrunk_fip": shrink_rate(observed, ip, context.lg_fip, k),
                "reliability": ip / (ip + k),
                "stabilization_ip": k,
                "k_self_calibrated": self_calibrated,
            },
            ["player_season_id"],
        )
        count += 1
    session.commit()
    return count
