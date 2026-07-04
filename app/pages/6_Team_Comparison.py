import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from app.components.charts import trend_chart
from app.components.data_access import team_history
from app.components.filters import team_multiselect

st.set_page_config(page_title="Team Comparison", page_icon="📈", layout="wide")
st.title("Team Comparison")
st.caption("Pick one team to see its year-over-year trend, or two or more to compare them against each other.")

teams = team_multiselect()
if not teams:
    st.info("Pick at least one team.")
    st.stop()

df = team_history(teams)
if df.empty:
    st.info("No completed games found for the selected team(s).")
    st.stop()

st.dataframe(
    df,
    hide_index=True,
    use_container_width=True,
    column_config={"pct": st.column_config.NumberColumn(format="%.3f")},
)

st.plotly_chart(trend_chart(df, "year", "pct", color_col="team"), use_container_width=True)
