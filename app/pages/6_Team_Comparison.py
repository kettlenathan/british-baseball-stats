import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import bar_chart, radar_chart, trend_chart
from app.components.data_access import team_history, team_season_stats
from app.components.filters import league_season_selector, team_multiselect
from app.components.formatting import BATTING_COLUMN_CONFIG, PCT_COLUMN_CONFIG, PITCHING_COLUMN_CONFIG, TEAM_COLUMN_CONFIG

st.set_page_config(page_title="Team Comparison", page_icon="📈", layout="wide")
st.title("Team Comparison")

# Each spoke of the head-to-head radar is a percentile rank among every team
# in the chosen league/season (not just the ones being compared) so stats on
# very different scales — ERA, OPS, fielding % — can share one 0-100 axis;
# "-" stats are rank-inverted so lower raw values still read as higher on
# the chart, matching the "higher is always better" convention of the other
# spokes.
_H2H_STATS = [
    ("pct", "+"), ("war", "+"), ("ops", "+"), ("wrc_plus", "+"),
    ("era", "-"), ("fip", "-"), ("fpct", "+"), ("avg_risp", "+"),
]

st.subheader("Year-over-year")
st.caption("Pick one team to see its win-pct trend, or two or more to compare them against each other.")

teams = team_multiselect()
if not teams:
    st.info("Pick at least one team.")
else:
    trend_df = team_history(teams)
    if trend_df.empty:
        st.info("No completed games found for the selected team(s).")
    else:
        st.dataframe(trend_df, hide_index=True, use_container_width=True, column_config=PCT_COLUMN_CONFIG)
        st.plotly_chart(
            trend_chart(trend_df, "year", "pct", color_col="team", reference_y=0.5), use_container_width=True
        )

st.divider()

st.subheader("Head-to-head")
st.caption("Compare teams within a single league/season across batting, pitching, and fielding stats.")

league_season_id = league_season_selector(key="h2h_league_season")
if league_season_id is None:
    st.stop()

season_stats = team_season_stats(league_season_id)
if season_stats.empty:
    st.info("No team stats available for this league/season yet.")
    st.stop()

available_teams = sorted(season_stats["team"])
h2h_teams = st.multiselect("Teams", available_teams, default=available_teams[:2], key="h2h_teams")
if len(h2h_teams) < 2:
    st.info("Pick at least two teams to compare.")
    st.stop()

value_cols = [col for col, _ in _H2H_STATS]
percentiles = season_stats[["team"]].copy()
for col, direction in _H2H_STATS:
    percentiles[col] = season_stats[col].rank(pct=True, ascending=(direction == "+")) * 100
percentiles[value_cols] = percentiles[value_cols].fillna(50)  # neutral when a team has no qualifying sample

selected_pct = percentiles[percentiles["team"].isin(h2h_teams)].reset_index(drop=True)
selected_raw = season_stats[season_stats["team"].isin(h2h_teams)].reset_index(drop=True)

st.plotly_chart(radar_chart(selected_pct, value_cols), use_container_width=True)
st.caption(
    "Each spoke is a percentile rank (100 = best) among every team in this league/season, "
    "not just the ones shown — so ERA, OPS, and fielding % can share one axis."
)

with st.expander("Per-stat comparison (actual values)"):
    cols = st.columns(4)
    for i, (col, _) in enumerate(_H2H_STATS):
        with cols[i % 4]:
            st.plotly_chart(bar_chart(selected_raw, "team", col), use_container_width=True)

with st.expander("Underlying data"):
    st.dataframe(
        selected_raw,
        hide_index=True,
        use_container_width=True,
        column_config={**TEAM_COLUMN_CONFIG, **BATTING_COLUMN_CONFIG, **PITCHING_COLUMN_CONFIG},
    )
