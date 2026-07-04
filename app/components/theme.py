"""Chart theming shared across every Plotly figure in the app: a validated,
colorblind-safe categorical palette (fixed hue order, light/dark variants),
stat display labels/number formats, and a deterministic color assignment so
a given player/team keeps the same color across charts regardless of
selection order. Background, gridlines, and fonts are deliberately left
unset in the figures that use this module — Streamlit's own "streamlit"
plotly theme (the st.plotly_chart default) re-colors those to match the
app's light/dark setting automatically; only explicit per-trace colors
(the palette below) need to pick a light/dark variant themselves."""

import streamlit as st

# Eight hues, ordered for maximum adjacent colorblind-safe separation.
# Light/dark are the same hues stepped for their own chart surface, not
# separate palettes — pick by chart_mode() at render time.
CATEGORICAL = {
    "light": ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"],
    "dark": ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"],
}

SURFACE = {"light": "#fcfcfb", "dark": "#1a1a19"}
MUTED = "#898781"

STAT_LABELS = {
    "year": "Year", "pa": "PA", "ab": "AB", "h": "H", "doubles": "2B", "triples": "3B",
    "hr": "HR", "rbi": "RBI", "bb": "BB", "so": "SO", "sb": "SB",
    "avg": "AVG", "obp": "OBP", "slg": "SLG", "ops": "OPS", "iso": "ISO",
    "woba": "wOBA", "wrc_plus": "wRC+", "war": "WAR",
    "bb_pct": "BB%", "k_pct": "K%",
    "w": "W", "l": "L", "sv": "SV", "er": "ER", "ip": "IP",
    "era": "ERA", "whip": "WHIP", "k9": "K/9", "bb9": "BB/9",
    "fip": "FIP", "era_plus": "ERA+",
    "pct": "Win %", "team": "Team", "player": "Player", "league": "League",
    "po": "PO", "a": "A", "e": "E", "dp": "DP", "fpct": "FPCT",
    "avg_risp": "AVG w/RISP", "r_pg": "R/G", "ra_pg": "RA/G", "lob_pg": "LOB/G",
}

STAT_FORMAT = {
    "avg": ".3f", "obp": ".3f", "slg": ".3f", "ops": ".3f", "iso": ".3f", "woba": ".3f",
    "wrc_plus": ".0f", "war": ".2f", "bb_pct": ".1%", "k_pct": ".1%",
    "era": ".2f", "whip": ".2f", "k9": ".1f", "bb9": ".1f", "fip": ".2f",
    "era_plus": ".0f", "ip": ".1f", "pct": ".3f", "year": ".0f",
    "pa": ",.0f", "ab": ",.0f", "h": ",.0f", "hr": ",.0f", "rbi": ",.0f",
    "bb": ",.0f", "so": ",.0f", "sb": ",.0f", "doubles": ",.0f", "triples": ",.0f",
    "w": ",.0f", "l": ",.0f", "sv": ",.0f", "er": ",.0f",
    "po": ",.0f", "a": ",.0f", "e": ",.0f", "dp": ",.0f", "fpct": ".3f",
    "avg_risp": ".3f", "r_pg": ".2f", "ra_pg": ".2f", "lob_pg": ".2f",
}


def stat_label(col: str) -> str:
    return STAT_LABELS.get(col, col.replace("_", " ").title())


def stat_format(col: str) -> str:
    return STAT_FORMAT.get(col, ".2f")


def chart_mode() -> str:
    """'light' or 'dark', matching the viewer's current Streamlit app theme."""
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def categorical_palette(mode: str | None = None) -> list[str]:
    return CATEGORICAL[mode or chart_mode()]


def assign_colors(categories, mode: str | None = None) -> dict[str, str]:
    """Stable color per category, keyed off the alphabetically-sorted set —
    so a player/team's color stays put across charts and re-renders instead
    of shifting whenever the selection or row order changes."""
    palette = categorical_palette(mode)
    ordered = sorted(set(categories))
    return {cat: palette[i % len(palette)] for i, cat in enumerate(ordered)}
