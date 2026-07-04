import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.data_access import batting_leaderboard, pitching_leaderboard, standings
from app.components.filters import league_season_selector
from app.components.formatting import BATTING_COLUMN_CONFIG, PCT_COLUMN_CONFIG, PITCHING_COLUMN_CONFIG
from stats.war import WAR_DISCLAIMER

st.set_page_config(page_title="Leaderboards", page_icon="⚾", layout="wide")
st.title("Leaderboards")

league_season_id = league_season_selector()
if league_season_id is None:
    st.stop()

tab_batting, tab_pitching, tab_standings = st.tabs(["Batting", "Pitching", "Standings"])

with tab_batting:
    min_pa = st.slider("Minimum PA", 0, 100, 10, key="min_pa")
    df = batting_leaderboard(league_season_id, min_pa=min_pa)
    if df.empty:
        st.info("No qualifying batters.")
    else:
        st.dataframe(
            df.sort_values("war", ascending=False),
            hide_index=True,
            use_container_width=True,
            column_config=BATTING_COLUMN_CONFIG,
        )
        st.caption(WAR_DISCLAIMER)

with tab_pitching:
    min_ip = st.slider("Minimum IP", 0, 60, 5, key="min_ip")
    df = pitching_leaderboard(league_season_id, min_ip=min_ip)
    if df.empty:
        st.info("No qualifying pitchers.")
    else:
        st.dataframe(
            df.sort_values("war", ascending=False),
            hide_index=True,
            use_container_width=True,
            column_config=PITCHING_COLUMN_CONFIG,
        )
        st.caption(WAR_DISCLAIMER)

with tab_standings:
    df = standings(league_season_id)
    if df.empty:
        st.info("No completed games yet.")
    else:
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config=PCT_COLUMN_CONFIG,
        )
