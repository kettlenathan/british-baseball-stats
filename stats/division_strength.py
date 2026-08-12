"""Cross-division strength offsets, estimated from players who appear in
more than one division.

**The problem.** Teams play only inside their own division, so the whole
corpus contains no regular-season evidence linking one division's standard
to another's. Milton Keynes Bucks went 20-0 in 2026 Division 3 Central and
London Meteors 19-5 in Division 3 South, and the two never met. The
Bradley-Terry ratings in stats/team_strength.py are therefore centred inside
each division and are explicitly not comparable across them.

**The bridge.** Players are not confined to one division. Across the corpus
1,221 pairs of division-seasons share at least one player, and the resulting
network connects all 78 division-seasons into a single component even when
each link is required to carry 25+ plate appearances on both sides. When the
same person bats in two divisions, the difference in what they produce is
evidence about the difference between those divisions — which is the same
reasoning behind Major League Equivalencies, applied to a league that needs
it badly.

**The estimator** is a two-way fixed-effects model on batting:

    wOBA(player p, division d) = talent(p) + offset(d) + noise

weighted by plate appearances. Crucially the player effects are left
**unpenalised**, so `offset` is identified purely from *within-player*
variation: a player who appears in only one division contributes exactly
nothing to any offset, and cannot drag a division up merely by being good.
Only the offsets are ridge-penalised, toward a common mean, because several
pairs of divisions are linked by very few players.

A positive offset means the division was an *easier place to bat*, so it
implies weaker pitching, not a stronger division. Converting that into "are
these teams better" needs a scale factor that batting data alone cannot
supply, which is why nothing here writes a strength number on its own — see
scripts/validate_division_strength.py, which fits that one scalar against
held-out cross-division games and reports whether it beats assuming the
divisions are equal.

**Known bias, deliberately not corrected here.** A player who turns out in
two divisions is not a random sample: they may be a stronger player guesting
down, or a fringe player called up. That selection inflates the apparent gap
in whichever direction the movement runs, and no amount of weighting inside
this model fixes it. It is the main reason the offsets are reported with
standard errors and bridge counts rather than as settled facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import BattingGameLine, Division, Game, PlayerSeason
from stats.advanced_stats import woba

# Ridge penalty on the offsets. Deliberately mild: it exists to stop a
# division linked by two players from swinging wildly, not to flatten real
# differences, and the offsets are already anchored by being centred.
DEFAULT_RIDGE_LAMBDA = 25.0

# A player-division stint below this many plate appearances is too noisy to
# say anything about the division; including them adds variance without
# adding signal. 15 PA is roughly two games' worth here.
MIN_PA_PER_STINT = 15

# Below this many cross-division games there is no honest way to calibrate
# wOBA difficulty into win probability, and no cross-division comparison
# should be offered at all rather than one resting on a handful of results.
MIN_GAMES_FOR_SCALE = 50


@dataclass(frozen=True)
class Stint:
    """One player's batting in one division-season."""

    player_id: int
    division_id: int
    value: float
    weight: float


@dataclass
class DivisionOffset:
    offset: float
    standard_error: float
    bridge_players: int
    bridge_pa: float
    stints: int


@dataclass
class OffsetFit:
    offsets: dict[int, DivisionOffset] = field(default_factory=dict)
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA
    # Players contributing any within-player variation at all — i.e. those
    # who actually appeared in more than one division. Everything rests on
    # these, so the count belongs next to the numbers.
    identifying_players: int = 0
    residual_sd: float | None = None


def fit_division_offsets(
    stints: list[Stint], ridge_lambda: float = DEFAULT_RIDGE_LAMBDA
) -> OffsetFit:
    """Estimate one offset per division from multi-division players.

    Implemented by the within-player transformation rather than by building
    a design matrix with one column per player: subtracting each player's own
    weighted mean absorbs their talent exactly, leaving a small dense system
    in the divisions alone (78 x 78 on the current corpus). That is both far
    cheaper and, more importantly, makes the identifying assumption visible —
    a single-division player's rows become all-zero and drop out on their own.
    """
    divisions = sorted({s.division_id for s in stints})
    if not divisions:
        return OffsetFit()

    index = {d: i for i, d in enumerate(divisions)}
    by_player: dict[int, list[Stint]] = {}
    for stint in stints:
        by_player.setdefault(stint.player_id, []).append(stint)

    n_div = len(divisions)
    design_rows: list[np.ndarray] = []
    value_rows: list[float] = []
    weight_rows: list[float] = []
    identifying = 0

    for player_stints in by_player.values():
        if len({s.division_id for s in player_stints}) < 2:
            # One division only: the player effect absorbs everything and
            # this player says nothing about how divisions compare.
            continue

        weights = np.array([s.weight for s in player_stints])
        values = np.array([s.value for s in player_stints])
        total_weight = weights.sum()
        if total_weight <= 0:
            continue
        identifying += 1

        # Within-player demeaning, weighted.
        design = np.zeros((len(player_stints), n_div))
        for row, stint in enumerate(player_stints):
            design[row, index[stint.division_id]] = 1.0
        mean_design = (weights @ design) / total_weight
        mean_value = float(np.dot(weights, values) / total_weight)

        design_rows.append(design - mean_design)
        value_rows.extend(values - mean_value)
        weight_rows.extend(weights)

    if not design_rows:
        # No player bridges any two divisions, so nothing is identified.
        # Return zeroed offsets for every division rather than an empty
        # mapping: callers should get the same shape whether or not the
        # evidence exists, and "no difference established" is exactly what a
        # zero with no bridges behind it means.
        return _uninformative_fit(divisions, by_player, ridge_lambda, identifying)

    X = np.vstack(design_rows)
    y = np.array(value_rows)
    w = np.array(weight_rows)

    normal = X.T @ (X * w[:, None])
    rhs = (X * w[:, None]).T @ y
    penalised = normal + np.eye(n_div) * ridge_lambda
    try:
        offsets = np.linalg.solve(penalised, rhs)
        covariance = np.linalg.inv(penalised)
    except np.linalg.LinAlgError:
        return OffsetFit(ridge_lambda=ridge_lambda, identifying_players=identifying)

    # Standard errors need the variance of a *single plate appearance*, not
    # of a stint's wOBA. Weighting by PA is what makes this weighted least
    # squares with unit variance sigma^2_PA, so that is what has to be
    # estimated: sum(w * residual^2) over the residual degrees of freedom.
    # Scaling by the stint-level spread instead understates the intervals by
    # more than an order of magnitude, which would make thinly-bridged
    # divisions look far more settled than they are.
    residuals = y - X @ offsets
    dof = max(len(y) - n_div, 1)
    sigma_sq_per_pa = float(np.sum(w * residuals**2) / dof)
    residual_sd = float(np.sqrt(sigma_sq_per_pa))

    # Centre so that 0 is the average division rather than an arbitrary
    # reference — the offsets are only ever used as differences, and a
    # floating origin would make them unreadable.
    offsets = offsets - offsets.mean()

    bridge_players, bridge_pa, stint_counts = _bridge_diagnostics(by_player)

    return OffsetFit(
        offsets={
            division: DivisionOffset(
                offset=float(offsets[index[division]]),
                standard_error=float(
                    np.sqrt(
                        max(covariance[index[division], index[division]], 0.0)
                        * sigma_sq_per_pa
                    )
                ),
                bridge_players=bridge_players.get(division, 0),
                bridge_pa=bridge_pa.get(division, 0.0),
                stints=stint_counts.get(division, 0),
            )
            for division in divisions
        },
        ridge_lambda=ridge_lambda,
        identifying_players=identifying,
        residual_sd=residual_sd,
    )


def _uninformative_fit(
    divisions: list[int],
    by_player: dict[int, list[Stint]],
    ridge_lambda: float,
    identifying: int,
) -> OffsetFit:
    bridge_players, bridge_pa, stint_counts = _bridge_diagnostics(by_player)
    return OffsetFit(
        offsets={
            division: DivisionOffset(
                offset=0.0,
                standard_error=float("nan"),
                bridge_players=bridge_players.get(division, 0),
                bridge_pa=bridge_pa.get(division, 0.0),
                stints=stint_counts.get(division, 0),
            )
            for division in divisions
        },
        ridge_lambda=ridge_lambda,
        identifying_players=identifying,
    )


def _bridge_diagnostics(
    by_player: dict[int, list[Stint]],
) -> tuple[dict[int, int], dict[int, float], dict[int, int]]:
    """Per division: how many multi-division players touch it, how much
    playing time they brought, and how many stints in total.

    Reported alongside every offset because the estimate is only ever as
    trustworthy as the bridges behind it, and those vary by an order of
    magnitude between divisions.
    """
    bridge_players: dict[int, int] = {}
    bridge_pa: dict[int, float] = {}
    stints: dict[int, int] = {}
    for player_stints in by_player.values():
        multi = len({s.division_id for s in player_stints}) >= 2
        for stint in player_stints:
            stints[stint.division_id] = stints.get(stint.division_id, 0) + 1
            if multi:
                bridge_players[stint.division_id] = bridge_players.get(stint.division_id, 0) + 1
                bridge_pa[stint.division_id] = bridge_pa.get(stint.division_id, 0.0) + stint.weight
    return bridge_players, bridge_pa, stints


# --------------------------------------------------------------------------
# DB layer
# --------------------------------------------------------------------------


def load_stints(session: Session, min_pa: int = MIN_PA_PER_STINT) -> list[Stint]:
    """One Stint per (player, division-season) with enough plate appearances.

    Built from game lines filtered to regular-season, intra-division, played
    games — the same slice DivisionContext calibrates on — rather than from
    season totals, so a player's line for a division contains only the games
    that actually belong to it.
    """
    rows = session.execute(
        select(
            PlayerSeason.player_id,
            Game.division_id,
            func.sum(BattingGameLine.ab).label("ab"),
            func.sum(BattingGameLine.h).label("h"),
            func.sum(BattingGameLine.doubles).label("doubles"),
            func.sum(BattingGameLine.triples).label("triples"),
            func.sum(BattingGameLine.hr).label("hr"),
            func.sum(BattingGameLine.bb).label("bb"),
            func.sum(BattingGameLine.ibb).label("ibb"),
            func.sum(BattingGameLine.hbp).label("hbp"),
            func.sum(BattingGameLine.sf).label("sf"),
            func.sum(BattingGameLine.pa).label("pa"),
        )
        .join(PlayerSeason, PlayerSeason.id == BattingGameLine.player_season_id)
        .join(Game, Game.id == BattingGameLine.game_id)
        .where(
            Game.division_id.isnot(None),
            Game.status == "final",
            Game.phase == "regular",
            Game.result_type == "played",
        )
        .group_by(PlayerSeason.player_id, Game.division_id)
    ).all()

    stints: list[Stint] = []
    for row in rows:
        if (row.pa or 0) < min_pa:
            continue
        # Reuse the one wOBA implementation rather than restating the
        # weights here — this layer must never carry a second copy of a
        # sabermetric formula.
        value = woba(
            SimpleNamespace(
                ab=row.ab or 0,
                h=row.h or 0,
                doubles=row.doubles or 0,
                triples=row.triples or 0,
                hr=row.hr or 0,
                bb=row.bb or 0,
                ibb=row.ibb or 0,
                hbp=row.hbp or 0,
                sf=row.sf or 0,
            )
        )
        if value is None:
            continue
        stints.append(
            Stint(
                player_id=row.player_id,
                division_id=row.division_id,
                value=value,
                weight=float(row.pa),
            )
        )
    return stints


def compute_division_offsets(
    session: Session, ridge_lambda: float = DEFAULT_RIDGE_LAMBDA
) -> OffsetFit:
    """Fit offsets across every division in the database at once.

    Deliberately global rather than per league-season: the bridges that
    connect two divisions of the same league frequently run through a third
    division in another league or another year, and fitting one league-season
    in isolation would throw that evidence away. Only 88 of the 336 games
    that could ever test these offsets have a *direct* same-season bridge for
    their own division pair; the rest depend on those longer paths.
    """
    return fit_division_offsets(load_stints(session), ridge_lambda=ridge_lambda)


def fit_strength_scale(session: Session, offsets: dict[int, float]) -> float | None:
    """The one number batting data cannot supply: how many log-odds of
    winning a wOBA point of division difficulty is worth.

    Fitted against every cross-division game in the corpus — the playoffs and
    the handful of earlier seasons that crossed divisions mid-season. Those
    are the only games that carry the information, and using them here is not
    circular: nothing else in the pipeline touches them, and
    scripts/validate_division_strength.py holds them out fold by fold to
    check the whole construction predicts games it has never seen.

    Returns None when there are too few such games to fit, in which case no
    cross-division comparison should be offered at all.

    The expected sign is negative — an easier division to bat in has weaker
    pitching, so its teams should be weaker. A positive fit would mean the
    premise is wrong, and is returned as-is rather than clamped, so a caller
    or a test can notice.
    """
    from db.models import Game, TeamSeason, TeamStrength

    strength = {
        row.team_season_id: row
        for row in session.execute(select(TeamStrength)).scalars()
    }
    divisions = {
        ts_id: division_id
        for ts_id, division_id in session.execute(
            select(TeamSeason.id, TeamSeason.division_id).where(
                TeamSeason.division_id.isnot(None)
            )
        )
    }

    home = TeamSeason.__table__.alias("home_ts")
    away = TeamSeason.__table__.alias("away_ts")
    games = session.execute(
        select(
            Game.home_team_season_id,
            Game.away_team_season_id,
            Game.home_score,
            Game.away_score,
        )
        .join(home, home.c.id == Game.home_team_season_id)
        .join(away, away.c.id == Game.away_team_season_id)
        .where(
            Game.status == "final",
            Game.result_type == "played",
            home.c.division_id.isnot(None),
            away.c.division_id.isnot(None),
            home.c.division_id != away.c.division_id,
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
        )
    ).all()

    rows = []
    for home_id, away_id, home_score, away_score in games:
        if home_id not in strength or away_id not in strength:
            continue
        base = (
            (strength[home_id].rating or 0.0)
            - (strength[away_id].rating or 0.0)
            + (strength[home_id].home_advantage or 0.0)
        )
        gap = offsets.get(divisions.get(home_id), 0.0) - offsets.get(
            divisions.get(away_id), 0.0
        )
        outcome = 0.5 if home_score == away_score else float(home_score > away_score)
        rows.append((base, gap, outcome))

    if len(rows) < MIN_GAMES_FOR_SCALE:
        return None

    scale = 0.0
    for _ in range(50):
        gradient = 0.0
        hessian = 0.0
        for base, gap, outcome in rows:
            p = 1.0 / (1.0 + np.exp(-(base + scale * gap)))
            gradient += gap * (outcome - p)
            hessian += gap * gap * p * (1 - p)
        if hessian <= 1e-12:
            break
        step = gradient / hessian
        scale += step
        if abs(step) < 1e-10:
            break
    return float(scale)


def compute_division_strength(session: Session) -> int:
    """Fit offsets and the log-odds scale, and store one row per division.

    Global rather than per league-season — see compute_division_offsets — so
    this runs once after every league-season has been recomputed, not inside
    the per-season loop.
    """
    from db.models import DivisionStrength
    from db.upsert import upsert

    fit = compute_division_offsets(session)
    if not fit.offsets:
        return 0
    scale = fit_strength_scale(session, {d: o.offset for d, o in fit.offsets.items()})

    for division_id, offset in fit.offsets.items():
        upsert(
            session,
            DivisionStrength,
            {
                "division_id": division_id,
                "offset": offset.offset,
                "standard_error": offset.standard_error,
                "adjustment": None if scale is None else scale * offset.offset,
                "adjustment_se": (
                    None if scale is None else abs(scale) * offset.standard_error
                ),
                "bridge_players": offset.bridge_players,
                "bridge_pa": offset.bridge_pa,
                "scale": scale,
                "identifying_players": fit.identifying_players,
            },
            ["division_id"],
        )
    session.commit()
    return len(fit.offsets)


def division_labels(session: Session) -> dict[int, str]:
    """division_id -> "2026 Division 3 North", for reporting."""
    from db.models import League, LeagueSeason, Season

    rows = session.execute(
        select(Division.id, Season.year, League.name, Division.name)
        .join(LeagueSeason, LeagueSeason.id == Division.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .join(Season, Season.id == LeagueSeason.season_id)
    ).all()
    return {row[0]: f"{row[1]} {row[2]} {row[3]}" for row in rows}


__all__ = [
    "DEFAULT_RIDGE_LAMBDA",
    "DivisionOffset",
    "OffsetFit",
    "Stint",
    "compute_division_offsets",
    "division_labels",
    "fit_division_offsets",
    "load_stints",
]
