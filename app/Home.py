import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from sqlalchemy import func, select

from app.components.data_access import list_league_seasons
from app.env import is_deployed
from db.engine import get_session
from db.models import Game, League, Player, ScrapeLog, Team
from stats.war import WAR_DISCLAIMER


def render_home() -> None:
    st.set_page_config(page_title="British Baseball Stats Explorer", page_icon="⚾", layout="wide")

    st.title("⚾ British Baseball Stats Explorer")
    st.caption(
        "Player and team performance stats for British Baseball, scraped from "
        "stats.britishbaseball.org.uk, in the style of Baseball-Reference/FanGraphs."
    )

    session = get_session()
    try:
        n_leagues = session.execute(select(func.count()).select_from(League)).scalar()
        n_teams = session.execute(select(func.count()).select_from(Team)).scalar()
        n_players = session.execute(select(func.count()).select_from(Player)).scalar()
        n_games = session.execute(select(func.count()).select_from(Game)).scalar()
        last_scrape = session.execute(select(func.max(ScrapeLog.fetched_at))).scalar()
    finally:
        session.close()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Leagues", n_leagues or 0)
    col2.metric("Teams", n_teams or 0)
    col3.metric("Players", n_players or 0)
    col4.metric("Games", n_games or 0)

    if last_scrape:
        st.caption(f"Data last refreshed: {last_scrape} UTC")

    st.divider()

    st.subheader("Available leagues / seasons")
    df = list_league_seasons()
    if df.empty:
        st.info("No data scraped yet.")
    else:
        st.dataframe(df[["league_name", "year", "competition_slug"]], hide_index=True, use_container_width=True)

    st.divider()
    st.caption(f"⚠️ {WAR_DISCLAIMER}")


pages = [
    st.Page(render_home, title="Home", icon="🏠", default=True),
    st.Page("pages/1_Leaderboards.py", title="Leaderboards", icon="⚾"),
    st.Page("pages/2_Player_Explorer.py", title="Player Explorer", icon="📊"),
    st.Page("pages/3_Player_Page.py", title="Player Page", icon="🧑‍💼"),
    st.Page("pages/4_Team_Page.py", title="Team Page", icon="🏟️"),
    st.Page("pages/5_Player_Comparison.py", title="Player Comparison", icon="🆚"),
    st.Page("pages/6_Team_Comparison.py", title="Team Comparison", icon="📈"),
    st.Page("pages/7_Batter_Archetypes.py", title="Batter Archetypes", icon="🧬"),
    st.Page("pages/9_Methodology.py", title="Methodology", icon="📖"),
    st.Page("pages/10_Feedback.py", title="Feedback", icon="💬"),
]
if not is_deployed():
    pages.insert(8, st.Page("pages/8_Data_Admin.py", title="Data Admin", icon="🛠️"))

st.navigation(pages).run()
