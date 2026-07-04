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
    "directly — strikeouts, walks, hit-by-pitches, and home runs — since no batted-ball data "
    "exists for this league to judge defense separately from pitching:"
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
    "- **A defensive component** — there's no batted-ball or fielding-range tracking data, so "
    "batting WAR is offense-only and pitching WAR is FIP-only; a plus defender and a poor one "
    "with identical batting/pitching lines get the same WAR here.\n"
    "- **League-specific linear weights** — the coefficients are fixed published constants "
    "(see above), not re-derived from this league's own play-by-play data.\n\n"
    f"Formula version: `{constants.FORMULA_VERSION}` (stored alongside every computed WAR row, "
    "so historical values stay traceable to the formula that produced them if it's ever revised)."
)

st.divider()
st.caption(
    "Spotted something that looks wrong, or have a question about how a stat is calculated? "
    "Use the Feedback page in the sidebar."
)
