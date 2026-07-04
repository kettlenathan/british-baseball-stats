"""Shared season/league filter widget, used consistently across pages."""

import streamlit as st

from app.components.data_access import all_player_names, all_team_names, list_league_seasons


def league_season_selector(key: str = "league_season") -> int | None:
    df = list_league_seasons()
    if df.empty:
        st.warning("No data scraped yet — run the scraper first (see Data Admin page).")
        return None

    df = df.copy()
    df["label"] = df["league_name"] + " (" + df["year"].astype(str) + ")"
    choice = st.selectbox("League / season", df["label"], key=key)
    return int(df.loc[df["label"] == choice, "league_season_id"].iloc[0])


def player_multiselect(key: str = "player_compare") -> list[str]:
    return st.multiselect("Players", all_player_names(), key=key)


def team_multiselect(key: str = "team_compare") -> list[str]:
    return st.multiselect("Teams", all_team_names(), key=key)
