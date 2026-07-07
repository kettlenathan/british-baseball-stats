"""Shared st.column_config formatting for stat tables, so every page that
displays batting/pitching/win-pct stats rounds them the same way, and every
column header shows its properly-cased label (theme.stat_label is the one
source of truth for that — never a second hardcoded string here) rather
than a raw lowercase DataFrame column name. Keys not present in a given
DataFrame are simply ignored by st.dataframe, so these dicts can be passed
in full regardless of which stat columns a page has."""

import streamlit as st

from app.components.theme import stat_label

_TEXT_COLS = ["player", "team", "league", "opponent", "position", "home_away", "result", "score"]
_DATE_COLS = ["game_date"]
_FORMATTED_COLS = {
    "avg": "%.3f", "obp": "%.3f", "slg": "%.3f", "ops": "%.3f", "iso": "%.3f", "woba": "%.3f",
    "wrc_plus": "%.0f", "war": "%.2f", "bb_pct": "percent", "k_pct": "percent",
    "era": "%.2f", "whip": "%.2f", "k9": "%.1f", "bb9": "%.1f", "fip": "%.2f",
    "era_plus": "%.0f", "ip": "%.1f", "fpct": "%.3f", "avg_risp": "%.3f",
    "fps_pct": "percent", "pct": "%.3f", "r_pg": "%.2f", "ra_pg": "%.2f", "lob_pg": "%.2f",
}


def _build(cols: list[str]) -> dict:
    config = {}
    for col in cols:
        label = stat_label(col)
        if col in _FORMATTED_COLS:
            config[col] = st.column_config.NumberColumn(label, format=_FORMATTED_COLS[col])
        elif col in _TEXT_COLS:
            config[col] = st.column_config.TextColumn(label)
        elif col in _DATE_COLS:
            config[col] = st.column_config.DateColumn(label, format="D MMM YYYY")
        else:
            config[col] = st.column_config.NumberColumn(label, format="%d")
    return config


BATTING_COLUMN_CONFIG = _build(
    [
        "player", "team", "league", "year", "pa", "ab", "h", "doubles", "triples", "hr", "rbi", "bb", "so", "sb",
        "avg", "obp", "slg", "ops", "iso", "bb_pct", "k_pct", "woba", "wrc_plus", "war",
        "po", "a", "e", "dp", "fpct", "avg_risp",
    ]
)

PITCHING_COLUMN_CONFIG = _build(
    [
        "player", "team", "league", "year", "w", "l", "sv", "so", "bb", "h", "er", "ip",
        "era", "whip", "k9", "bb9", "fip", "era_plus", "fps_pct", "war",
    ]
)

PCT_COLUMN_CONFIG = _build(["team", "w", "l", "t", "pct"])

TEAM_COLUMN_CONFIG = _build(
    [
        "team", "w", "l", "t", "pct", "r_pg", "ra_pg", "lob_pg",
        "avg", "obp", "slg", "ops", "iso", "bb_pct", "k_pct", "woba", "wrc_plus", "fpct", "avg_risp",
        "era", "whip", "fip", "era_plus", "war",
    ]
)

ROSTER_COLUMN_CONFIG = _build(["player", "position", "jersey_number"])

MATCHUP_COLUMN_CONFIG = _build(["opponent", "pa", "ab", "h", "doubles", "triples", "hr", "bb", "so", "hbp", "avg"])

RECENT_GAMES_COLUMN_CONFIG = _build(["game_date", "opponent", "home_away", "score", "result"])
