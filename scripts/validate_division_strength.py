"""The go/no-go test for cross-division strength offsets.

Teams never play outside their own division in the regular season, so the
claim that division A is stronger than division B cannot be checked against
the games that produced it. It *can* be checked against the games it never
saw: 336 cross-division games exist in the corpus, played in the playoffs
and in a handful of earlier seasons that crossed divisions mid-season.

This holds those out and asks whether knowing the player-derived offsets
predicts them better than assuming the divisions are equal — which is
exactly what the app currently assumes by centring every division's ratings
on its own mean.

    equal_divisions   rating_home - rating_away + home_advantage
    bridge_offsets    ... + alpha * (offset_home - offset_away)
    offsets_only      alpha * (offset_home - offset_away) + home_advantage
    home_team / coin_flip

Nothing that produces a prediction has seen a cross-division game. The
offsets come only from intra-division play (stats/division_strength.py loads
stints filtered to Game.division_id IS NOT NULL, and cross-division games
carry no division), and the team ratings likewise. The single scalar
`alpha` — the conversion from "wOBA points easier to bat in" to log-odds of
winning, which batting data alone cannot supply — is fitted on the training
folds of the cross-division games and never on the fold being scored.

Its fitted **sign is itself a test**. A positive division offset means the
division was an easier place to bat, which should mean weaker pitching and
so weaker teams; alpha should therefore come out *negative*. If it comes out
positive, or indistinguishable from zero, the premise has failed and the
offsets should not be shipped as a strength claim.

Usage:
    uv run python -m scripts.validate_division_strength
    uv run python -m scripts.validate_division_strength --folds 8 --seed 3
"""

import argparse
import math

import numpy as np
from sqlalchemy import select

from db.engine import get_session
from db.models import Game, TeamSeason, TeamStrength
from stats.division_strength import compute_division_offsets

_EPS = 1e-12


def _log_loss(preds: list[tuple[float, float]]) -> float:
    if not preds:
        return float("nan")
    return float(
        -np.mean(
            [
                a * math.log(min(max(p, _EPS), 1 - _EPS))
                + (1 - a) * math.log(1 - min(max(p, _EPS), 1 - _EPS))
                for p, a in preds
            ]
        )
    )


def _accuracy(preds: list[tuple[float, float]]) -> float:
    if not preds:
        return float("nan")
    hits = 0.0
    for p, a in preds:
        if a == 0.5:
            hits += 0.5
        elif (p > 0.5) == (a == 1.0):
            hits += 1.0
    return hits / len(preds)


def _fit_alpha(rows: list[tuple[float, float, float]]) -> float:
    """One-parameter logistic fit of alpha on (base, gap, outcome) rows.

    `base` is the rating difference plus home advantage, held fixed as an
    offset; only the coefficient on the division gap is free. Newton steps on
    a scalar, which is trivially convex.
    """
    alpha = 0.0
    for _ in range(50):
        gradient = 0.0
        hessian = 0.0
        for base, gap, outcome in rows:
            p = 1.0 / (1.0 + math.exp(-(base + alpha * gap)))
            gradient += gap * (outcome - p)
            hessian += gap * gap * p * (1 - p)
        if hessian <= 1e-12:
            break
        step = gradient / hessian
        alpha += step
        if abs(step) < 1e-10:
            break
    return alpha


def validate(folds: int = 5, seed: int = 0) -> dict:
    session = get_session()
    try:
        fit = compute_division_offsets(session)
        offsets = {d: o.offset for d, o in fit.offsets.items()}

        strength = {
            row.team_season_id: row
            for row in session.execute(select(TeamStrength)).scalars()
        }
        divisions = {
            ts_id: div
            for ts_id, div in session.execute(
                select(TeamSeason.id, TeamSeason.division_id).where(
                    TeamSeason.division_id.isnot(None)
                )
            )
        }

        home_ts = TeamSeason.__table__.alias("h")
        away_ts = TeamSeason.__table__.alias("a")
        games = session.execute(
            select(
                Game.home_team_season_id,
                Game.away_team_season_id,
                Game.home_score,
                Game.away_score,
            )
            .join(home_ts, home_ts.c.id == Game.home_team_season_id)
            .join(away_ts, away_ts.c.id == Game.away_team_season_id)
            .where(
                Game.status == "final",
                Game.result_type == "played",
                home_ts.c.division_id.isnot(None),
                away_ts.c.division_id.isnot(None),
                home_ts.c.division_id != away_ts.c.division_id,
                Game.home_score.isnot(None),
                Game.away_score.isnot(None),
            )
        ).all()

        rows = []
        for home, away, hs, as_ in games:
            if home not in strength or away not in strength:
                continue
            if home not in divisions or away not in divisions:
                continue
            base = (
                (strength[home].rating or 0.0)
                - (strength[away].rating or 0.0)
                + (strength[home].home_advantage or 0.0)
            )
            gap = offsets.get(divisions[home], 0.0) - offsets.get(divisions[away], 0.0)
            outcome = 0.5 if hs == as_ else float(hs > as_)
            rows.append((base, gap, outcome))

        if len(rows) < folds * 2:
            return {"error": f"only {len(rows)} usable cross-division games"}

        rng = np.random.default_rng(seed)
        assignment = rng.permutation(len(rows)) % folds
        results = {k: [] for k in ("equal_divisions", "bridge_offsets", "offsets_only", "home_team", "coin_flip")}
        alphas = []

        for fold in range(folds):
            train = [r for i, r in enumerate(rows) if assignment[i] != fold]
            test = [r for i, r in enumerate(rows) if assignment[i] == fold]
            if not train or not test:
                continue
            alpha = _fit_alpha(train)
            alphas.append(alpha)
            home_rate = sum(o for _, _, o in train) / len(train)
            alpha_only = _fit_alpha([(0.0, g, o) for _, g, o in train])

            for base, gap, outcome in test:
                results["equal_divisions"].append((1 / (1 + math.exp(-base)), outcome))
                results["bridge_offsets"].append(
                    (1 / (1 + math.exp(-(base + alpha * gap))), outcome)
                )
                results["offsets_only"].append(
                    (1 / (1 + math.exp(-(alpha_only * gap))), outcome)
                )
                results["home_team"].append((home_rate, outcome))
                results["coin_flip"].append((0.5, outcome))

        return {
            "n": len(rows),
            "alphas": alphas,
            "identifying_players": fit.identifying_players,
            "scores": {
                name: {
                    "log_loss": _log_loss(preds),
                    "accuracy": _accuracy(preds),
                }
                for name, preds in results.items()
            },
        }
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    out = validate(folds=args.folds, seed=args.seed)
    if "error" in out:
        raise SystemExit(out["error"])

    print(
        f"{args.folds}-fold cross-validation on {out['n']} held-out cross-division games\n"
        f"(offsets estimated from {out['identifying_players']} multi-division players, "
        "using no cross-division game)\n"
    )
    print(f"{'method':18s} {'log loss':>9s} {'accuracy':>9s}")
    for name in ("bridge_offsets", "equal_divisions", "offsets_only", "home_team", "coin_flip"):
        s = out["scores"][name]
        print(f"{name:18s} {s['log_loss']:9.4f} {s['accuracy']:9.3f}")

    bridge = out["scores"]["bridge_offsets"]["log_loss"]
    equal = out["scores"]["equal_divisions"]["log_loss"]
    delta = equal - bridge
    alphas = out["alphas"]
    mean_alpha = sum(alphas) / len(alphas)

    print(f"\nalpha per fold: {', '.join(f'{a:+.2f}' for a in alphas)}")
    print(f"mean alpha: {mean_alpha:+.2f}")
    print(
        "  (negative is the expected sign: a division that is easier to bat in has\n"
        "   weaker pitching, so its teams should be weaker)"
    )
    verdict = "BEATS" if delta > 0 else "DOES NOT BEAT"
    print(
        f"\nUsing division offsets {verdict} assuming divisions are equal, "
        f"by {delta:+.4f} log loss ({delta / equal:+.1%})."
    )
    sign_ok = mean_alpha < 0
    print(
        "Sign check: "
        + ("PASS - offsets point the way the theory says.\n" if sign_ok else "FAIL - the fitted sign is backwards.\n")
    )


if __name__ == "__main__":
    main()
