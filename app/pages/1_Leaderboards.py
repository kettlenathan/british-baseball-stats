import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.data_access import batting_leaderboard, pitching_leaderboard, standings
from app.components.filters import league_season_selector
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
            column_config={
                "avg": st.column_config.NumberColumn(format="%.3f"),
                "obp": st.column_config.NumberColumn(format="%.3f"),
                "slg": st.column_config.NumberColumn(format="%.3f"),
                "ops": st.column_config.NumberColumn(format="%.3f"),
                "iso": st.column_config.NumberColumn(format="%.3f"),
                "woba": st.column_config.NumberColumn(format="%.3f"),
                "wrc_plus": st.column_config.NumberColumn("wRC+", format="%.0f"),
                "war": st.column_config.NumberColumn("WAR", format="%.2f"),
                "bb_pct": st.column_config.NumberColumn("BB%", format="%.1%"),
                "k_pct": st.column_config.NumberColumn("K%", format="%.1%"),
            },
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
            column_config={
                "era": st.column_config.NumberColumn(format="%.2f"),
                "whip": st.column_config.NumberColumn(format="%.2f"),
                "k9": st.column_config.NumberColumn("K/9", format="%.1f"),
                "bb9": st.column_config.NumberColumn("BB/9", format="%.1f"),
                "fip": st.column_config.NumberColumn(format="%.2f"),
                "era_plus": st.column_config.NumberColumn("ERA+", format="%.0f"),
                "war": st.column_config.NumberColumn("WAR", format="%.2f"),
                "ip": st.column_config.NumberColumn("IP", format="%.1f"),
            },
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
            column_config={"pct": st.column_config.NumberColumn(format="%.3f")},
        )
