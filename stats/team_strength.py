"""Bradley-Terry team ratings, fitted per league-season.

**The problem.** A win-loss record is not comparable between two teams even
inside one division, because the schedules are unbalanced. In 2026's
Division 3 Central, Milton Keynes Bucks played the bottom-placed East London
Latin Boys six times and second-placed Cambridge Sovereigns only twice; the
18-0 and Cambridge's 10-4 were built against materially different
opposition. This module estimates each team's strength from who they
actually played, and reports the schedule difficulty separately.

**Why Bradley-Terry on outcomes, and not run margin.** A margin-based model
(Massey ratings, run differential) would normally carry more information per
game than a bare win. Not here, for two measured reasons. Margins in this
league are enormous and dominated by blowouts — median 6 runs, 33% of games
decided by 10 or more, largest 38 — so a least-squares fit on margin would be
driven by how badly the worst teams lose rather than by who beats whom. Worse,
those margins are *censored*: games run 7 innings under mercy rules, so a
rout stops early and the final margin records when the game was halted rather
than how one-sided it was. Fitting censored, blowout-heavy margins as if they
were a clean continuous signal would produce confident and wrong ratings, so
only the outcome is used.

**Why the fit is penalised.** Milton Keynes went 18-0. Unpenalised
Bradley-Terry has no finite maximum-likelihood estimate for an undefeated
team — its rating diverges to infinity, which is both numerically useless and
a bad description of an 18-game sample. An L2 penalty pulls ratings toward
the division average, exactly like the empirical-Bayes shrinkage already
applied to wOBA and FIP in stats/shrinkage.py, and its strength is chosen by
cross-validation on this league-season's own games rather than being
hardcoded.

**Scope of comparability.** Divisions play no regular-season games against
one another, so the data contains nothing that fixes their relative level.
Each division's ratings are therefore centred on its own mean and are
comparable *only within that division*. Reading one division's rating
against another's would assert they are equally strong, which is the open
question rather than an answer to it.

The core fit is DB-free (plain dataclasses in, plain results out) so it can
be tested without a database, following stats/lineup.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Division, Game, TeamSeason, TeamStrength
from db.upsert import upsert

# Candidate L2 penalties tried by cross-validation, spanning "barely
# regularised" to "almost everyone is average". Chosen on a log scale
# because what matters is the order of magnitude, not the exact value.
RIDGE_GRID = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)

# Used when a league-season has too few games to choose a penalty honestly.
# Mid-grid: with a handful of games almost everything should sit near the
# division average anyway.
DEFAULT_RIDGE_LAMBDA = 4.0

# Below this many decisive games, cross-validation is choosing between
# noise — every fold would hold out a meaningful share of the evidence.
MIN_GAMES_FOR_CV = 30

# Folds for the penalty search. 5 is the usual default and leaves each
# training fold with 80% of the games.
CV_FOLDS = 5

# Newton-Raphson settings for the penalised fit. Bradley-Terry with an L2
# penalty is strictly convex, so this converges in a handful of steps from
# any start; the cap only guards against a pathological input.
_MAX_NEWTON_STEPS = 100
_CONVERGENCE_TOL = 1e-10


@dataclass(frozen=True)
class GameResult:
    """One decisive or tied game between two teams.

    `home_won` is None for a tie, which contributes half a win to each side —
    the standard Bradley-Terry treatment, and these leagues do record ties.
    """

    home: int
    away: int
    home_won: bool | None


@dataclass
class TeamRating:
    rating: float
    rating_se: float
    sos: float
    expected_win_pct: float
    games: int
    wins: int
    losses: int
    ties: int
    games_remaining: int = 0
    sos_remaining: float | None = None


@dataclass
class StrengthFit:
    ratings: dict[int, TeamRating]
    home_advantage: float
    ridge_lambda: float
    lambda_self_calibrated: bool


def _design(games: list[GameResult], teams: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build the logistic design for a Bradley-Terry fit.

    Each game becomes one row with +1 in the home team's column and -1 in the
    away team's, so the fitted coefficients are the team ratings and their
    difference drives the win probability. Home advantage enters as an
    intercept — a constant +1 on every row — which is why it is estimated
    once for the whole league-season rather than per division, and why it is
    left unpenalised while the ratings are shrunk.

    Ties become two half-weight rows, one win and one loss.
    """
    index = {team: i for i, team in enumerate(teams)}
    rows: list[np.ndarray] = []
    outcomes: list[float] = []
    weights: list[float] = []

    for game in games:
        row = np.zeros(len(teams))
        row[index[game.home]] = 1.0
        row[index[game.away]] = -1.0
        if game.home_won is None:
            for outcome in (1.0, 0.0):
                rows.append(row)
                outcomes.append(outcome)
                weights.append(0.5)
        else:
            rows.append(row)
            outcomes.append(1.0 if game.home_won else 0.0)
            weights.append(1.0)

    if not rows:
        return np.zeros((0, len(teams))), np.zeros(0), np.zeros(0)
    return np.vstack(rows), np.array(outcomes), np.array(weights)


def _fit_penalised(
    X: np.ndarray, y: np.ndarray, w: np.ndarray, ridge_lambda: float
) -> tuple[np.ndarray, float, np.ndarray]:
    """Penalised logistic fit by Newton-Raphson.

    Returns (ratings, home_advantage, covariance). Written out rather than
    delegated to scikit-learn because the covariance matrix is wanted for
    standard errors, and sklearn does not expose one — having the Hessian in
    hand anyway makes the Newton step the simpler of the two options.

    The intercept (home advantage) is deliberately left out of the penalty:
    shrinking it toward zero would bias a real, measured effect (53.5% home
    win rate across the corpus) toward "no home advantage".
    """
    n_teams = X.shape[1]
    # Parameter vector is [ratings..., home_advantage].
    design = np.hstack([X, np.ones((X.shape[0], 1))])
    penalty = np.eye(n_teams + 1) * ridge_lambda
    penalty[-1, -1] = 0.0  # home advantage is unpenalised

    beta = np.zeros(n_teams + 1)
    for _ in range(_MAX_NEWTON_STEPS):
        eta = design @ beta
        p = 1.0 / (1.0 + np.exp(-eta))
        gradient = design.T @ (w * (y - p)) - penalty @ beta
        hessian = (design * (w * p * (1.0 - p))[:, None]).T @ design + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            break
        beta = beta + step
        if np.max(np.abs(step)) < _CONVERGENCE_TOL:
            break

    try:
        covariance = np.linalg.inv(hessian)
    except np.linalg.LinAlgError:
        covariance = np.full((n_teams + 1, n_teams + 1), np.nan)

    return beta[:n_teams], float(beta[-1]), covariance


def _log_loss(X: np.ndarray, y: np.ndarray, w: np.ndarray, beta_teams: np.ndarray, home: float) -> float:
    eta = X @ beta_teams + home
    p = np.clip(1.0 / (1.0 + np.exp(-eta)), 1e-12, 1 - 1e-12)
    return float(-np.sum(w * (y * np.log(p) + (1 - y) * np.log(1 - p))))


def _choose_lambda(
    X: np.ndarray, y: np.ndarray, w: np.ndarray, n_games: int, seed: int = 0
) -> tuple[float, bool]:
    """Pick the L2 penalty by k-fold cross-validated log loss.

    Self-calibrated for the same reason stats/shrinkage.py self-calibrates
    its stabilization point: how much a 6-team division's records should be
    trusted is a property of that competition, not a constant worth
    importing from elsewhere. Falls back to a fixed penalty when there are
    too few games for the folds to mean anything, and reports which happened.
    """
    if n_games < MIN_GAMES_FOR_CV or X.shape[0] < CV_FOLDS:
        return DEFAULT_RIDGE_LAMBDA, False

    rng = np.random.default_rng(seed)
    fold_of = rng.permutation(X.shape[0]) % CV_FOLDS

    best_lambda, best_loss = DEFAULT_RIDGE_LAMBDA, np.inf
    for candidate in RIDGE_GRID:
        total = 0.0
        for fold in range(CV_FOLDS):
            train, test = fold_of != fold, fold_of == fold
            if not test.any() or not train.any():
                continue
            ratings, home, _ = _fit_penalised(X[train], y[train], w[train], candidate)
            total += _log_loss(X[test], y[test], w[test], ratings, home)
        if total < best_loss:
            best_lambda, best_loss = candidate, total

    return best_lambda, True


def fit_team_strength(
    games: list[GameResult],
    groups: dict[int, int] | None = None,
    *,
    remaining: list[tuple[int, int]] | None = None,
    seed: int = 0,
) -> StrengthFit:
    """Fit ratings for every team appearing in `games`.

    `groups` maps team -> division. Ratings are centred within each group,
    since only within-group comparisons are supported (see the module
    docstring). Teams with no group are centred together as one pool.

    `remaining` is the fixtures still to be played, as (team, opponent)
    pairs in both directions. It never influences the ratings — only results
    can do that — but it is what lets a mid-season rating be reported as
    provisional, with the difficulty of the run-in alongside the difficulty
    of the games already played.

    Home advantage is fitted once across all the games passed in, so callers
    should pass a whole league-season at a time rather than one division:
    a 4-team division's games alone cannot estimate it stably.
    """
    teams = sorted({t for g in games for t in (g.home, g.away)})
    if not teams:
        return StrengthFit({}, 0.0, DEFAULT_RIDGE_LAMBDA, False)

    X, y, w = _design(games, teams)
    ridge_lambda, self_calibrated = _choose_lambda(X, y, w, len(games), seed=seed)
    raw_ratings, home_advantage, covariance = _fit_penalised(X, y, w, ridge_lambda)

    index = {team: i for i, team in enumerate(teams)}
    groups = groups or {}
    ratings = dict(zip(teams, raw_ratings))

    # Centre within each division. The penalty already pulls each
    # disconnected division toward zero, but only exactly so for a perfectly
    # balanced schedule; centring makes "0 is a division-average team" true
    # by construction rather than approximately, which is what the app's
    # captions claim.
    by_group: dict[int | None, list[int]] = {}
    for team in teams:
        by_group.setdefault(groups.get(team), []).append(team)
    for members in by_group.values():
        mean = float(np.mean([ratings[t] for t in members]))
        for team in members:
            ratings[team] -= mean

    records = _records(games)
    opponents = _opponents(games)

    upcoming: dict[int, list[int]] = {}
    for team, opponent in remaining or []:
        upcoming.setdefault(team, []).append(opponent)

    out: dict[int, TeamRating] = {}
    for team in teams:
        variance = covariance[index[team], index[team]]
        wins, losses, ties = records[team]
        division = by_group[groups.get(team)]
        left = [o for o in upcoming.get(team, []) if o in ratings]
        out[team] = TeamRating(
            rating=float(ratings[team]),
            rating_se=float(np.sqrt(variance)) if np.isfinite(variance) and variance >= 0 else float("nan"),
            sos=_strength_of_schedule(team, ratings, opponents[team], division),
            expected_win_pct=float(1.0 / (1.0 + np.exp(-ratings[team]))),
            games=wins + losses + ties,
            wins=wins,
            losses=losses,
            ties=ties,
            games_remaining=len(upcoming.get(team, [])),
            sos_remaining=(
                _strength_of_schedule(team, ratings, left, division) if left else None
            ),
        )

    return StrengthFit(out, home_advantage, ridge_lambda, self_calibrated)


def _strength_of_schedule(
    team: int, ratings: dict[int, float], faced: list[int], division: list[int]
) -> float:
    """How much harder the schedule actually played was than a balanced one.

    The obvious definition — the mean rating of the opponents faced — has a
    bias that matters here. A team never plays itself, so in a perfectly
    balanced round robin the strongest team's average opponent is
    automatically the weakest and the bottom team's is the strongest, purely
    from self-exclusion. Reporting that as "strength of schedule" would say
    every good team had an easy ride, which is precisely the confound this
    number exists to remove.

    So the baseline is the mean rating of the division's *other* teams —
    what a balanced schedule would have handed this team — and the result is
    the difference. A balanced round robin gives exactly zero for everyone,
    however lopsided the division's talent; a team that played the weakest
    opponents more often than the rest gets a negative value.
    """
    if not faced:
        return 0.0
    others = [t for t in division if t != team]
    if not others:
        return 0.0
    actual = float(np.mean([ratings[opp] for opp in faced]))
    balanced = float(np.mean([ratings[t] for t in others]))
    return actual - balanced


def _records(games: list[GameResult]) -> dict[int, tuple[int, int, int]]:
    records: dict[int, list[int]] = {}
    for game in games:
        for team in (game.home, game.away):
            records.setdefault(team, [0, 0, 0])
        if game.home_won is None:
            records[game.home][2] += 1
            records[game.away][2] += 1
        elif game.home_won:
            records[game.home][0] += 1
            records[game.away][1] += 1
        else:
            records[game.away][0] += 1
            records[game.home][1] += 1
    return {team: tuple(counts) for team, counts in records.items()}


def _opponents(games: list[GameResult]) -> dict[int, list[int]]:
    """Every opponent faced, repeated once per meeting — so that playing a
    weak team six times and a strong one twice is reflected in the mean."""
    opponents: dict[int, list[int]] = {}
    for game in games:
        opponents.setdefault(game.home, []).append(game.away)
        opponents.setdefault(game.away, []).append(game.home)
    return opponents


# --------------------------------------------------------------------------
# DB layer
# --------------------------------------------------------------------------


def compute_team_strength(session: Session, league_season_id: int) -> int:
    """Fit and store ratings for one league_season. Returns rows written.

    Only regular-season games inside a division are used. Playoffs are
    excluded because they are a different competitive context played by a
    selected subset of teams, and cross-division games are excluded here
    because a rating is only interpretable within its division — using them
    would quietly tie two divisions' scales together on the strength of a
    handful of games.
    """
    rows = session.execute(
        select(Game.home_team_season_id, Game.away_team_season_id, Game.home_score, Game.away_score)
        .where(
            Game.league_season_id == league_season_id,
            Game.status == "final",
            Game.phase == "regular",
            Game.division_id.isnot(None),
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
        )
    ).all()

    games = [
        GameResult(
            home=home,
            away=away,
            home_won=None if home_score == away_score else home_score > away_score,
        )
        for home, away, home_score, away_score in rows
    ]
    if not games:
        return 0

    groups = {
        team_season_id: division_id
        for team_season_id, division_id in session.execute(
            select(TeamSeason.id, TeamSeason.division_id).where(
                TeamSeason.league_season_id == league_season_id,
                TeamSeason.division_id.isnot(None),
            )
        )
    }

    # Fixtures still to come. The site publishes the whole season's schedule
    # up front, so mid-season this is known rather than guessed — which is
    # what makes "18-0 through 18 of 24" sayable instead of a bare 18-0.
    # Postponed games are included: they are still owed, just undated.
    upcoming = session.execute(
        select(Game.home_team_season_id, Game.away_team_season_id).where(
            Game.league_season_id == league_season_id,
            Game.status.in_(("scheduled", "postponed")),
            Game.phase == "regular",
            Game.division_id.isnot(None),
        )
    ).all()
    remaining = [(h, a) for h, a in upcoming] + [(a, h) for h, a in upcoming]

    fit = fit_team_strength(games, groups, remaining=remaining)
    for team_season_id, rating in fit.ratings.items():
        upsert(
            session,
            TeamStrength,
            {
                "team_season_id": team_season_id,
                "rating": rating.rating,
                "rating_se": None if np.isnan(rating.rating_se) else rating.rating_se,
                "sos": rating.sos,
                "expected_win_pct": rating.expected_win_pct,
                "games": rating.games,
                "wins": rating.wins,
                "losses": rating.losses,
                "ties": rating.ties,
                "games_remaining": rating.games_remaining,
                "sos_remaining": rating.sos_remaining,
                "home_advantage": fit.home_advantage,
                "ridge_lambda": fit.ridge_lambda,
                "lambda_self_calibrated": fit.lambda_self_calibrated,
            },
            ["team_season_id"],
        )
    session.commit()
    return len(fit.ratings)


def division_ids_for(session: Session, league_season_id: int) -> list[int]:
    """Divisions in this league_season, for callers that need to iterate."""
    return list(
        session.execute(
            select(Division.id).where(Division.league_season_id == league_season_id)
        ).scalars()
    )
