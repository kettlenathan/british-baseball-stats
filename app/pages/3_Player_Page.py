import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import trend_chart
from app.components.data_access import all_player_names, player_career

st.set_page_config(page_title="Player Page", page_icon="🧑‍💼", layout="wide")
st.title("Player Page")

names = all_player_names()
if not names:
    st.info("No players scraped yet.")
    st.stop()

player = st.selectbox("Player", names)
df = player_career(player)

if df.empty:
    st.info("No batting seasons found for this player (they may be pitcher-only).")
    st.stop()

st.subheader(f"{player} — career batting")
st.dataframe(
    df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "avg": st.column_config.NumberColumn(format="%.3f"),
        "obp": st.column_config.NumberColumn(format="%.3f"),
        "slg": st.column_config.NumberColumn(format="%.3f"),
        "ops": st.column_config.NumberColumn(format="%.3f"),
        "war": st.column_config.NumberColumn("WAR", format="%.2f"),
    },
)

if len(df) > 1:
    col1, col2 = st.columns(2)
    with col1:
        st.plotly_chart(trend_chart(df, "year", "ops"), use_container_width=True)
    with col2:
        st.plotly_chart(trend_chart(df, "year", "war"), use_container_width=True)
