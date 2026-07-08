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

# Eight hues, ordered for maximum adjacent colorblind-safe separation —
# checked against a Python port of this repo's dataviz-skill validator
# (OKLCH lightness band, chroma floor, Machado-2009 CVD deltaE at both
# adjacent and all-pairs spacing, since teams/players sharing one scatter or
# radar chart can sit next to any other, not just their neighbor in a list).
# Light/dark are the same hue family stepped for their own chart surface —
# pick by chart_mode() at render time.
CATEGORICAL = {
    "light": ["#2a78d6", "#1baf7a", "#eda100", "#b22290", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"],
    "dark": ["#3987e5", "#199e70", "#c98500", "#ba2897", "#9085e9", "#e66767", "#d55181", "#d95926"],
}

SURFACE = {"light": "#fcfcfb", "dark": "#1a1a19"}
MUTED = "#898781"

# Fixed semantic colors for batted-ball outcomes — deliberately NOT derived
# from assign_colors' "alphabetical among what's present" logic, so a
# player who's only ever hit a single and a double still shows those in the
# same colors a slugger's Home Run/Triple/Double/Single legend would use.
# "Out" is muted rather than a hue — it's not a batted-ball "result" worth
# the same visual weight as a hit.
OUTCOME_COLORS = {
    "light": {"Home Run": "#e34948", "Triple": "#eb6834", "Double": "#2a78d6", "Single": "#1baf7a", "Out": MUTED},
    "dark": {"Home Run": "#e66767", "Triple": "#d95926", "Double": "#3987e5", "Single": "#199e70", "Out": MUTED},
}

# Ten hues for team charts (Team Comparison allows selecting up to that many
# at once) — the 8-color CATEGORICAL set above plus a moss green and a teal,
# picked to clear the same lightness/chroma/CVD bar as CATEGORICAL rather
# than just appended. Color-only, deliberately: an earlier version also
# varied fill pattern/line dash/marker shape per team, which read as busy
# and amateurish rather than as a considered palette — a bespoke, larger
# color set is the more professional fix. Assigned positionally per chart
# (see assign_colors), not hashed per team name — a hash can hand two teams
# in the same small selection visually-similar neighboring hues, whereas
# positional assignment always hands whatever's on screen the most
# distinct colors available for that count. The tradeoff is a team's color
# can shift between charts that show a different subset of teams.
TEAM_PALETTE = {
    "light": [
        "#2a78d6", "#1baf7a", "#eda100", "#b22290", "#4a3aa7",
        "#e34948", "#e87ba4", "#eb6834", "#5b6c0e", "#188e77",
    ],
    "dark": [
        "#3987e5", "#199e70", "#c98500", "#ba2897", "#9085e9",
        "#e66767", "#d55181", "#d95926", "#4f8d20", "#2da39d",
    ],
}

# Two-hue "heat" scale for the spray heatmap — most batted balls read hot
# (red), fewest read cold (blue), bridged by a neutral midpoint. This is a
# deliberate departure from the usual one-hue sequential ramp: it's the
# explicitly requested convention for that one chart, not the general rule
# for magnitude encodings elsewhere in the app.
HEAT = {
    "light": ["#1c5aa8", "#f2ede3", "#b3261e"],
    "dark": ["#2f5a8c", "#4a4844", "#e2503f"],
}

STAT_LABELS = {
    "year": "Year", "pa": "PA", "ab": "AB", "h": "H", "doubles": "2B", "triples": "3B",
    "hr": "HR", "rbi": "RBI", "bb": "BB", "so": "SO", "sb": "SB", "hbp": "HBP",
    "avg": "AVG", "obp": "OBP", "slg": "SLG", "ops": "OPS", "iso": "ISO",
    "woba": "wOBA", "wrc_plus": "wRC+", "war": "WAR",
    "bb_pct": "BB%", "k_pct": "K%",
    "w": "W", "l": "L", "t": "T", "sv": "SV", "er": "ER", "ip": "IP",
    "era": "ERA", "whip": "WHIP", "k9": "K/9", "bb9": "BB/9",
    "fip": "FIP", "era_plus": "ERA+",
    "pct": "Win %", "team": "Team", "player": "Player", "league": "League",
    "opponent": "Opponent", "position": "Position", "jersey_number": "No.",
    "po": "PO", "a": "A", "e": "E", "dp": "DP", "fpct": "FPCT",
    "avg_risp": "AVG w/RISP", "r_pg": "R/G", "ra_pg": "RA/G", "lob_pg": "LOB/G",
    "fps_pct": "First Pitch Strike%", "hitdistance": "Distance", "outcome": "Outcome",
    "game_date": "Date", "home_away": "Home/Away", "score": "Score", "result": "Result",
    "observed_woba": "Observed wOBA", "shrunk_woba": "True Talent wOBA",
    "observed_fip": "Observed FIP", "shrunk_fip": "True Talent FIP",
    "reliability": "Reliability",
    "pull_pct": "Pull%", "center_pct": "Center%", "oppo_pct": "Oppo%", "pull_minus_oppo": "Net Pull%",
    "singles_pct": "1B%", "doubles_pct": "2B%", "triples_pct": "3B%", "hr_pct": "HR%",
    "cluster": "Cluster", "cluster_label": "Archetype", "pc1": "Component 1", "pc2": "Component 2",
    "count": "Players", "k": "k", "inertia": "Inertia", "silhouette": "Silhouette",
}

STAT_FORMAT = {
    "avg": ".3f", "obp": ".3f", "slg": ".3f", "ops": ".3f", "iso": ".3f", "woba": ".3f",
    "wrc_plus": ".0f", "war": ".2f", "bb_pct": ".1%", "k_pct": ".1%",
    "era": ".2f", "whip": ".2f", "k9": ".1f", "bb9": ".1f", "fip": ".2f",
    "era_plus": ".0f", "ip": ".1f", "pct": ".3f", "year": ".0f",
    "pa": ",.0f", "ab": ",.0f", "h": ",.0f", "hr": ",.0f", "rbi": ",.0f",
    "bb": ",.0f", "so": ",.0f", "sb": ",.0f", "doubles": ",.0f", "triples": ",.0f", "hbp": ",.0f",
    "w": ",.0f", "l": ",.0f", "t": ",.0f", "sv": ",.0f", "er": ",.0f",
    "po": ",.0f", "a": ",.0f", "e": ",.0f", "dp": ",.0f", "fpct": ".3f",
    "avg_risp": ".3f", "r_pg": ".2f", "ra_pg": ".2f", "lob_pg": ".2f",
    "fps_pct": ".1%", "hitdistance": ",.0f",
    "observed_woba": ".3f", "shrunk_woba": ".3f", "observed_fip": ".2f", "shrunk_fip": ".2f",
    "reliability": ".0%",
    "pull_pct": ".1%", "center_pct": ".1%", "oppo_pct": ".1%", "pull_minus_oppo": "+.1%",
    "singles_pct": ".1%", "doubles_pct": ".1%", "triples_pct": ".1%", "hr_pct": ".1%",
    "pc1": ".2f", "pc2": ".2f", "silhouette": ".3f", "inertia": ".1f",
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


def outcome_color_map(mode: str | None = None) -> dict[str, str]:
    return OUTCOME_COLORS[mode or chart_mode()]


def heat_colorscale(mode: str | None = None) -> list[list]:
    colors = HEAT[mode or chart_mode()]
    n = len(colors) - 1
    return [[i / n, c] for i, c in enumerate(colors)]


def assign_colors(
    categories, mode: str | None = None, universe: list[str] | None = None, palette: list[str] | None = None
) -> dict[str, str]:
    """Color per category, assigned positionally (alphabetically-sorted
    among whatever's actually present) so a small selection always gets the
    most mutually-distinct colors the palette has, rather than being
    scattered across the full palette by a per-name hash — the tradeoff is
    a category's color can shift between two charts that show a different
    subset. Pass `universe` (a fixed, complete ordering — e.g. every
    possible outcome label) when a category's color must mean the same
    thing everywhere it appears instead. Pass `palette` to draw from
    something other than the general CATEGORICAL set (e.g. TEAM_PALETTE)."""
    colors = palette if palette is not None else categorical_palette(mode)
    ordered = universe if universe is not None else sorted(set(categories))
    return {cat: colors[i % len(colors)] for i, cat in enumerate(ordered)}
