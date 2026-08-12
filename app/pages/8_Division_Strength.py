import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.data_access import division_comparison, division_environments
from app.components.filters import league_season_selector
from app.components.formatting import PCT_COLUMN_CONFIG, column_config_for

st.set_page_config(page_title="Division Strength", page_icon="⚖️", layout="wide")
st.title("Division Strength")
st.caption(
    "Teams play only inside their own division, so their records were built against "
    "different opposition. This page asks how those divisions compared — and shows the two "
    "ways of answering that, because they disagree."
)

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

comparison = division_comparison(league_season_id)
if comparison.empty:
    st.info(
        "No division comparison is available for this league-season — it either has only "
        "one division, or too few players who appeared in more than one."
    )
    st.stop()

st.markdown(
    "The most direct way to compare players across divisions is on the **Leaderboards** "
    "page, which shows every hitter's `wRC+` (against the whole competition) beside their "
    "`wRC+ vs Div` (against their own division). That pair needs no model at all, and it is "
    "the recommended way to read across divisions."
)
st.markdown(
    "What that pair *cannot* tell you is **why** a division scored the way it did. A "
    "division looks high-scoring whether its pitching was weak or its hitters were strong. "
    "This page separates those two explanations."
)

st.subheader("How easy was each division to bat in?")
st.markdown(
    "Both columns are in wRC+ points, relative to this league-season as a whole. "
    "Positive means easier."
)
st.dataframe(
    comparison,
    hide_index=True,
    use_container_width=True,
    column_config=column_config_for(comparison),
)
st.markdown(
    "- **Scoring gap** — implied by the division's own scoring level. This is what the two "
    "wRC+ columns on the Leaderboards already encode.\n"
    "- **Same-player gap** — the same question asked of players who batted in more than one "
    "division, which controls for who happened to play where.\n"
    "- **Down to who played** — the difference between them. A large positive value means the "
    "division scored more heavily than its conditions alone explain, i.e. it held the "
    "stronger hitters; a large negative value means it was tougher than its scoring suggests."
)

st.info(
    "**Where the two disagree, trust the direction more than the size.** The same-player "
    "gaps are systematically wider than the scoring gaps, which is what you would expect "
    "from the one bias that cannot be corrected here: a player turning out in a second "
    "division is more often guesting *down* a level than moving up, and a stronger player "
    "visiting a weaker division exaggerates the difference between them."
)

with st.expander("Worked example — 2026 Division 3"):
    st.markdown(
        "Milton Keynes Bucks went 20-0 in Central while London Meteors went 19-5 in South, "
        "and the two never played."
    )
    st.markdown(
        "Both readings agree that Central was the easier of the two. By scoring level it sat "
        "about 8 wRC+ points above the league and South about 3 below; by players who batted "
        "in both, Central was about 7 above and South about 7 below. Whichever measure you "
        "take, Central was somewhere around 11 to 14 wRC+ points the softer division, so the "
        "Bucks' unbeaten record deserves a real discount against the Meteors' 19-5."
    )
    st.markdown(
        "The two readings disagree more elsewhere in the same league. North scored only "
        "slightly above the league but shared players found it much the easiest division of "
        "the four — meaning its scoring understates how weak the *hitting* in it was."
    )
    st.markdown(
        "What this page will **not** do is turn any of that into a probability that one club "
        "beats another. With around 22 games each, a team's own strength is the far larger "
        "unknown — it accounts for roughly 96% of the uncertainty in such a comparison — and "
        "about three quarters of cross-division pairings in this league-season cannot be "
        "separated at all. A single percentage would imply a precision that does not exist."
    )

st.divider()

st.subheader("Run environments")
env = division_environments(league_season_id)
if not env.empty:
    st.dataframe(
        env, hide_index=True, use_container_width=True, column_config=PCT_COLUMN_CONFIG
    )
    st.caption(
        "Raw scoring by division, the input behind the scoring gap above. Regular-season "
        "intra-division games only; forfeits are excluded, since no runs were scored in them."
    )

with st.expander("How the same-player comparison works, and what it cannot do"):
    st.markdown(
        "Divisions play no regular-season games against each other, so their relative "
        "standard has to come from somewhere other than results. It comes from **players who "
        "appeared in more than one division**: when the same person bats in two, the "
        "difference in what they produce is evidence about the difference between them. "
        "Across the seasons here, 1,221 pairs of divisions share at least one such player, "
        "enough to link every division into a single connected network."
    )
    st.markdown(
        "The estimate uses only *within-player* differences. A player who appeared in one "
        "division contributes nothing at all and cannot make their division look easy simply "
        "by being a good hitter — which is precisely the contamination that the scoring-level "
        "measure suffers from, and the reason both are shown."
    )
    st.markdown(
        "**Two limits worth stating plainly.** The selection bias above is real, uncorrected, "
        "and pushes the same-player gaps outward. And while this construction does beat "
        "assuming the divisions are equal when tested against 305 games that genuinely did "
        "cross divisions, it does so by a margin only about twice its own standard error. "
        "These are the least certain numbers on the site."
    )
    st.markdown("See the Methodology page for the full description.")
