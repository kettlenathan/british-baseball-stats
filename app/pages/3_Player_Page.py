import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import trend_chart
from app.components.data_access import all_player_names, player_batting_career, player_pitching_career
from app.components.formatting import BATTING_COLUMN_CONFIG, PITCHING_COLUMN_CONFIG

st.set_page_config(page_title="Player Page", page_icon="🧑‍💼", layout="wide")
st.title("Player Page")

names = all_player_names()
if not names:
    st.info("No players scraped yet.")
    st.stop()

player = st.selectbox("Player", names)
bat_df = player_batting_career(player)
pitch_df = player_pitching_career(player)

if bat_df.empty and pitch_df.empty:
    st.info("No seasons found for this player.")
    st.stop()

if not bat_df.empty:
    st.subheader(f"{player} — career batting")
    st.dataframe(
        bat_df,
        hide_index=True,
        use_container_width=True,
        column_config=BATTING_COLUMN_CONFIG,
    )
    if len(bat_df) > 1:
        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(trend_chart(bat_df, "year", "ops"), use_container_width=True)
        with col2:
            st.plotly_chart(trend_chart(bat_df, "year", "war"), use_container_width=True)

if not pitch_df.empty:
    st.subheader(f"{player} — career pitching")
    st.dataframe(
        pitch_df,
        hide_index=True,
        use_container_width=True,
        column_config=PITCHING_COLUMN_CONFIG,
    )
    if len(pitch_df) > 1:
        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(trend_chart(pitch_df, "year", "era"), use_container_width=True)
        with col2:
            st.plotly_chart(trend_chart(pitch_df, "year", "war"), use_container_width=True)
