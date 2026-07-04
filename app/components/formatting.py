"""Shared st.column_config formatting for stat tables, so every page that
displays batting/pitching/win-pct stats rounds them the same way. Keys not
present in a given DataFrame are simply ignored by st.dataframe, so these
dicts can be passed in full regardless of which stat columns a page has."""

import streamlit as st

BATTING_COLUMN_CONFIG = {
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
}

PITCHING_COLUMN_CONFIG = {
    "era": st.column_config.NumberColumn(format="%.2f"),
    "whip": st.column_config.NumberColumn(format="%.2f"),
    "k9": st.column_config.NumberColumn("K/9", format="%.1f"),
    "bb9": st.column_config.NumberColumn("BB/9", format="%.1f"),
    "fip": st.column_config.NumberColumn(format="%.2f"),
    "era_plus": st.column_config.NumberColumn("ERA+", format="%.0f"),
    "war": st.column_config.NumberColumn("WAR", format="%.2f"),
    "ip": st.column_config.NumberColumn("IP", format="%.1f"),
}

PCT_COLUMN_CONFIG = {
    "pct": st.column_config.NumberColumn(format="%.3f"),
}
