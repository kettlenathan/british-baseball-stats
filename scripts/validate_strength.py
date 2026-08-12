"""Does the Bradley-Terry rating actually predict better than the record?

A rating model is only worth showing if it beats the thing it replaces. This
holds out games, fits on the rest, and scores the predictions against two
baselines that cost nothing:

* **coin flip** — 50% every time, the floor any model must clear.
* **home team** — always predict the home side, using the corpus-wide home
  win rate as the probability. This is the real baseline to beat, because it
  needs no fitting at all.
* **win percentage** — rank teams by their record in the training games and
  convert the difference to a probability. This is what the app showed
  before, and the model has to beat it to justify its existence.

Scored by log loss (lower is better), which punishes confident mistakes, and
by plain accuracy. Only regular-season intra-division games are used, since
those are the only games the rating is fitted on.

Results are also split by how lopsided the schedule was, because that is
where the rating is *supposed* to earn its keep and the aggregate number
hides it. Measured on the current corpus: the rating beats win percentage by
0.7% overall, which splits into +0.003 log loss where schedules are balanced
against +0.011 (2.2% relative) where they are skewed — roughly a
three-and-a-half-fold difference. That is the honest case for the model: not
that it predicts better on average, but that it costs nothing where the
record is already a fair comparison and corrects it where it is not.

Both also beat the unfitted baselines by a wide margin (0.52 against 0.69),
so the ratings are clearly capturing real signal; the narrow margin is
against win percentage specifically, because most schedules in this league
are close enough to balanced that the record is already a decent estimate.

Worth knowing if these numbers are ever compared against an older run: they
moved when Game.result_type landed. Recovering the 588 forfeit and
result-only games added 554 scorable games here *and* cut the genuinely
skewed bucket from 1,210 games to 569, because a good deal of the apparent
schedule imbalance had been missing results rather than an uneven draw.

Usage:
    uv run python -m scripts.validate_strength
    uv run python -m scripts.validate_strength --folds 10 --seed 1
"""

import argparse
import math
from collections import defaultdict

import numpy as np
from sqlalchemy import select

from db.engine import get_session
from db.models import Game, League, LeagueSeason, Season, TeamSeason
from stats.team_strength import GameResult, fit_team_strength

_EPS = 1e-12

# A team whose |SOS| exceeds this played a materially different schedule from
# the one a balanced round robin would have given it. Set where the corpus
# separates cleanly into "roughly balanced" and "genuinely lopsided" rather
# than at a value with any theoretical significance.
SKEW_THRESHOLD = 0.15
SKEWED = "skewed"
BALANCED = "balanced"


def _log_loss(predictions: list[tuple[float, float]]) -> float:
    """predictions: (probability home wins, actual outcome as 0/0.5/1)."""
    if not predictions:
        return float("nan")
    total = 0.0
    for p, actual in predictions:
        p = min(max(p, _EPS), 1 - _EPS)
        total += -(actual * math.log(p) + (1 - actual) * math.log(1 - p))
    return total / len(predictions)


def _accuracy(predictions: list[tuple[float, float]]) -> float:
    if not predictions:
        return float("nan")
    hits = 0.0
    for p, actual in predictions:
        if actual == 0.5:
            hits += 0.5
        elif (p > 0.5) == (actual == 1.0):
            hits += 1.0
    return hits / len(predictions)


def _win_pct_baseline(train: list[GameResult]) -> dict[int, float]:
    record: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for g in train:
        result = 0.5 if g.home_won is None else float(g.home_won)
        record[g.home][0] += result
        record[g.home][1] += 1
        record[g.away][0] += 1 - result
        record[g.away][1] += 1
    # Add one win and one loss to every team, so a team with no training
    # games (or a perfect record) still yields a usable probability rather
    # than a 0 or 1 the log loss would treat as infinitely wrong.
    return {t: (w + 1) / (n + 2) for t, (w, n) in record.items()}


def validate(folds: int = 5, seed: int = 0) -> dict[str, dict[str, float]]:
    session = get_session()
    try:
        league_seasons = session.execute(
            select(LeagueSeason.id, League.code, Season.year)
            .join(League, League.id == LeagueSeason.league_id)
            .join(Season, Season.id == LeagueSeason.season_id)
            .order_by(Season.year, League.code)
        ).all()

        results: dict[str, list[tuple[float, float]]] = {
            "bradley_terry": [],
            "win_pct": [],
            "home_team": [],
            "coin_flip": [],
        }
        # Same predictions, split by whether either side had a lopsided draw.
        by_skew: dict[str, dict[str, list[tuple[float, float]]]] = {
            SKEWED: {"bradley_terry": [], "win_pct": []},
            BALANCED: {"bradley_terry": [], "win_pct": []},
        }

        # The corpus-wide home win rate, used by the home-team baseline. Taken
        # from the same games being scored, which flatters that baseline
        # slightly — deliberately, so beating it means something.
        rng = np.random.default_rng(seed)

        for league_season_id, code, year in league_seasons:
            rows = session.execute(
                select(
                    Game.home_team_season_id,
                    Game.away_team_season_id,
                    Game.home_score,
                    Game.away_score,
                ).where(
                    Game.league_season_id == league_season_id,
                    Game.status == "final",
                    Game.phase == "regular",
                    Game.division_id.isnot(None),
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            ).all()
            games = [
                GameResult(h, a, None if hs == as_ else hs > as_)
                for h, a, hs, as_ in rows
            ]
            if len(games) < folds * 4:
                continue

            groups = {
                ts_id: div
                for ts_id, div in session.execute(
                    select(TeamSeason.id, TeamSeason.division_id).where(
                        TeamSeason.league_season_id == league_season_id,
                        TeamSeason.division_id.isnot(None),
                    )
                )
            }

            # Schedule skew is measured on the full season and used only to
            # *bucket* games, never to predict them — the predictions
            # themselves still come from the training folds alone.
            full_season = fit_team_strength(games, groups)

            assignment = rng.permutation(len(games)) % folds
            for fold in range(folds):
                train = [g for i, g in enumerate(games) if assignment[i] != fold]
                test = [g for i, g in enumerate(games) if assignment[i] == fold]
                if not train or not test:
                    continue

                fit = fit_team_strength(train, groups)
                win_pct = _win_pct_baseline(train)
                home_rate = sum(
                    0.5 if g.home_won is None else float(g.home_won) for g in train
                ) / len(train)

                for g in test:
                    actual = 0.5 if g.home_won is None else float(g.home_won)

                    # A team held out entirely has no rating; skip rather than
                    # score a guess, so every method sees the same games.
                    if g.home not in fit.ratings or g.away not in fit.ratings:
                        continue

                    diff = fit.ratings[g.home].rating - fit.ratings[g.away].rating
                    p_bt = 1 / (1 + math.exp(-(diff + fit.home_advantage)))
                    results["bradley_terry"].append((p_bt, actual))

                    # Convert the win-percentage gap to a probability on the
                    # same logistic scale, so the comparison is like for like.
                    gap = _logit(win_pct.get(g.home, 0.5)) - _logit(win_pct.get(g.away, 0.5))
                    results["win_pct"].append((1 / (1 + math.exp(-gap)), actual))

                    results["home_team"].append((home_rate, actual))
                    results["coin_flip"].append((0.5, actual))

                    skew = max(
                        abs(full_season.ratings[g.home].sos),
                        abs(full_season.ratings[g.away].sos),
                    )
                    bucket = by_skew[SKEWED if skew > SKEW_THRESHOLD else BALANCED]
                    bucket["bradley_terry"].append((p_bt, actual))
                    bucket["win_pct"].append((1 / (1 + math.exp(-gap)), actual))

        scored = {
            name: {
                "log_loss": _log_loss(preds),
                "accuracy": _accuracy(preds),
                "n": len(preds),
            }
            for name, preds in results.items()
        }
        for bucket_name, methods in by_skew.items():
            for method, preds in methods.items():
                scored[f"{bucket_name}/{method}"] = {
                    "log_loss": _log_loss(preds),
                    "accuracy": _accuracy(preds),
                    "n": len(preds),
                }
        return scored
    finally:
        session.close()


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    scores = validate(folds=args.folds, seed=args.seed)
    print(f"{args.folds}-fold cross-validation on held-out intra-division regular-season games\n")
    print(f"{'method':16s} {'log loss':>9s} {'accuracy':>9s} {'games':>7s}")
    for name in ("bradley_terry", "win_pct", "home_team", "coin_flip"):
        s = scores[name]
        print(f"{name:16s} {s['log_loss']:9.4f} {s['accuracy']:9.3f} {s['n']:7d}")

    bt, wp = scores["bradley_terry"], scores["win_pct"]
    delta = wp["log_loss"] - bt["log_loss"]
    verdict = "better than" if delta > 0 else "WORSE than"
    print(
        f"\nBradley-Terry is {verdict} the win-percentage baseline "
        f"by {delta:.4f} log loss ({abs(delta) / wp['log_loss']:.1%})."
    )

    print("\nSplit by how lopsided the schedule was:")
    print(f"{'bucket':10s} {'BT loss':>9s} {'win% loss':>10s} {'gain':>8s} {'games':>7s}")
    for bucket in (BALANCED, SKEWED):
        b, w = scores[f"{bucket}/bradley_terry"], scores[f"{bucket}/win_pct"]
        print(
            f"{bucket:10s} {b['log_loss']:9.4f} {w['log_loss']:10.4f} "
            f"{w['log_loss'] - b['log_loss']:+8.4f} {b['n']:7d}"
        )
    # Plain ASCII: this prints to a Windows console, where an em dash in a
    # cp1252 code page comes out as a replacement character.
    print(
        "\nThe rating is not meant to predict better on average. It is meant to cost\n"
        "nothing where the record is already a fair comparison, and to correct it where\n"
        f"the draw was uneven (|SOS| > {SKEW_THRESHOLD})."
    )


if __name__ == "__main__":
    main()
