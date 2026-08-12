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

What a batter is shrunk *toward*
--------------------------------
Not the flat league mean. Playing time in this league is strongly
performance-selected, so "an unknown player is league average" is false in
a way that is large and measurable: across 9,602 player-seasons, wOBA minus
the league mean runs −.088 for players with 1-4 PA, −.064 at 5-9, −.042 at
10-19, through +.019 at 40-79 and +.064 at 80-149. A PA-weighted fit gives
`wOBA − lg = −0.190 + 0.052 * ln(PA)`, and the slope is positive in **all 25**
league-seasons with enough players to fit one (mean +.041, sd .012) — one of
the most stable relationships in the database. Shrinking a 6-PA hitter toward
the pooled mean therefore overrates them by 60-90 points of wOBA, which is
exactly the error the scouting report's lineup optimizer used to paper over
with an ad-hoc ranking penalty (see stats/lineup.py).

So the prior mean is a function of playing time: `lg_woba + f(PA)`, with `f`
fitted per league-season by PA-weighted least squares on ln(PA) and then
re-centred so its own PA-weighted mean is exactly zero — that re-centring is
what keeps the prior consistent with `lg_woba` rather than shifting the whole
league (the fit is self-centring in exact arithmetic, but LeagueSeasonContext
filters games slightly differently, so it is done explicitly).

`f` is **damped** by PLAYING_TIME_PRIOR_DAMPING rather than used at full
strength, because the contemporaneous relationship overstates the durable
part. Some of a low-PA line is genuinely a weaker hitter; some is a hitter
whose season was frozen at its worst by not being picked again. Splitting each
season chronologically and predicting the unseen remainder separates the two —
that is what scripts/validate_low_pa_prior.py measures, and the damping
constant is set from its output, not chosen by eye. Re-run it before changing
any of this.

That harness scores 6,654 held-out hitter-remainders and is unambiguous about
the direction: shrinking toward the flat mean beats ignoring the hitter
entirely by 3.4% and beats the raw line by 20%, and the damped playing-time
prior beats the flat mean by a further 0.71%. It is equally unambiguous that
the raw fit is too strong — applying it undamped is 0.44% *worse* than the
flat mean. The effect is real, and roughly 40% of it projects.

Keep the size of that last number in perspective: nearly all of the value in
this module comes from shrinking at all, and the playing-time prior is a
final sub-1% refinement on top. Its value is less that it predicts better on
average and more that it stops the model asserting something false about the
specific hitters it is most often asked about — the ones with a handful of
PA, where "league average" was never a neutral default.

Uncertainty
-----------
Each estimate carries its posterior SD, `sqrt(V_e / (n + k))` — the standard
EB result, and the honest replacement for a hand-tuned small-sample penalty.
Note how flat it is (.046 at 0 PA to .037 at 60 PA with k=120): once the prior
mean does the work of knowing that low-PA hitters are worse, what is left for
the uncertainty term to say is genuinely small. Consumers that must *rank*
players use estimate − z * sd; consumers that display a number show the
interval.
"""

import math
import statistics
from dataclasses import dataclass

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

# Fitting the playing-time prior needs both a population to fit on and a
# spread of playing time within it; below these it isn't a fit, it's a line
# through noise, and the flat league mean is the honest fallback.
MIN_PLAYERS_FOR_PLAYING_TIME_PRIOR = 40
MIN_LOG_PA_SPREAD = 0.75

# Fraction of the fitted playing-time effect actually applied. The
# contemporaneous fit measures talent *and* within-season attrition (a line
# frozen at its worst by not being picked again); only the first part should
# carry into a projection. Set from scripts/validate_low_pa_prior.py's
# held-out remainder-of-season test, where this is the pooled optimum
# (+0.71% against the flat league mean). Applying the fit at full strength
# scores measurably WORSE than the flat mean it replaced (-0.44%) — that is
# the attrition effect showing up as a number.
#
# Honest caveat rather than a clean sweep: 0.4 improves the 1-9, 20-39 and
# 40+ PA buckets and is 0.15% *worse* than the flat prior in the 10-19
# bucket. Damping 0.2 improves all four but gives up half the gain on
# regulars, and the two are identical (.1567) in the 1-9 bucket this exists
# for — so 0.4 is chosen on the pooled number with that one small regression
# accepted, not because nothing regresses. Re-run the script before changing
# this, and sweep it against MIN_PA_FOR_PRIOR_CURVE, which the two interact
# strongly through.
PLAYING_TIME_PRIOR_DAMPING = 0.40

# ln(PA) is undefined at 0 and violently steep just above it, so the prior
# curve is flat at and below this many PA. Set by the same held-out harness
# that sets the damping, sweeping it against damping — and it matters more
# than it looks: at a clamp of 3 the curve's bottom end is so steep that no
# damping above 0.2 improves the lightly-used hitters at all, while at 12 the
# fitted damping of 0.4 improves every playing-time bucket.
#
# The reason is a genuine limitation of the fit rather than a tuning quirk.
# The curve is fitted on *end-of-season* PA, where 3 PA means a hitter who
# was available all year and pointedly not picked. Read mid-season — which is
# when the scouting report is used — 3 PA often just means a hitter who
# hasn't debuted yet: a new signing, someone back from injury or travel. Those
# are not the same hitter, and the corpus can't tell them apart, so the curve
# must not extrapolate confidently into the range where they're confused.
MIN_PA_FOR_PRIOR_CURVE = 12.0


def shrink_rate(observed: float | None, n: float, league_mean: float | None, k: float) -> float | None:
    """Empirical-Bayes posterior mean. Returns league_mean when there's no
    observed rate to shrink (n=0) or no league mean to shrink toward is
    unavailable falls back to the raw observed value."""
    if league_mean is None:
        return observed
    if observed is None or not n:
        return league_mean
    return (n * observed + k * league_mean) / (n + k)


@dataclass(frozen=True)
class PlayingTimePrior:
    """How far a hitter's prior mean sits from the flat league mean, given
    how much they've played. `deviation(pa)` is in wOBA points, negative for
    lightly-used hitters, and is PA-weighted-zero across the population it
    was fitted on — so this re-weights who the league mean applies to without
    moving the league mean itself.

    `slope` already has the damping factor applied (see the module docstring
    on why the raw fit overstates the durable effect). `self_calibrated` says
    whether it came from this league-season's own players or from the
    corpus-wide fallback below, the same role `k_self_calibrated` plays for
    the stabilization point."""

    slope: float = 0.0
    center_log_pa: float = 0.0
    self_calibrated: bool = False

    def deviation(self, pa: float) -> float:
        return self.slope * (math.log(max(pa or 0.0, MIN_PA_FOR_PRIOR_CURVE)) - self.center_log_pa)

    def mean_for(self, league_mean: float | None, pa: float) -> float | None:
        """The prior this hitter is actually shrunk toward."""
        if league_mean is None:
            return None
        return league_mean + self.deviation(pa)


# Slope used when a league-season's own players can't support a fit: the
# corpus-wide value (all 9,602 player-seasons, each expressed relative to its
# own league mean before pooling), already damped.
#
# Falling back to *this* rather than to a flat league mean is the same choice
# the stabilization point already makes in falling back to a published
# constant rather than to "no shrinkage". Assuming no playing-time effect is
# not the neutral option; it is a specific claim, and one the data rejects in
# every league-season measured. All 25 real league-seasons currently fit
# their own curve, so this mostly guards small or partial ones — but where it
# does apply it must not quietly reinstate "an unknown hitter is average",
# because that is the assumption this whole layer exists to remove.
#
# Only the *slope* is borrowed. The pivot is always recomputed from the
# population being scored, because the re-centring is what keeps the prior's
# PA-weighted mean at zero — borrow a pivot fitted on full seasons and apply
# it to a half-season, where everyone has fewer PA by construction, and every
# hitter reads as lightly used and the whole league gets marked down.
FALLBACK_PLAYING_TIME_SLOPE = 0.0210
FALLBACK_PLAYING_TIME_CENTER_LOG_PA = 3.663

# The last-resort prior for when there is no population to centre on at all.
FALLBACK_PLAYING_TIME_PRIOR = PlayingTimePrior(
    slope=FALLBACK_PLAYING_TIME_SLOPE,
    center_log_pa=FALLBACK_PLAYING_TIME_CENTER_LOG_PA,
    self_calibrated=False,
)


def fit_playing_time_prior(
    rows: list[tuple[float, float | None]],
    league_mean: float | None,
    damping: float = PLAYING_TIME_PRIOR_DAMPING,
) -> PlayingTimePrior:
    """rows: (pa, observed_woba) per player-season in one league-season.

    PA-weighted least squares of (observed - league_mean) on ln(PA), damped,
    and re-centred on the population's PA-weighted mean ln(PA) so the fitted
    deviation averages to exactly zero. Because the re-centring pins the
    intercept, the whole curve is one number — the slope — plus where it
    pivots. Returns FALLBACK_PLAYING_TIME_PRIOR when the population can't
    support a fit, so callers never have to special-case that.

    `damping=0.0` yields a flat prior and is how scripts/validate_low_pa_prior.py
    reproduces the previous league-mean behaviour as a baseline."""
    if damping == 0.0:
        return PlayingTimePrior(slope=0.0, center_log_pa=0.0, self_calibrated=True)
    usable = [(pa, obs) for pa, obs in rows if obs is not None and pa and pa > 0]
    if league_mean is None or not usable:
        return FALLBACK_PLAYING_TIME_PRIOR

    xs = [math.log(max(pa, MIN_PA_FOR_PRIOR_CURVE)) for pa, _ in usable]
    ys = [obs - league_mean for _, obs in usable]
    ws = [float(pa) for pa, _ in usable]

    total_w = sum(ws)
    x_bar = sum(w * x for w, x in zip(ws, xs)) / total_w
    y_bar = sum(w * y for w, y in zip(ws, ys)) / total_w

    def _borrowed() -> PlayingTimePrior:
        """Corpus slope, this population's own pivot — see
        FALLBACK_PLAYING_TIME_SLOPE for why the pivot is never borrowed."""
        return PlayingTimePrior(
            slope=damping / PLAYING_TIME_PRIOR_DAMPING * FALLBACK_PLAYING_TIME_SLOPE,
            center_log_pa=x_bar,
            self_calibrated=False,
        )

    # A roster where everyone played the same amount carries no information
    # about how playing time relates to talent, and dividing by its ~zero
    # variance would produce an enormous slope from rounding noise.
    if len(usable) < MIN_PLAYERS_FOR_PLAYING_TIME_PRIOR or max(xs) - min(xs) < MIN_LOG_PA_SPREAD:
        return _borrowed()
    s_xx = sum(w * (x - x_bar) ** 2 for w, x in zip(ws, xs))
    if s_xx <= 0:
        return _borrowed()
    s_xy = sum(w * (x - x_bar) * (y - y_bar) for w, x, y in zip(ws, xs, ys))

    slope = damping * (s_xy / s_xx)
    # A negative slope would say this league's *regulars* are its worst
    # hitters. That is not a thing that happens (25 of 25 league-seasons fit
    # positive); seeing one means the population is too small or too odd to
    # trust, so fall back rather than invert the correction.
    if slope <= 0:
        return _borrowed()
    return PlayingTimePrior(slope=slope, center_log_pa=x_bar, self_calibrated=True)


def posterior_sd(v_e: float | None, n: float, k: float) -> float | None:
    """Standard deviation of the shrunk estimate: sqrt(V_e / (n + k)), the
    empirical-Bayes posterior SD. At n=0 this correctly reduces to the
    between-player talent spread tau = sqrt(V_e / k) — an unplayed hitter is
    uncertain by exactly as much as players differ from each other."""
    if v_e is None or v_e < 0 or (n + k) <= 0:
        return None
    return math.sqrt(v_e / (n + k))


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
    pairs = [(row.pa, observed_by_row[row.id]) for row in stats_rows]
    k, self_calibrated = estimate_batting_stabilization_pa(pairs, v_e)
    # Fitted on every hitter, not just those clearing a minimum — the
    # lightly-used end is the part of the curve being estimated.
    prior = fit_playing_time_prior(pairs, context.lg_woba)

    count = 0
    for row in stats_rows:
        observed = observed_by_row[row.id]
        prior_woba = prior.mean_for(context.lg_woba, row.pa)
        upsert(
            session,
            BattingTrueTalent,
            {
                "player_season_id": row.player_season_id,
                "pa": row.pa,
                "observed_woba": observed,
                "prior_woba": prior_woba,
                "shrunk_woba": shrink_rate(observed, row.pa, prior_woba, k),
                "shrunk_woba_sd": posterior_sd(v_e, row.pa, k),
                "reliability": row.pa / (row.pa + k),
                "stabilization_pa": k,
                "k_self_calibrated": self_calibrated,
                "prior_ln_pa_slope": prior.slope if prior.self_calibrated else None,
                "prior_center_log_pa": prior.center_log_pa,
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
