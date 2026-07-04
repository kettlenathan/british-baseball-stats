import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import trend_chart
from app.components.data_access import player_batting_comparison, player_pitching_comparison
from app.components.filters import player_multiselect
from app.components.formatting import BATTING_COLUMN_CONFIG, PITCHING_COLUMN_CONFIG

st.set_page_config(page_title="Player Comparison", page_icon="🆚", layout="wide")
st.title("Player Comparison")
st.caption("Pick two or more players to compare their batting and pitching careers side by side.")

players = player_multiselect()
if len(players) < 2:
    st.info("Pick at least two players to compare.")
    st.stop()

st.subheader("Batting")
bat_df = player_batting_comparison(players)
if bat_df.empty:
    st.info("No batting seasons found for any selected player.")
else:
    st.dataframe(
        bat_df,
        hide_index=True,
        use_container_width=True,
        column_config=BATTING_COLUMN_CONFIG,
    )
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(trend_chart(bat_df, "year", "ops", color_col="player"), use_container_width=True)
    with col2:
        st.plotly_chart(trend_chart(bat_df, "year", "war", color_col="player"), use_container_width=True)

st.divider()

st.subheader("Pitching")
pitch_df = player_pitching_comparison(players)
if pitch_df.empty:
    st.info("No pitching seasons found for any selected player.")
else:
    st.dataframe(
        pitch_df,
        hide_index=True,
        use_container_width=True,
        column_config=PITCHING_COLUMN_CONFIG,
    )
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(trend_chart(pitch_df, "year", "era", color_col="player"), use_container_width=True)
    with col2:
        st.plotly_chart(trend_chart(pitch_df, "year", "war", color_col="player"), use_container_width=True)
