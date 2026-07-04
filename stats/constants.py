"""Fixed sabermetric constants used by stats/advanced_stats.py and
stats/war.py.

These are the *linear weight coefficients* — how many runs a event (a walk,
a double, a strikeout allowed, etc.) is worth relative to an out, on average.
Deriving these from scratch requires a run-expectancy matrix built from
play-by-play data (how many runs score, on average, from each base/out
state), which this league does not have. Published sabermetric research
(Tom Tango et al., "The Book"; FanGraphs' public sabermetrics glossary) has
shown these coefficients are fairly stable across run environments, so using
fixed values here — while everything *context-dependent* (league averages,
runs-per-win, FIP constant) is self-calibrated per season from this league's
own data in stats/league_context.py — is a reasonable approximation. See the
module docstring in stats/war.py for the full disclaimer surfaced in the UI.
"""

# wOBA linear weights (approximate values from published sabermetric
# research; treated as fixed across all seasons/leagues in this app).
WOBA_WEIGHT_UBB = 0.69  # unintentional walk
WOBA_WEIGHT_HBP = 0.72
WOBA_WEIGHT_1B = 0.89
WOBA_WEIGHT_2B = 1.27
WOBA_WEIGHT_3B = 1.62
WOBA_WEIGHT_HR = 2.10

# Converts wOBA-above-league-average back into runs above average per PA.
# Published wOBA scale values are typically ~1.15-1.25; fixed here rather
# than re-derived (that requires the same run-expectancy data noted above).
WOBA_SCALE = 1.15

# Replacement level for batters: a replacement-level player at 600 PA is
# conventionally worth about 20 runs below league average.
REPLACEMENT_RUNS_PER_600_PA = 20.0

# FIP linear weights (standard: HR weighted heaviest, BB/HBP allowed,
# strikeouts credited). The additive FIP_constant is NOT fixed — it's
# solved per league-season so that lgFIP == lgERA that season (see
# league_context.py) since this league's run environment differs from MLB's.
FIP_WEIGHT_HR = 13.0
FIP_WEIGHT_BB_HBP = 3.0
FIP_WEIGHT_SO = 2.0

# Replacement level for pitchers, expressed as runs/9 worse than league
# average FIP-implied runs. fWAR actually splits this by starter/reliever
# role using league-specific win% baselines; a single fixed constant is a
# simplification documented here rather than derived.
REPLACEMENT_FIP_RUNS_PER_9 = 0.90

# Runs-per-win: the traditional sabermetric rule of thumb is "10 runs = 1
# win" at a reference scoring environment of 4.5 runs/game/team (roughly
# modern MLB). Scaled linearly by this league's own actual runs/game so a
# higher- or lower-scoring environment shifts the runs-per-win rate
# accordingly. This is a simplified stand-in for the exact (non-linear)
# formula derived from the Pythagorean win expectation exponent, chosen
# because deriving the exact version needs more precise league-wide run
# distribution data than is available here.
REFERENCE_RUNS_PER_GAME = 4.5
REFERENCE_RUNS_PER_WIN = 10.0

FORMULA_VERSION = "v1"
