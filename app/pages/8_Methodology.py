import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from stats import constants
from stats.war import WAR_DISCLAIMER

st.set_page_config(page_title="Methodology", page_icon="📖", layout="wide")
st.title("Methodology")
st.caption(
    "How the stats on this site are collected and calculated, and where they "
    "diverge from official Baseball-Reference/FanGraphs definitions."
)

st.subheader("Data source")
st.markdown(
    "All data is scraped from [stats.britishbaseball.org.uk](https://stats.britishbaseball.org.uk), "
    "the official stats platform for the British Baseball Federation. Box scores are only "
    "pulled in once a game is marked `final` on the source site, so figures shown here "
    "should match the federation's own published totals."
)

st.divider()

st.subheader("wOBA and wRC+")
st.markdown(
    "**wOBA** (weighted On-Base Average) values every way of reaching base by how many runs "
    "it's actually worth, instead of treating a walk and a home run as equal like OBP does:"
)
st.latex(
    r"""
    wOBA = \frac{%.2f \cdot uBB + %.2f \cdot HBP + %.2f \cdot 1B + %.2f \cdot 2B + %.2f \cdot 3B + %.2f \cdot HR}
    {AB + BB - IBB + SF + HBP}
    """
    % (
        constants.WOBA_WEIGHT_UBB,
        constants.WOBA_WEIGHT_HBP,
        constants.WOBA_WEIGHT_1B,
        constants.WOBA_WEIGHT_2B,
        constants.WOBA_WEIGHT_3B,
        constants.WOBA_WEIGHT_HR,
    )
)
st.markdown(
    "**wRC+** expresses a player's wOBA relative to the league average for that league-season "
    "(100 = league average, 120 = 20% better than average, and so on): "
    "`100 × (player wOBA / league wOBA)`."
)

st.subheader("FIP and ERA+")
st.markdown(
    "**FIP** (Fielding Independent Pitching) scores a pitcher only on the outcomes they control "
    "directly — strikeouts, walks, hit-by-pitches, and home runs — since no fielder positioning "
    "or range data exists for this league to judge defense separately from pitching:"
)
st.latex(
    r"""
    FIP = \frac{%.1f \cdot HR + %.1f \cdot (BB + HBP) - %.1f \cdot SO}{IP} + FIP_{constant}
    """
    % (constants.FIP_WEIGHT_HR, constants.FIP_WEIGHT_BB_HBP, constants.FIP_WEIGHT_SO)
)
st.markdown(
    "**ERA+** expresses a pitcher's ERA relative to the league average, inverted so higher is "
    "still better: `100 × (league ERA / player ERA)`."
)

st.divider()

st.subheader("What's self-calibrated to this league vs. fixed")
st.markdown(
    "The **linear weight coefficients** above (the numbers in front of each stat in the "
    "formulas) are fixed, published sabermetric constants — deriving them from scratch needs "
    "a run-expectancy matrix built from play-by-play data, which this league doesn't have. "
    "Published research (Tom Tango et al., *The Book*) shows these coefficients are fairly "
    "stable across different run environments, so using fixed values here is a reasonable "
    "approximation."
)
st.markdown(
    "What **is** calculated fresh from this league's own scraped data, separately for every "
    "league and season:"
)
st.markdown(
    "- League-average wOBA, OBP, SLG, ERA, and FIP\n"
    "- The FIP additive constant (solved so league FIP equals league ERA that season)\n"
    "- The runs-per-win conversion rate, scaled from the traditional \"10 runs = 1 win\" "
    f"reference (at {constants.REFERENCE_RUNS_PER_GAME} runs/game/team) by this league's own "
    "actual scoring rate"
)
st.markdown(
    "This is what makes a \"0 WAR\" or \"100 wRC+\" player here mean *league-average within "
    "this league's own actual run environment* — not relative to MLB or any other league."
)

st.divider()

st.subheader("WAR — what it is and isn't")
st.warning(WAR_DISCLAIMER)
st.markdown(
    "In more detail, this WAR calculation is missing three things a full implementation "
    "would have:\n"
    "- **Park factors** — no per-venue run environment data exists for this league, so every "
    "park is treated as neutral.\n"
    "- **A defensive component** — there's a coarse batted-ball proxy (pull direction, distance, "
    "ground/fly/line/pop type — see the section below), but no true field coordinates, exit "
    "velocity, or fielder positioning/range data, so batting WAR is offense-only and pitching WAR "
    "is FIP-only; a plus defender and a poor one with identical batting/pitching lines get the "
    "same WAR here.\n"
    "- **League-specific linear weights** — the coefficients are fixed published constants "
    "(see above) rather than solved from this league's own play-by-play, since that needs a far "
    "larger sample than this league's scale can support for a stable result.\n\n"
    f"Formula version: `{constants.FORMULA_VERSION}` (stored alongside every computed WAR row, "
    "so historical values stay traceable to the formula that produced them if it's ever revised)."
)

st.divider()

st.subheader("Batted-ball tendency, spray charts, matchups, and first-pitch-strike%")
st.markdown(
    "This league's scorers don't record true batted-ball field coordinates or exit velocity "
    "(those fields are always empty in the source data) — but they do record a directional "
    "**pull value** (roughly which side of the field a ball was hit to) and a **hit distance** "
    "for every ball put in play. That's enough for an approximation of a spray chart and a "
    "pull-tendency read, just not a to-scale field diagram."
)
st.markdown(
    "**Pull / Center / Oppo tendency**: every batted ball's pull value is adjusted for the "
    "batter's own handedness (so \"pulled\" always means the same thing regardless of which "
    "side someone bats from), then bucketed against **fixed thirds of the true 90-degree "
    "fair-territory fan** — the middle 30 degrees (+/-15 degrees off dead-center) is Center, "
    "the outer 15-45 degrees on the batter's pull side is Pull, and the same range on the "
    "other side is Oppo. Unlike the league-average wOBA/FIP above, this is deliberately **not** "
    "self-calibrated to this league's own batted-ball distribution — a real ballpark's foul "
    "lines don't move with it, so neither does \"pulled\". A player's tendency label is "
    "whichever third holds the most of their own batted balls. **Switch hitters are excluded** "
    "from this entirely — there's no per-plate-appearance record of which side they actually "
    "batted from in a given at-bat, so classifying them would risk mislabeling roughly half "
    "their pulled balls as opposite-field and vice versa."
)
st.markdown(
    "**Spray chart**: plotted on a radial (polar) chart — angle from the raw pull direction, "
    "distance from home plate as the radius — approximating a real spray chart's shape without "
    "claiming to be a precise field-location plot. The pull value is treated as degrees off "
    "dead-center field and clamped to the true +/-45 degrees of a real ballpark's fair "
    "territory (foul line to foul line), so no point ever plots outside the field."
)
st.markdown(
    "**Direction heatmap**: a second chart alongside the spray chart, on the same schematic "
    "field and the same +/-45 degree fan (with the surrounding polar grid/boundary dropped — "
    "only the field lines themselves frame the chart), but dropping hit distance entirely — a "
    "handful of raw distance values are negative, which is impossible, so distance is the less "
    "trustworthy of the two fields. Batted balls are bucketed into angular wedges spanning the "
    "full field depth, colored red for the most common directions and blue for the least "
    "common, to give a distance-independent read on where a player's contact actually goes."
)
st.markdown(
    "**First-pitch-strike%**: the source data's own pitch-result flags (ball/called "
    "strike/swinging strike/foul/in play) are never populated for this league, so a first-pitch "
    "strike is instead inferred by comparing the ball-strike count on the first pitch of a plate "
    "appearance to the next pitch in that same plate appearance — if the strike count went up (or "
    "the first pitch itself ended the at-bat as a ball in play, not a walk or hit-by-pitch), it "
    "counts as a first-pitch strike."
)
st.markdown(
    "**Batter-vs-pitcher matchups**: aggregated directly from plate-appearance results, shown "
    "for both season and career scope. There's **no minimum plate-appearance threshold** — a "
    "single at-bat between two players shows up the same as a 20-at-bat history, so treat small "
    "samples with appropriate skepticism."
)

st.divider()
st.caption(
    "Spotted something that looks wrong, or have a question about how a stat is calculated? "
    "Use the Feedback page in the sidebar."
)
