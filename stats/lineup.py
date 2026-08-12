"""Batting-order recommendation for the scouting report
(app/pages/9_Scouting_Report.py): turn each available batter into a per-PA
outcome profile, evaluate a batting order's expected runs with an exact
Markov chain over base-out states, and search for the best order.

Like stats/archetypes.py and stats/probable_pitchers.py this is computed at
read time, parameterized by user choices (who's available this week, which
opposing pitcher), so nothing here is materialized. It is also deliberately
DB-free — callers (app/components/data_access.py) pass plain dicts of
counting stats, so the whole module is testable without a database.

Batter profiles
---------------
Each batter becomes per-PA probabilities of {BB, HBP, 1B, 2B, 3B, HR, out}.
Small samples are the norm in this league, so raw rates aren't trusted
directly:

- The walk/HBP rates are shrunk toward the league rate with the same
  empirical-Bayes form as stats/shrinkage.py, using its published fallback
  stabilization point (a fixed k is fine here — this is an input to a
  heuristic search, not a published stat).
- The hit-type rates are shrunk on that same k, then scaled by a single
  factor so the profile's implied wOBA equals the player's **shrunk** wOBA
  from BattingTrueTalent — i.e. the overall quality estimate comes from the
  shrinkage layer, and the season line contributes only how that quality is
  distributed across hit types, itself discounted by how much of it there is.
  Batters with no data at all get the league profile.
- IBB is not modeled separately (it's situational, not a talent), so BB here
  is all walks at the uBB wOBA weight.

Nothing here treats a thin sample as no sample. A hitter with 8 PA is a
hitter with 8 PA of evidence: their shrunk wOBA is anchored to a prior that
already knows lightly-used hitters are below average (stats/shrinkage.py's
playing-time prior), their hit mix pulls their profile a little way from
league shape, and what remains uncertain is carried as an interval rather
than erased. What this module must never do is *claim* more than 8 PA can
support — hence conservative_woba below and the shrunk trait ranks in
slot_rationales, which handle thin evidence by discounting it continuously
rather than by refusing to speak below a threshold.

An optional platoon adjustment re-targets the profile at a shrunk vs-hand
wOBA (see platoon_adjusted_woba) when the opposing starter's throwing hand
is known. Platoon splits stabilize very slowly, so the vs-hand observation
is shrunk toward the batter's own overall shrunk wOBA with a large k.

Run model
---------
Expected runs come from an exact Markov chain over the 24 base-out states
(times the batter due up), advanced one plate appearance at a time until the
inning's probability mass is absorbed at three outs, for 7 innings (the BBF
game length). Runner advancement is deterministic per event — the site's
play-by-play can't support empirically fitted advancement probabilities:

- walk/HBP: forced advances only
- single: batter to 1st, runner on 1st to 2nd, runners on 2nd/3rd score
- double: batter to 2nd, runner on 1st to 3rd, runners on 2nd/3rd score
- triple/HR: everybody scores
- out: one out, runners hold (no sacrifices, no double plays, no steals)

Mercy rules (10 after 5 / 15 after 4) and the 2h15 curfew are deliberately
ignored: they truncate games in ways that are nearly symmetric across
candidate orders, so they don't change which *order* is best, only the
absolute run totals.

Search
------
Expected-runs differences between sensible orders are small (fractions of a
run per game), so the search doesn't need to be exhaustive: seed with a
"The Book"-style heuristic slotting (best hitters up front, on-base ahead of
power), then hill-climb over pairwise swaps with a few seeded random
restarts. The result is deterministic for the same inputs.
"""

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from stats import constants
from stats.shrinkage import FALLBACK_BATTING_STABILIZATION_PA, shrink_rate

# Platoon splits stabilize far more slowly than overall wOBA (Russell
# Carleton-style estimates put platoon-split stabilization at many hundreds
# of PA), so the vs-hand observation gets a much heavier shrink toward the
# batter's own overall talent than the standard batting k.
PLATOON_STABILIZATION_PA = 300.0

DEFAULT_INNINGS = 7

# Scaling the hit-rate block to a target wOBA is clamped so an extreme
# shrunk-vs-observed gap can't produce a degenerate profile (e.g. hit
# probabilities summing past certainty for a tiny sample that's all hits).
_MIN_HIT_SCALE = 0.25
_MAX_HIT_SCALE = 3.0
_MAX_EVENT_PROB = 0.95

_EVENTS = ("bb", "hbp", "single", "double", "triple", "hr", "out")

_EVENT_WEIGHTS = {
    "bb": constants.WOBA_WEIGHT_UBB,
    "hbp": constants.WOBA_WEIGHT_HBP,
    "single": constants.WOBA_WEIGHT_1B,
    "double": constants.WOBA_WEIGHT_2B,
    "triple": constants.WOBA_WEIGHT_3B,
    "hr": constants.WOBA_WEIGHT_HR,
    "out": 0.0,
}


@dataclass(frozen=True)
class BatterProfile:
    name: str
    p_bb: float
    p_hbp: float
    p_single: float
    p_double: float
    p_triple: float
    p_hr: float

    @property
    def p_out(self) -> float:
        return 1.0 - (self.p_bb + self.p_hbp + self.p_single + self.p_double + self.p_triple + self.p_hr)

    @property
    def p_onbase(self) -> float:
        return 1.0 - self.p_out

    @property
    def implied_woba(self) -> float:
        return (
            _EVENT_WEIGHTS["bb"] * self.p_bb
            + _EVENT_WEIGHTS["hbp"] * self.p_hbp
            + _EVENT_WEIGHTS["single"] * self.p_single
            + _EVENT_WEIGHTS["double"] * self.p_double
            + _EVENT_WEIGHTS["triple"] * self.p_triple
            + _EVENT_WEIGHTS["hr"] * self.p_hr
        )

    @property
    def power(self) -> float:
        """Extra-base weight per PA — used only to order power hitters into
        the middle of the heuristic seed and to phrase rationales."""
        return 2 * self.p_double + 3 * self.p_triple + 4 * self.p_hr

    def event_probs(self) -> list[float]:
        return [self.p_bb, self.p_hbp, self.p_single, self.p_double, self.p_triple, self.p_hr, self.p_out]


def league_component_rates(league_totals: dict) -> dict[str, float]:
    """Per-PA event rates for the league as a whole, from a totals dict with
    pa/h/doubles/triples/hr/bb/hbp keys (stats/league_context's
    _league_batting_totals shape)."""
    pa = league_totals.get("pa") or 0
    if not pa:
        # A neutral, roughly amateur-league-shaped fallback so profile
        # construction never divides by zero on an empty league.
        return {"bb": 0.10, "hbp": 0.02, "single": 0.17, "double": 0.05, "triple": 0.01, "hr": 0.01}
    singles = league_totals["h"] - league_totals["doubles"] - league_totals["triples"] - league_totals["hr"]
    return {
        "bb": league_totals["bb"] / pa,
        "hbp": league_totals["hbp"] / pa,
        "single": singles / pa,
        "double": league_totals["doubles"] / pa,
        "triple": league_totals["triples"] / pa,
        "hr": league_totals["hr"] / pa,
    }


def platoon_adjusted_woba(
    overall_woba: float, vs_hand_pa: int, vs_hand_woba: float | None, k: float = PLATOON_STABILIZATION_PA
) -> float:
    """Shrink a batter's observed vs-hand wOBA toward their own overall
    (already-shrunk) wOBA — the platoon sample is treated as weak evidence
    against the strong prior of the player's general ability."""
    adjusted = shrink_rate(vs_hand_woba, vs_hand_pa, overall_woba, k)
    return adjusted if adjusted is not None else overall_woba


def build_profile(
    name: str,
    counts: dict,
    league_rates: dict[str, float],
    target_woba: float | None,
) -> BatterProfile:
    """counts: this player's season pa/h/doubles/triples/hr/bb/hbp (raw
    counting stats). league_rates: league_component_rates() output.
    target_woba: the wOBA the finished profile should imply (normally the
    player's shrunk wOBA, optionally platoon-adjusted); None means
    league-average."""
    pa = counts.get("pa") or 0
    k = FALLBACK_BATTING_STABILIZATION_PA

    p_bb = ((counts.get("bb") or 0) + k * league_rates["bb"]) / (pa + k)
    p_hbp = ((counts.get("hbp") or 0) + k * league_rates["hbp"]) / (pa + k)

    hits = counts.get("h") or 0
    doubles = counts.get("doubles") or 0
    triples = counts.get("triples") or 0
    hr = counts.get("hr") or 0
    singles = hits - doubles - triples - hr
    # The hit-type mix is shrunk on the same k as the walk rates. Without
    # this a hitter whose only two hits were home runs gets a profile that is
    # *entirely* home runs, which then survives the wOBA rescale below as a
    # bizarre all-or-nothing bat: never on base, enormous power. The level of
    # a small sample was already being discounted; its shape was not, which
    # was the same mistake in the opposite direction.
    observed_hit_counts = {"single": singles, "double": doubles, "triple": triples, "hr": hr}
    raw_hit_rates = {
        key: (observed_hit_counts[key] + k * league_rates[key]) / (pa + k) for key in observed_hit_counts
    }

    league_woba = sum(_EVENT_WEIGHTS[e] * league_rates[e] for e in ("bb", "hbp", "single", "double", "triple", "hr"))
    target = target_woba if target_woba is not None else league_woba

    walk_woba = _EVENT_WEIGHTS["bb"] * p_bb + _EVENT_WEIGHTS["hbp"] * p_hbp
    raw_hit_woba = sum(_EVENT_WEIGHTS[e] * raw_hit_rates[e] for e in raw_hit_rates)
    scale = (target - walk_woba) / raw_hit_woba if raw_hit_woba > 0 else 0.0
    scale = min(max(scale, _MIN_HIT_SCALE), _MAX_HIT_SCALE)

    probs = {"bb": p_bb, "hbp": p_hbp, **{e: raw_hit_rates[e] * scale for e in raw_hit_rates}}
    total = sum(probs.values())
    if total > _MAX_EVENT_PROB:
        probs = {e: p * _MAX_EVENT_PROB / total for e, p in probs.items()}

    return BatterProfile(
        name=name,
        p_bb=probs["bb"],
        p_hbp=probs["hbp"],
        p_single=probs["single"],
        p_double=probs["double"],
        p_triple=probs["triple"],
        p_hr=probs["hr"],
    )


# --------------------------------------------------------------------------
# Markov run-expectancy model
# --------------------------------------------------------------------------

# Base-out state encoding: state = outs * 8 + bases, bases a bitmask with
# bit 0 = runner on 1st, bit 1 = 2nd, bit 2 = 3rd. 24 live states; the
# third out absorbs.
_N_STATES = 24
_ABSORBED = -1


def _advance(bases: int, event: str) -> tuple[int, int]:
    """(new bases bitmask, runs scored) for one event from one base state,
    per the deterministic advancement rules in the module docstring."""
    first, second, third = bool(bases & 1), bool(bases & 2), bool(bases & 4)
    if event == "single":
        runs = int(second) + int(third)
        return 1 | (2 if first else 0), runs
    if event == "double":
        runs = int(second) + int(third)
        return 2 | (4 if first else 0), runs
    if event == "triple":
        return 4, int(first) + int(second) + int(third)
    if event == "hr":
        return 0, int(first) + int(second) + int(third) + 1
    raise ValueError(event)


def _walk_advance(bases: int) -> tuple[int, int]:
    """Forced advances on a walk/HBP, written out longhand for clarity."""
    first, second, third = bool(bases & 1), bool(bases & 2), bool(bases & 4)
    runs = 0
    if first and second and third:
        runs = 1
    elif first and second:
        third = True
    elif first:
        second = True
    first = True
    return (1 if first else 0) | (2 if second else 0) | (4 if third else 0), runs


def _build_transitions() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per event: (next_state[24] with _ABSORBED for the third out,
    runs[24])."""
    tables: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for event in _EVENTS:
        next_state = np.zeros(_N_STATES, dtype=np.int64)
        runs = np.zeros(_N_STATES, dtype=np.float64)
        for outs in range(3):
            for bases in range(8):
                s = outs * 8 + bases
                if event == "out":
                    next_state[s] = _ABSORBED if outs == 2 else (outs + 1) * 8 + bases
                    runs[s] = 0.0
                else:
                    if event in ("bb", "hbp"):
                        new_bases, scored = _walk_advance(bases)
                    else:
                        new_bases, scored = _advance(bases, event)
                    next_state[s] = outs * 8 + new_bases
                    runs[s] = scored
        tables[event] = (next_state, runs)
    return tables


_TRANSITIONS = _build_transitions()


@lru_cache(maxsize=256)
def _batter_operators(profile: BatterProfile) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Collapse a batter's event probabilities and the shared per-event
    transition tables into a single per-PA linear operator: a 24x24
    state-transition matrix T (T[s', s] = P(s -> s')), an expected-runs
    vector, and a third-out absorption vector. Cached per profile since the
    hill-climb evaluates the same batters in many different orders."""
    transition = np.zeros((_N_STATES, _N_STATES), dtype=np.float64)
    runs = np.zeros(_N_STATES, dtype=np.float64)
    absorb = np.zeros(_N_STATES, dtype=np.float64)
    for event, prob in zip(_EVENTS, profile.event_probs()):
        next_state, event_runs = _TRANSITIONS[event]
        runs += prob * event_runs
        for s in range(_N_STATES):
            if next_state[s] == _ABSORBED:
                absorb[s] += prob
            else:
                transition[next_state[s], s] += prob
    return transition, runs, absorb

# Residual in-flight probability mass below which an inning is considered
# fully resolved, and a hard cap on plate appearances per inning in case a
# pathological profile (near-zero out rate, already clamped above) converges
# slowly. Mass still live at the cap is dropped — at these thresholds that
# under-counts expected runs by an amount far below the optimizer's epsilon.
_INNING_EPSILON = 1e-9
_MAX_PA_PER_INNING = 40


def expected_runs(profiles: list[BatterProfile], innings: int = DEFAULT_INNINGS) -> float:
    """Exact expected runs per game for this batting order (index 0 leads
    off inning 1), cycling through the order across `innings` innings."""
    n = len(profiles)
    if n == 0:
        return 0.0
    operators = [_batter_operators(p) for p in profiles]
    transitions = np.stack([op[0] for op in operators])  # (n, 24, 24)
    run_vectors = np.stack([op[1] for op in operators])  # (n, 24)
    absorb_vectors = np.stack([op[2] for op in operators])  # (n, 24)

    total_runs = 0.0
    leadoff = np.zeros(n, dtype=np.float64)
    leadoff[0] = 1.0

    for _ in range(innings):
        # dist[s, j] = P(state s with batter j due up); columns track who's
        # at the plate, so applying batter j's operator to column j and then
        # rolling one column forward advances the game one PA for everyone.
        dist = np.zeros((_N_STATES, n), dtype=np.float64)
        dist[0, :] = leadoff
        next_leadoff = np.zeros(n, dtype=np.float64)

        for _ in range(_MAX_PA_PER_INNING):
            if dist.sum() < _INNING_EPSILON:
                break
            total_runs += float(np.einsum("js,sj->", run_vectors, dist))
            absorbed = np.einsum("js,sj->j", absorb_vectors, dist)
            dist = np.roll(np.einsum("jts,sj->tj", transitions, dist), 1, axis=1)
            next_leadoff += np.roll(absorbed, 1)

        leadoff = next_leadoff

    return total_runs


# --------------------------------------------------------------------------
# Order search
# --------------------------------------------------------------------------

LINEUP_SIZE = 9

# Below this many PA no *claim* is made about a hitter in prose — they are
# not named "best bat off the bench", and their rate stats are not quoted as
# strengths. They are still projected, still ranked, and still shown with a
# number: the threshold gates assertions, not estimates. Selection itself has
# no threshold at all; conservative_woba handles thin evidence continuously,
# which is why a hard cutoff is no longer needed there.
MIN_PA_FOR_JUDGEMENT = 20

# How many posterior standard deviations to subtract when ranking. This is a
# risk preference, not a measurement: at z=1 we rank hitters by roughly the
# 16th percentile of what they might be, so a well-evidenced average bat is
# preferred to an unknown who *might* be good. One SD is deliberately mild —
# with the playing-time prior in place the point estimates are already
# honest about lightly-used hitters, so this no longer has to double as a
# correction for an over-generous prior (the old ad-hoc penalty did, which
# is why it also docked proven 60-PA regulars ~60 points of wOBA).
RANKING_CONFIDENCE_Z = 1.0

# Used only when a caller has no posterior SD to hand (no BattingTrueTalent
# row for this hitter). tau = sqrt(V_e / k) for a typical league-season here;
# at 0 PA the posterior SD equals exactly this, so it is the conservative
# choice for an unknown.
_FALLBACK_TALENT_SD = 0.046


def conservative_woba(estimate: float | None, pa: int, sd: float | None = None) -> float | None:
    """Evidence-aware ranking score: the (shrunk, adjusted) point estimate
    minus RANKING_CONFIDENCE_Z posterior standard deviations. Two hitters
    with the same estimate rank by who has more evidence behind theirs.

    `sd` is stats/shrinkage.py's `shrunk_woba_sd` — sqrt(V_e / (pa + k)) —
    and callers should pass it. It is a genuine posterior SD rather than the
    hand-tuned sd/sqrt(pa + floor) penalty this replaced, which was steep
    (.158 at 0 PA falling to .060 at 60) because it was silently standing in
    for a prior that assumed unknown hitters were league average. Now that
    the prior knows better, the honest uncertainty is much flatter (~.046 to
    ~.037 over the same range) and the two jobs are separated.

    For ranking and selection only; displayed estimates stay unpenalized."""
    if estimate is None:
        return None
    if sd is None or sd <= 0 or math.isnan(sd):
        sd = _FALLBACK_TALENT_SD
    return estimate - RANKING_CONFIDENCE_Z * sd


def select_starters(
    profiles: list[BatterProfile], size: int = LINEUP_SIZE, scores: list[float] | None = None
) -> tuple[list[BatterProfile], list[BatterProfile]]:
    """Split the available hitters into (starters, bench): the `size` best
    start and everyone else goes to the bench. "Best" is taken from `scores`
    when given (callers pass conservative_woba-style sample-aware scores);
    otherwise the profile's implied wOBA. Both halves keep their original
    relative order (ties broken by input position) so the output is
    deterministic for the same inputs."""
    if len(profiles) <= size:
        return list(profiles), []
    ranking = scores if scores is not None else [p.implied_woba for p in profiles]
    ranked = sorted(range(len(profiles)), key=lambda i: (-ranking[i], i))
    starter_indices = set(ranked[:size])
    starters = [p for i, p in enumerate(profiles) if i in starter_indices]
    bench = [p for i, p in enumerate(profiles) if i not in starter_indices]
    return starters, bench


# "The Book"-style seed: your best hitters bat in the first four slots
# (on-base ahead of power), your worst bat last. Expressed as which
# quality-rank goes in each lineup slot: e.g. the best hitter (rank 0) bats
# 2nd, the next two bat 1st and 4th... Slots past the 9th (a 10+ hitter
# lineup) just continue in descending quality.
_SEED_SLOT_FOR_RANK = [1, 0, 3, 2, 4, 5, 6, 7, 8]


def heuristic_order(profiles: list[BatterProfile]) -> list[int]:
    """Seed order (indices into `profiles`): descending overall quality
    mapped into the seed slots, with the on-base-heavier of the top two in
    the leadoff slot and the more powerful in the heart of the order."""
    n = len(profiles)
    ranked = sorted(range(n), key=lambda i: -profiles[i].implied_woba)
    order: list[int | None] = [None] * n
    for rank, idx in enumerate(ranked):
        slot = _SEED_SLOT_FOR_RANK[rank] if rank < len(_SEED_SLOT_FOR_RANK) else rank
        order[slot] = idx
    # Table-setter refinement: of the two hitters seeded 1st/2nd, lead off
    # with the better on-base profile; of the pair seeded 3rd/4th, bat the
    # more powerful cleanup.
    if n >= 2 and profiles[order[1]].p_onbase > profiles[order[0]].p_onbase:
        order[0], order[1] = order[1], order[0]
    if n >= 4 and profiles[order[2]].power > profiles[order[3]].power:
        order[2], order[3] = order[3], order[2]
    return [i for i in order if i is not None]


_IMPROVEMENT_EPSILON = 1e-6


def _hill_climb(profiles: list[BatterProfile], order: list[int], innings: int) -> tuple[list[int], float]:
    order = list(order)
    best = expected_runs([profiles[i] for i in order], innings)
    improved = True
    while improved:
        improved = False
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = list(order)
                candidate[i], candidate[j] = candidate[j], candidate[i]
                value = expected_runs([profiles[k] for k in candidate], innings)
                if value > best + _IMPROVEMENT_EPSILON:
                    order, best = candidate, value
                    improved = True
    return order, best


@dataclass
class LineupResult:
    order: list[str]
    expected_runs: float
    baselines: dict[str, float] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)


def optimize_lineup(
    profiles: list[BatterProfile], innings: int = DEFAULT_INNINGS, restarts: int = 2, seed: int = 0
) -> LineupResult:
    """Best batting order found for these profiles. Deterministic: the same
    profiles (same list order) always return the same lineup."""
    if not profiles:
        return LineupResult(order=[], expected_runs=0.0)

    seed_order = heuristic_order(profiles)
    best_order, best_runs = _hill_climb(profiles, seed_order, innings)

    rng = np.random.default_rng(seed)
    for _ in range(restarts):
        start = list(rng.permutation(len(profiles)))
        order, runs = _hill_climb(profiles, [int(i) for i in start], innings)
        if runs > best_runs + _IMPROVEMENT_EPSILON:
            best_order, best_runs = order, runs

    woba_desc = sorted(range(len(profiles)), key=lambda i: -profiles[i].implied_woba)
    baselines = {
        "recommended": best_runs,
        "by_woba_desc": expected_runs([profiles[i] for i in woba_desc], innings),
        "as_selected": expected_runs(profiles, innings),
    }
    return LineupResult(
        order=[profiles[i].name for i in best_order],
        expected_runs=best_runs,
        baselines=baselines,
        rationale=_rationales(profiles, best_order),
    )


def _rationales(profiles: list[BatterProfile], order: list[int]) -> list[str]:
    """Fallback rationale built from the profiles alone, for callers with no
    season stats to hand — prefer slot_rationales() (real box-score numbers)
    wherever season lines are available."""
    onbase_rank = {idx: r for r, idx in enumerate(sorted(order, key=lambda i: -profiles[i].p_onbase))}
    power_rank = {idx: r for r, idx in enumerate(sorted(order, key=lambda i: -profiles[i].power))}
    woba_rank = {idx: r for r, idx in enumerate(sorted(order, key=lambda i: -profiles[i].implied_woba))}

    lines = []
    for slot, idx in enumerate(order, start=1):
        p = profiles[idx]
        traits = []
        if onbase_rank[idx] <= 2:
            traits.append(f"{_ordinal(onbase_rank[idx])} projected on-base rate ({p.p_onbase:.3f})")
        if power_rank[idx] <= 2:
            traits.append(f"{_ordinal(power_rank[idx])} projected extra-base power")
        if not traits:
            traits.append(f"{_ordinal(woba_rank[idx])} overall bat of this nine")
        lines.append(f"{slot}. {p.name} — " + ", ".join(traits) + ".")
    return lines


def _ordinal(rank: int) -> str:
    return {0: "best", 1: "2nd-best", 2: "3rd-best"}.get(rank, f"{rank + 1}th-best")


# Traits considered for the per-slot rationale, in priority order: (stat
# key, higher-is-better, published stabilization point in the stat's own
# denominator, phrasing). K% is the one lower-is-better entry.
#
# The stabilization points are the standard published ones (Carleton et al.,
# the same literature stats/shrinkage.py's fallback comes from) and they
# differ by an order of magnitude between stats: strikeout rate is close to
# real after 60 PA, on-base percentage is still mostly noise at 300. Ranking
# raw rates would treat those as equally solid. `sb` is a counting stat with
# no rate denominator, so it isn't shrunk — a hitter can't accumulate steals
# without playing, which is its own sample-size guard.
_TRAIT_SPECS = [
    ("obp", True, 300.0, lambda v, o: f"{o} OBP in this lineup ({v:.3f})"),
    ("iso", True, 160.0, lambda v, o: f"{o.replace('best', 'most')} extra-base power (ISO {v:.3f})"),
    ("k_pct", False, 60.0, lambda v, o: f"{o.replace('best', 'hardest')} to strike out ({v:.0%} K rate)"),
    ("sb", True, None, lambda v, o: f"{o.replace('best', 'most')} stolen bases ({v:.0f} SB)"),
    ("bb_pct", True, 120.0, lambda v, o: f"{o} walk rate ({v:.0%} of PA)"),
]


def _shrunk_trait_ranks(order: list[str], stats_by_name: dict[str, dict]) -> dict[str, dict[str, int]]:
    """Rank each hitter on each trait *within this nine*, after shrinking the
    trait toward the nine's own PA-weighted mean by its published
    stabilization point.

    Shrinking toward the lineup's mean rather than the league's is the right
    regularizer here precisely because the claim being made is a within-nine
    one ("best OBP in this lineup"): the comparison set and the prior are the
    same population, so a hitter only out-ranks the others by evidence they
    actually have. This is what lets the hard PA threshold go. An 8-PA .450
    OBP shrinks to within a hair of the lineup average and quietly fails to
    place; an 80-PA .450 OBP stays where it is and gets the credit. No
    hitter is excluded from competing, and nothing changes discontinuously
    at any particular plate appearance."""

    def value(name: str, key: str):
        v = stats_by_name.get(name, {}).get(key)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    ranks: dict[str, dict[str, int]] = {}
    for key, higher_better, k, _ in _TRAIT_SPECS:
        present = [(name, value(name, key), stats_by_name.get(name, {}).get("pa") or 0) for name in order]
        present = [(n, v, pa) for n, v, pa in present if v is not None]
        if not present:
            ranks[key] = {}
            continue
        if k is None:
            scored = [(n, v) for n, v, _ in present]
        else:
            total_pa = sum(pa for _, _, pa in present)
            group_mean = (
                sum(v * pa for _, v, pa in present) / total_pa
                if total_pa
                else sum(v for _, v, _ in present) / len(present)
            )
            scored = [(n, (pa * v + k * group_mean) / (pa + k)) for n, v, pa in present]
        # Ties are common once traits are shrunk (identical rates collapse to
        # identical estimates), and float noise in the group mean would
        # otherwise order them arbitrarily. Break on lineup position so the
        # same inputs always give the same ranks.
        position = {name: i for i, name in enumerate(order)}
        scored.sort(key=lambda nv: (-nv[1] if higher_better else nv[1], position[nv[0]]))
        ranks[key] = {name: r for r, (name, _) in enumerate(scored)}
    return ranks


def evidence_label(pa: int, estimate: float | None, sd: float | None) -> str:
    """How much we know about a hitter, in one short phrase, for table cells
    (the bench "role" column) rather than prose.

    Deliberately states the projection instead of only the shortfall: the old
    "Too few PA to judge (7)" told a coach that we had no view, when in fact
    we had a view and a range for it. A hitter with no PA at all still gets a
    number — the playing-time prior's estimate for someone who hasn't played,
    which is below league average, not at it."""
    prefix = "No PA yet" if not pa else f"{pa} PA so far"
    if estimate is None or (isinstance(estimate, float) and math.isnan(estimate)):
        return prefix
    label = f"{prefix} — projects {estimate:.3f}"
    if sd and not math.isnan(sd) and sd > 0:
        label += f" ±{sd:.3f}"
    return label


def _evidence_note(stats: dict) -> str | None:
    """The thin-evidence half of a rationale line: what the model projects,
    how wide that is, and the raw line it came from. Returns None for a
    hitter with enough PA to describe in the ordinary way.

    This replaces a flat "too few PA to judge; treated as roughly league
    average", which was both unhelpful and untrue — the hitter was never
    treated as league average, and a coach still has to bat them somewhere.
    Showing the projection with its range says the same thing honestly:
    here is our best guess, here is how little we know, here is what we saw."""
    pa = stats.get("pa") or 0
    if pa >= MIN_PA_FOR_JUDGEMENT:
        return None

    estimate = stats.get("shrunk_woba")
    sd = stats.get("shrunk_woba_sd")
    bits = []
    if estimate is not None and not (isinstance(estimate, float) and math.isnan(estimate)):
        if sd and not math.isnan(sd) and sd > 0:
            bits.append(f"projects {estimate:.3f} wOBA (likely range {estimate - sd:.3f}-{estimate + sd:.3f})")
        else:
            bits.append(f"projects {estimate:.3f} wOBA")

    raw = _raw_line(stats)
    if raw:
        bits.append(raw)
    if not bits:
        return f"only {pa} PA so far — too little to say much, but they have to bat somewhere"
    return f"{pa} PA so far — " + "; ".join(bits)


def _raw_line(stats: dict) -> str | None:
    """"2-for-8 with a double and a walk" — the actual evidence, in the form
    a coach would say it out loud."""
    h, ab = stats.get("h"), stats.get("ab")
    if h is None or ab is None or not ab:
        return None
    extras = []
    for key, label in (("doubles", "double"), ("triples", "triple"), ("hr", "home run")):
        n = stats.get(key) or 0
        if n:
            extras.append(f"{n} {label}{'s' if n > 1 else ''}" if n > 1 else f"a {label}")
    bb = stats.get("bb") or 0
    if bb:
        extras.append(f"{bb} walks" if bb > 1 else "a walk")
    line = f"{int(h)}-for-{int(ab)}"
    if extras:
        line += " with " + (", ".join(extras[:-1]) + " and " + extras[-1] if len(extras) > 1 else extras[0])
    return line


def slot_rationales(order: list[str], stats_by_name: dict[str, dict]) -> list[str]:
    """One line per lineup slot, phrased in ordinary box-score stats (OBP,
    ISO, K%, SB, BB%) so the recommendation is traceable to numbers a coach
    already knows — never model internals like scaled wOBA, which all look
    alike. Each hitter is described by the traits where they rank top-3
    *within this specific nine*, so the line answers "why this hitter here,
    relative to the other eight".

    Hitters with little evidence behind them are described differently, not
    silently: they get their projection, its range, and their raw line (see
    _evidence_note). They also still compete for trait ranks, because those
    ranks are computed on shrunk traits and so can't be won on 8 PA of luck
    (see _shrunk_trait_ranks). stats_by_name values may contain pa, ab, h,
    doubles, triples, hr, bb, avg, obp, slg, iso, bb_pct, k_pct, sb,
    shrunk_woba, shrunk_woba_sd (any may be missing/None)."""

    def value(name: str, key: str):
        v = stats_by_name.get(name, {}).get(key)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    ranks = _shrunk_trait_ranks(order, stats_by_name)

    lines = []
    for slot, name in enumerate(order, start=1):
        stats = stats_by_name.get(name, {})
        pa = stats.get("pa") or 0
        if not pa:
            lines.append(
                f"{slot}. {name} — no season data yet; projected from the league's "
                "lightly-used hitters, who are below average."
            )
            continue

        note = _evidence_note(stats)
        if note is not None:
            # Thin evidence gets the projection and the raw line, never a
            # trait claim. Shrinking the trait ranks keeps this hitter from
            # *displacing* a proven one by much, but it cannot make an 8-PA
            # .600 OBP worth quoting as a strength — and quoting the shrunk
            # value instead would contradict the OBP column in the table
            # right above. So the claim is simply not made.
            lines.append(f"{slot}. {name} — {note}.")
            continue

        traits = []
        for key, _, _, phrase in _TRAIT_SPECS:
            rank = ranks.get(key, {}).get(name)
            v = value(name, key)
            if rank is not None and rank <= 2 and v is not None:
                # Zero steals or an ISO of .000 isn't a strength even if it
                # technically ranks — only claim traits with substance.
                if key == "sb" and v < 2:
                    continue
                if key == "iso" and v < 0.05:
                    continue
                traits.append(phrase(v, _ordinal(rank)))
            if len(traits) == 2:
                break

        if not traits:
            obp, slg = value(name, "obp"), value(name, "slg")
            line_bits = [f"{obp:.3f} OBP" if obp is not None else None, f"{slg:.3f} SLG" if slg is not None else None]
            summary = " / ".join(b for b in line_bits if b) or "limited data"
            traits.append(f"steady bat ({summary} over {pa} PA)")
        lines.append(f"{slot}. {name} — " + ", ".join(traits) + ".")
    return lines
