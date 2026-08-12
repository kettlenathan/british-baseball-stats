"""Should a lightly-used hitter be projected as league average?

`stats/shrinkage.py` shrinks each batter's season wOBA toward a prior, and
that prior used to be the flat league mean — which says an unknown hitter is
an average hitter. Playing time in this league is strongly
performance-selected, so that is false, and this harness measures by how
much.

The catch that makes a harness necessary: the *contemporaneous* relationship
(observed wOBA against season PA, fitted across the corpus) conflates two
different things.

* Genuinely weaker hitters get picked less. This is talent, and it should
  carry into a projection.
* A hitter who starts 0-for-6 stops being picked, freezing their season line
  at its low point. This is within-season attrition. It is real, but it is
  about a line that already happened, not about how they will hit next
  weekend — and projecting it forward would double-count noise the shrinkage
  layer is already handling.

Only the first belongs in a prior, so the fitted effect is damped by
`PLAYING_TIME_PRIOR_DAMPING`, and this script is what sets that constant.

Design
------
Split each league-season chronologically at a cutoff (default: 60% of the way
through its games), then predict each hitter's wOBA over the games *after*
the cutoff from their line *before* it. Nothing downstream of the cutoff is
used for anything — league mean, stabilization point k, and the playing-time
curve are all re-fitted on the seen portion alone — so the held-out half is
genuinely held out.

This mirrors how the scouting report actually uses the estimate: mid-season,
from a partial line, to project the games still to come. A cross-sectional
fit on completed seasons could not answer that question, because a completed
low-PA season has already been truncated by the attrition above.

Predictors compared, all scored by PA-weighted mean squared error against the
held-out wOBA (weighted so a hitter with 40 unseen PA counts for more than one
with 5):

* **league mean** — ignore the hitter entirely. The floor.
* **raw observed** — their seen line at face value, no shrinkage. The
  opposite failure mode, and terrible at low PA, which is the whole reason
  the shrinkage layer exists.
* **flat prior** — shrink toward the league mean. The previous behaviour.
* **PA-aware prior, damping d** — shrink toward `lg + d * f(PA)`, swept over
  d so the choice of damping is read off a curve rather than picked by eye.

Results are also broken out by seen-PA bucket, because the aggregate is
dominated by regulars for whom the two priors barely differ — the lightly-used
bucket is where the change is supposed to do anything at all, and reporting
only the pooled number would hide both the gain and any harm.

What it currently says, over 6,654 held-out hitter-remainders in 25
league-seasons (RMSE against held-out wOBA, lower better):

    league mean   .1394   -3.4%   ignoring the hitter entirely
    raw observed  .1620  -20.2%   trusting a partial line at face value
    flat prior    .1348    ref    the previous behaviour
    damping 0.4   .1339   +0.7%   <- chosen
    damping 1.0   .1354   -0.4%   the undamped fit, worse than doing nothing

So the shrinkage layer is doing most of the work (flat prior over raw
observed is a 20% gain), the playing-time prior adds a real but modest 0.7%
on top, and the undamped version is actively harmful.

Two things to know before re-tuning:

* **Damping and MIN_PA_FOR_PRIOR_CURVE interact strongly, so sweep them
  together.** The curve is fitted on end-of-season PA, where 3 PA means a
  hitter who was available all year and barely used; read mid-season the same
  3 PA often means one who hasn't debuted yet. With the clamp at 3 the bottom
  of the curve is steep enough that no damping above 0.2 helps lightly-used
  hitters at all; at 12 the pooled-optimal 0.4 helps them too.
* **0.4 is not a clean sweep and shouldn't be described as one.** It improves
  the 1-9, 20-39 and 40+ buckets and is 0.15% worse than the flat prior in
  the 10-19 bucket. Damping 0.2 improves all four but gives up half the gain
  on regulars, and ties 0.4 exactly (.1567) in the 1-9 bucket that motivates
  the whole thing.

A sanity check is built into the output: "damping 0.0" must score identically
to "flat prior", since it reduces to exactly that.

Usage:
    uv run python -m scripts.validate_low_pa_prior
    uv run python -m scripts.validate_low_pa_prior --cutoff 0.5 --min-unseen-pa 10
"""

import argparse
from collections import defaultdict

from sqlalchemy import select

from db.engine import get_session
from db.models import (
    BattingGameLine,
    Game,
    League,
    LeagueSeason,
    PlayerSeason,
    Season,
    TeamSeason,
)
from stats import constants
from stats.shrinkage import (
    PLAYING_TIME_PRIOR_DAMPING,
    _batting_component_variance,
    estimate_batting_stabilization_pa,
    fit_playing_time_prior,
    shrink_rate,
)

# Damping factors swept. 0.0 reproduces the flat prior exactly and 1.0 uses
# the raw contemporaneous fit, so the sweep brackets both of the positions
# this harness is choosing between.
DAMPING_GRID = (0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0)

SEEN_PA_BUCKETS = ((1, 9), (10, 19), (20, 39), (40, 10_000))

_WOBA_FIELDS = ("ab", "h", "doubles", "triples", "hr", "bb", "ibb", "hbp", "sf", "pa")


def _woba(totals: dict) -> float | None:
    """wOBA from a dict of counting stats — the same formula as
    stats/advanced_stats.py:woba, which wants an ORM row rather than a dict."""
    denom = totals["ab"] + totals["bb"] - totals["ibb"] + totals["sf"] + totals["hbp"]
    if not denom:
        return None
    singles = totals["h"] - totals["doubles"] - totals["triples"] - totals["hr"]
    return (
        constants.WOBA_WEIGHT_UBB * (totals["bb"] - totals["ibb"])
        + constants.WOBA_WEIGHT_HBP * totals["hbp"]
        + constants.WOBA_WEIGHT_1B * singles
        + constants.WOBA_WEIGHT_2B * totals["doubles"]
        + constants.WOBA_WEIGHT_3B * totals["triples"]
        + constants.WOBA_WEIGHT_HR * totals["hr"]
    ) / denom


def _blank() -> dict:
    return dict.fromkeys(_WOBA_FIELDS, 0)


def _add(into: dict, line: BattingGameLine) -> None:
    for f in _WOBA_FIELDS:
        into[f] += getattr(line, f) or 0


def _pool(totals: list[dict]) -> dict:
    pooled = _blank()
    for t in totals:
        for f in _WOBA_FIELDS:
            pooled[f] += t[f]
    return pooled


def _split_league_season(session, league_season_id: int, cutoff: float) -> tuple[dict, dict] | None:
    """(seen totals by player_season_id, unseen totals by player_season_id),
    split at the cutoff quantile of this league-season's played games. Only
    `result_type == "played"` games carry a box score at all, so forfeits
    can't contribute a phantom line either way."""
    game_dates = (
        session.execute(
            select(Game.id, Game.game_date)
            .join(TeamSeason, TeamSeason.id == Game.home_team_season_id)
            .where(
                TeamSeason.league_season_id == league_season_id,
                Game.status == "final",
                Game.result_type == "played",
                Game.game_date.is_not(None),
            )
            .order_by(Game.game_date)
        )
        .all()
    )
    if len(game_dates) < 20:
        return None
    cut_date = game_dates[int(len(game_dates) * cutoff)][1]

    lines = (
        session.execute(
            select(BattingGameLine, Game.game_date)
            .join(Game, Game.id == BattingGameLine.game_id)
            .join(PlayerSeason, PlayerSeason.id == BattingGameLine.player_season_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(
                TeamSeason.league_season_id == league_season_id,
                Game.status == "final",
                Game.result_type == "played",
                Game.game_date.is_not(None),
            )
        )
        .all()
    )
    seen: dict[int, dict] = defaultdict(_blank)
    unseen: dict[int, dict] = defaultdict(_blank)
    for line, game_date in lines:
        target = seen if game_date < cut_date else unseen
        _add(target[line.player_season_id], line)
    return dict(seen), dict(unseen)


def _evaluate(session, cutoff: float, min_unseen_pa: int) -> tuple[dict, dict]:
    """Squared-error totals per predictor, pooled and by seen-PA bucket."""
    league_seasons = session.execute(
        select(LeagueSeason.id, League.code, Season.year)
        .join(League, League.id == LeagueSeason.league_id)
        .join(Season, Season.id == LeagueSeason.season_id)
        .order_by(Season.year, League.code)
    ).all()

    predictors = ["league mean", "raw observed", "flat prior"] + [f"damping {d:.1f}" for d in DAMPING_GRID]
    pooled = {p: [0.0, 0.0] for p in predictors}  # [weighted sq error, weight]
    by_bucket = {b: {p: [0.0, 0.0] for p in predictors} for b in SEEN_PA_BUCKETS}
    n_scored = 0
    n_league_seasons = 0

    for ls_id, _code, _year in league_seasons:
        split = _split_league_season(session, ls_id, cutoff)
        if split is None:
            continue
        seen, unseen = split

        seen_rows = [(t["pa"], _woba(t)) for t in seen.values() if t["pa"]]
        seen_pool = _pool(list(seen.values()))
        lg_woba = _woba(seen_pool)
        if lg_woba is None or len(seen_rows) < 20:
            continue
        # k and the prior curve are both re-fitted on the seen portion only —
        # taking either from the full season would leak the held-out games.
        v_e = _batting_component_variance(seen_pool, seen_pool["pa"])
        k, _ = estimate_batting_stabilization_pa(seen_rows, v_e)
        priors = {d: fit_playing_time_prior(seen_rows, lg_woba, damping=d) for d in DAMPING_GRID}

        scored_here = 0
        for ps_id, unseen_totals in unseen.items():
            if unseen_totals["pa"] < min_unseen_pa:
                continue
            actual = _woba(unseen_totals)
            if actual is None:
                continue
            seen_totals = seen.get(ps_id)
            seen_pa = seen_totals["pa"] if seen_totals else 0
            observed = _woba(seen_totals) if seen_totals else None
            weight = float(unseen_totals["pa"])

            predictions = {
                "league mean": lg_woba,
                "raw observed": observed if observed is not None else lg_woba,
                "flat prior": shrink_rate(observed, seen_pa, lg_woba, k),
            }
            for d in DAMPING_GRID:
                prior_mean = priors[d].mean_for(lg_woba, seen_pa)
                predictions[f"damping {d:.1f}"] = shrink_rate(observed, seen_pa, prior_mean, k)

            bucket = next((b for b in SEEN_PA_BUCKETS if b[0] <= max(seen_pa, 1) <= b[1]), None)
            for name, pred in predictions.items():
                err = weight * (pred - actual) ** 2
                pooled[name][0] += err
                pooled[name][1] += weight
                if bucket:
                    by_bucket[bucket][name][0] += err
                    by_bucket[bucket][name][1] += weight
            scored_here += 1

        n_scored += scored_here
        if scored_here:
            n_league_seasons += 1

    return {
        "pooled": pooled,
        "by_bucket": by_bucket,
        "n_scored": n_scored,
        "n_league_seasons": n_league_seasons,
    }


def _rmse(cell: list[float]) -> float | None:
    return (cell[0] / cell[1]) ** 0.5 if cell[1] else None


def _report(results: dict) -> None:
    pooled = results["pooled"]
    print(
        f"Scored {results['n_scored']} hitter-remainders across "
        f"{results['n_league_seasons']} league-seasons.\n"
    )

    baseline = _rmse(pooled["flat prior"])
    print(f"{'predictor':>16} {'RMSE':>9} {'vs flat prior':>15}")
    for name, cell in pooled.items():
        r = _rmse(cell)
        if r is None:
            continue
        delta = (baseline - r) / baseline * 100 if baseline else 0.0
        marker = "  <- previous behaviour" if name == "flat prior" else ""
        print(f"{name:>16} {r:>9.4f} {delta:>+14.2f}%{marker}")

    shown = ["flat prior"] + [f"damping {d:.1f}" for d in DAMPING_GRID if d > 0]
    print("\nBy seen PA at the cutoff (where the change is meant to matter).")
    print("The chosen damping should improve on the flat prior in every row, not just on average:")
    print(f"{'seen PA':>10} {'held-out PA':>12} " + " ".join(f"{n.replace('damping ', 'd='):>9}" for n in shown))
    for bucket, cells in results["by_bucket"].items():
        if not cells["flat prior"][1]:
            continue
        label = f"{bucket[0]}-{bucket[1]}" if bucket[1] < 10_000 else f"{bucket[0]}+"
        row = f"{label:>10} {cells['flat prior'][1]:>12.0f} "
        for name in shown:
            r = _rmse(cells[name])
            row += f"{r:>9.4f} " if r is not None else f"{'-':>9} "
        print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--cutoff",
        type=float,
        default=0.6,
        help="Fraction of each league-season's games used as the seen portion (default 0.6).",
    )
    parser.add_argument(
        "--min-unseen-pa",
        type=int,
        default=5,
        help="Minimum held-out PA for a hitter to be scored (default 5).",
    )
    args = parser.parse_args()

    session = get_session()
    try:
        results = _evaluate(session, args.cutoff, args.min_unseen_pa)
    finally:
        session.close()

    _report(results)
    print(
        f"\nCurrent PLAYING_TIME_PRIOR_DAMPING = {PLAYING_TIME_PRIOR_DAMPING}. "
        "Pick the damping with the lowest RMSE in the lightly-used buckets that does no "
        "harm to the regulars; update stats/shrinkage.py if this run disagrees."
    )


if __name__ == "__main__":
    main()
