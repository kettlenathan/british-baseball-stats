import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import scatter_chart
from app.components.data_access import batting_leaderboard, pitching_leaderboard
from app.components.filters import league_season_selector

st.set_page_config(page_title="Player Explorer", page_icon="📊", layout="wide")
st.title("Player Explorer")
st.caption("Pick any two stats to compare batters or pitchers on a scatter plot.")

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

category = st.radio("Category", ["Batting", "Pitching"], horizontal=True)

if category == "Batting":
    min_pa = st.slider("Minimum PA", 0, 100, 10)
    df = batting_leaderboard(league_season_id, min_pa=min_pa)
    numeric_cols = [
        "pa", "ab", "h", "doubles", "triples", "hr", "rbi", "bb", "so", "sb",
        "avg", "obp", "slg", "ops", "iso", "bb_pct", "k_pct", "woba", "wrc_plus", "war",
    ]
else:
    min_ip = st.slider("Minimum IP", 0, 60, 5)
    df = pitching_leaderboard(league_season_id, min_ip=min_ip)
    numeric_cols = ["w", "l", "sv", "so", "bb", "h", "er", "ip", "era", "whip", "k9", "bb9", "fip", "era_plus", "war"]

if df.empty:
    st.info("No qualifying players for this filter.")
    st.stop()

teams = sorted(df["team"].unique())
selected_teams = st.multiselect("Teams", teams, default=teams)
df = df[df["team"].isin(selected_teams)]

numeric_cols = [c for c in numeric_cols if c in df.columns]
col1, col2 = st.columns(2)
x_axis = col1.selectbox("X axis", numeric_cols, index=numeric_cols.index("war") if "war" in numeric_cols else 0)
y_axis = col2.selectbox("Y axis", numeric_cols, index=min(1, len(numeric_cols) - 1))

fig = scatter_chart(df, x_axis, y_axis)
st.plotly_chart(fig, use_container_width=True)

with st.expander("Underlying data"):
    st.dataframe(df, hide_index=True, use_container_width=True)
