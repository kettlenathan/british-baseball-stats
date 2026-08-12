import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.data_access import (
    cross_division_comparison,
    division_strength_table,
    head_to_head,
)
from app.components.filters import league_season_selector
from app.components.formatting import column_config_for

st.set_page_config(page_title="Division Strength", page_icon="⚖️", layout="wide")
st.title("Division Strength")
st.caption(
    "Teams only play inside their own division, so their records were built against "
    "different opposition. This page estimates how those divisions compare — and is "
    "honest about how uncertain that estimate is."
)

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

comparison = cross_division_comparison(league_season_id)
if comparison.empty:
    st.info(
        "No cross-division estimate is available for this league-season — it either has "
        "only one division, or too few players who appeared in more than one."
    )
    st.stop()

st.warning(
    "**These are the least certain numbers on the site.** Nothing in the regular season "
    "links one division to another, so this rests entirely on players who turned out in "
    "more than one. Tested against 305 games that did cross divisions, it predicts better "
    "than assuming the divisions are equal — but only by about two standard errors, and "
    "most individual team comparisons below are genuinely too close to call."
)

st.subheader("Who is actually stronger?")
st.markdown(
    "Pick any two teams, including ones that never played each other, for a "
    "neutral-venue estimate."
)

teams = list(comparison["team"])
col_a, col_b = st.columns(2)
team_a = col_a.selectbox("Team", teams, index=0, key="team_a")
team_b = col_b.selectbox("Opponent", teams, index=min(1, len(teams) - 1), key="team_b")

if team_a == team_b:
    st.info("Pick two different teams.")
else:
    result = head_to_head(comparison, team_a, team_b)
    favourite = team_a if result["probability"] >= 0.5 else team_b
    probability = max(result["probability"], 1 - result["probability"])
    low, high = sorted((result["low"], result["high"]))
    if favourite == team_b:
        low, high = 1 - high, 1 - low

    st.metric(
        f"{favourite} to win, at a neutral venue",
        f"{probability:.0%}",
        help="Bradley-Terry ratings plus the division adjustment, on a common scale.",
    )
    st.caption(f"95% interval: **{low:.0%} to {high:.0%}**")

    if result["same_division"]:
        st.success(
            "These two are in the same division and have played each other, so this "
            "comparison rests on real head-to-head evidence rather than the division "
            "adjustment."
        )
    elif result["decisive"]:
        st.info(
            f"The gap is wider than the uncertainty, so **{favourite} are the stronger "
            "side** on this evidence — though they never met."
        )
    else:
        st.warning(
            "**Too close to call.** The interval spans both sides of an even contest, so "
            "the honest answer is that this data cannot separate these two teams. That is "
            "a real finding, not a missing number — they never played, and the players "
            "linking their divisions are too few to settle it."
        )

st.divider()

st.subheader("All teams on one scale")
st.markdown(
    "`Rating` is comparable only within a division. `Adjustment` is what this page adds "
    "for the division's standard, and `Adjusted rating` puts every team on a common "
    "scale. `Uncertainty` is one standard error — compare gaps against roughly twice it."
)
st.dataframe(
    comparison,
    hide_index=True,
    use_container_width=True,
    column_config=column_config_for(comparison),
)

st.divider()

st.subheader("How the divisions compare")
offsets = division_strength_table(league_season_id)
if offsets.empty:
    st.info("No division offsets available for this league-season.")
else:
    st.dataframe(
        offsets,
        hide_index=True,
        use_container_width=True,
        column_config=column_config_for(offsets),
    )
    st.caption(
        "`Offset` is in wOBA points and measures how much **easier to bat in** a division "
        "was, so a positive number implies weaker pitching. `Adjustment` converts that to "
        "the rating scale. `Bridges` counts the players who appeared in this division and "
        "at least one other — every estimate here rests on them, and a division with few "
        "of them deserves proportionately less trust."
    )

with st.expander("How this is worked out, and why to be cautious"):
    st.markdown(
        "Divisions play no regular-season games against each other, so their relative "
        "standard has to come from somewhere else. It comes from **players who appeared "
        "in more than one division**: when the same person bats in two, the difference in "
        "what they produce is evidence about the difference between those divisions. This "
        "is the reasoning behind Major League Equivalencies, applied to a league that needs "
        "it because its divisions never meet."
    )
    st.markdown(
        "The estimate uses only *within-player* variation — a player who appeared in one "
        "division contributes nothing, and cannot make their division look strong merely "
        "by being good. Converting \"easier to bat in\" into \"wins games\" needs one more "
        "number, which is fitted against the games in the archive that did cross divisions."
    )
    st.markdown(
        "**Two reasons to treat all of this as provisional.** First, players who move "
        "between divisions are not a random sample — someone guesting down a level is "
        "likely stronger than the division they visit, which inflates the apparent gap. "
        "Nothing here corrects for that. Second, the whole construction was tested on only "
        "305 cross-division games, and while it beat assuming divisions are equal, it did "
        "so by a margin that is itself close to the noise."
    )
    st.markdown("See the Methodology page for the full description.")
