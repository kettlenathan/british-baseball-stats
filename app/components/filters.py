"""Shared season/league filter widget, used consistently across pages."""

import streamlit as st

from app.components.data_access import list_league_seasons


def league_season_selector(key: str = "league_season") -> int | None:
    df = list_league_seasons()
    if df.empty:
        st.warning("No data scraped yet — run the scraper first (see Data Admin page).")
        return None

    df = df.copy()
    df["label"] = df["league_name"] + " (" + df["year"].astype(str) + ")"
    choice = st.selectbox("League / season", df["label"], key=key)
    return int(df.loc[df["label"] == choice, "league_season_id"].iloc[0])
