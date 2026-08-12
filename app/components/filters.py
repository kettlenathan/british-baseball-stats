"""Shared season/league/division filter widgets, used consistently across pages."""

import streamlit as st

from app.components.data_access import (
    all_player_names,
    all_team_names,
    list_divisions,
    list_league_seasons,
)

ALL_DIVISIONS = "All divisions"


def league_season_selector(key: str = "league_season") -> int | None:
    df = list_league_seasons()
    if df.empty:
        st.warning("No data scraped yet — run the scraper first (see Data Admin page).")
        return None

    df = df.copy()
    df["label"] = df["league_name"] + " (" + df["year"].astype(str) + ")"
    choice = st.selectbox("League / season", df["label"], key=key)
    return int(df.loc[df["label"] == choice, "league_season_id"].iloc[0])


def division_selector(league_season_id: int, key: str = "division") -> str | None:
    """Pick one of a league-season's divisions, or all of them.

    Returns the division *name* (matching the `division` column the
    data_access frames carry), or None for "all". Renders nothing at all
    when the league-season has fewer than two divisions — a season that was
    never split has no meaningful choice to offer, and 2021's NBL genuinely
    was one undivided table.
    """
    divisions = list_divisions(league_season_id)
    if len(divisions) < 2:
        return None

    labels = [ALL_DIVISIONS] + [
        f"{row.division} ({row.teams} teams)" for row in divisions.itertuples()
    ]
    choice = st.selectbox("Division", labels, key=key)
    if choice == ALL_DIVISIONS:
        return None
    return divisions.iloc[labels.index(choice) - 1]["division"]


def filter_by_division(df, division: str | None):
    """Narrow a data_access frame to one division, if one was chosen.

    Kept here rather than pushed into the queries so a page can switch
    divisions without invalidating the cached frame — one query serves every
    division of a league-season.
    """
    if division is None or df.empty or "division" not in df.columns:
        return df
    return df[df["division"] == division]


def player_multiselect(key: str = "player_compare") -> list[str]:
    return st.multiselect("Players", all_player_names(), key=key)


def team_multiselect(key: str = "team_compare") -> list[str]:
    # Capped at 10 to match TEAM_PALETTE's size (theme.py) — colors are
    # assigned positionally per chart, so past 10 selections two teams
    # would have to repeat the same color.
    return st.multiselect("Teams", all_team_names(), key=key, max_selections=10)
