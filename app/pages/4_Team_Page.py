import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.data_access import batting_leaderboard, pitching_leaderboard, team_recent_games, team_roster, team_season_stats
from app.components.filters import league_season_selector
from app.components.formatting import (
    BATTING_COLUMN_CONFIG,
    PITCHING_COLUMN_CONFIG,
    RECENT_GAMES_COLUMN_CONFIG,
    ROSTER_COLUMN_CONFIG,
    TEAM_COLUMN_CONFIG,
)

st.set_page_config(page_title="Team Page", page_icon="🏟️", layout="wide")
st.title("Team Page")

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

roster_df = team_roster(league_season_id)
if roster_df.empty:
    st.info("No rosters scraped for this league/season yet.")
    st.stop()

teams = sorted(roster_df["team"].unique())
team = st.selectbox("Team", teams)

st.subheader("Team stats")
stats_df = team_season_stats(league_season_id)
team_stats_row = stats_df[stats_df["team"] == team]
if team_stats_row.empty:
    st.info("No combined stats available for this team yet.")
else:
    st.dataframe(
        team_stats_row,
        hide_index=True,
        use_container_width=True,
        column_config=TEAM_COLUMN_CONFIG,
    )

st.subheader("Recent performance")
recent_df = team_recent_games(league_season_id, team, weeks=3)
if recent_df.empty:
    st.info("No games in the last 3 weekends for this team.")
else:
    st.dataframe(
        recent_df,
        hide_index=True,
        use_container_width=True,
        column_config=RECENT_GAMES_COLUMN_CONFIG,
    )

st.subheader("Roster")
st.dataframe(
    roster_df[roster_df["team"] == team].drop(columns=["team"]),
    hide_index=True,
    use_container_width=True,
    column_config=ROSTER_COLUMN_CONFIG,
)

col1, col2 = st.columns(2)
with col1:
    st.subheader("Batting")
    bat_df = batting_leaderboard(league_season_id, min_pa=0)
    st.dataframe(
        bat_df[bat_df["team"] == team].sort_values("pa", ascending=False),
        hide_index=True,
        use_container_width=True,
        column_config=BATTING_COLUMN_CONFIG,
    )
with col2:
    st.subheader("Pitching")
    pitch_df = pitching_leaderboard(league_season_id, min_ip=0)
    st.dataframe(
        pitch_df[pitch_df["team"] == team].sort_values("ip", ascending=False),
        hide_index=True,
        use_container_width=True,
        column_config=PITCHING_COLUMN_CONFIG,
    )
