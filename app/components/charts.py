"""Shared Plotly figure builders — kept in one place so charts across pages
look consistent (color-by-entity, hover template, marks). Background,
gridlines, and font color are deliberately left unset: st.plotly_chart's
default "streamlit" theme re-colors those to match the app's light/dark
setting on its own. Only the palette module needs to know which mode is
active, since our own trace colors are explicit and not touched by that
theme wrapper."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from app.components.theme import (
    MUTED,
    SURFACE,
    TEAM_PALETTE,
    assign_colors,
    categorical_palette,
    chart_mode,
    heat_colorscale,
    outcome_color_map,
    stat_format,
    stat_label,
)


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def _color_map(categories, color_col: str, mode: str) -> dict[str, str]:
    """color_discrete_map for the given column — teams draw positionally
    from the bespoke 10-color TEAM_PALETTE, everything else positionally
    from the general categorical palette. Positional (not hashed per name)
    so whatever's actually shown gets the most mutually-distinct colors
    available, at the cost of a team's color not being fixed across charts
    that show a different subset."""
    if color_col == "team":
        return assign_colors(categories, mode, palette=TEAM_PALETTE[mode])
    return assign_colors(categories, mode)


def scatter_chart(df: pd.DataFrame, x: str, y: str, color_col: str | None = "team", hover_name: str = "player"):
    mode = chart_mode()
    has_color = bool(color_col) and color_col in df.columns
    has_hover_name = hover_name in df.columns

    fig = px.scatter(
        df,
        x=x,
        y=y,
        color=color_col if has_color else None,
        color_discrete_map=_color_map(df[color_col], color_col, mode) if has_color else None,
        color_discrete_sequence=None if has_color else [categorical_palette(mode)[0]],
        hover_name=hover_name if has_hover_name else None,
    )

    def _style_trace(trace):
        trace.marker.update(size=10, opacity=0.85, line=dict(width=2, color=SURFACE[mode]))
        name_line = f"{stat_label(color_col)}: {trace.name}<br>" if has_color else ""
        trace.hovertemplate = (
            f"<b>%{{hovertext}}</b><br>{name_line}"
            f"{stat_label(x)}: %{{x:{stat_format(x)}}}<br>"
            f"{stat_label(y)}: %{{y:{stat_format(y)}}}<extra></extra>"
        )

    fig.for_each_trace(_style_trace)

    if len(df) > 1:
        fig.add_vline(x=df[x].mean(), line_width=1, line_dash="dot", line_color=MUTED, opacity=0.7)
        fig.add_hline(y=df[y].mean(), line_width=1, line_dash="dot", line_color=MUTED, opacity=0.7)

    fig.update_xaxes(title=stat_label(x))
    fig.update_yaxes(title=stat_label(y))
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=has_color,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        legend_title_text=stat_label(color_col) if has_color else None,
        hoverlabel=dict(bgcolor=SURFACE[mode], font_size=13),
    )
    return fig


def trend_chart(df: pd.DataFrame, x: str, y: str, color_col: str | None = None, reference_y: float | None = None):
    mode = chart_mode()
    has_color = bool(color_col) and color_col in df.columns

    fig = px.line(
        df,
        x=x,
        y=y,
        color=color_col if has_color else None,
        color_discrete_map=_color_map(df[color_col], color_col, mode) if has_color else None,
        color_discrete_sequence=None if has_color else [categorical_palette(mode)[0]],
        markers=True,
    )

    fig.update_traces(
        line=dict(width=2),
        marker=dict(size=9, line=dict(width=2, color=SURFACE[mode])),
        hovertemplate=f"%{{y:{stat_format(y)}}}<extra></extra>",
    )

    if reference_y is not None:
        fig.add_hline(
            y=reference_y, line_width=1, line_dash="dot", line_color=MUTED, opacity=0.7,
            annotation_text=f"{reference_y:{stat_format(y)}}", annotation_position="top left",
            annotation_font_color=MUTED,
        )

    fig.update_xaxes(title=stat_label(x), dtick=1 if x == "year" else None)
    fig.update_yaxes(title=stat_label(y), tickformat=stat_format(y))
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=has_color,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        legend_title_text=stat_label(color_col) if has_color else None,
        hovermode="x unified",
        hoverlabel=dict(bgcolor=SURFACE[mode], font_size=13),
    )
    return fig


def radar_chart(df: pd.DataFrame, value_cols: list[str], name_col: str = "team"):
    """One closed polygon per row (e.g. per team), across value_cols — values
    are expected pre-normalized to a single shared 0-100 scale (percentile
    rank within the wider population, oriented so higher is always better;
    see Team_Comparison.py), since raw stats of very different units/scales
    can't share one radial axis (the "one axis" rule applies to radar spokes
    the same as any other chart)."""
    mode = chart_mode()
    color_map = _color_map(df[name_col], name_col, mode)
    labels = [stat_label(c) for c in value_cols]
    labels_closed = labels + labels[:1]

    fig = go.Figure()
    for _, row in df.iterrows():
        name = row[name_col]
        values = [row[c] for c in value_cols]
        values_closed = values + values[:1]
        color = color_map[name]
        fig.add_trace(
            go.Scatterpolar(
                r=values_closed,
                theta=labels_closed,
                name=name,
                line=dict(color=color, width=2),
                marker=dict(size=7, color=color, line=dict(width=1, color=SURFACE[mode])),
                fill="toself",
                fillcolor=_hex_to_rgba(color, 0.15),
                hovertemplate=f"<b>{name}</b><br>%{{theta}}: %{{r:.0f}}th percentile<extra></extra>",
            )
        )

    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100], ticksuffix="")),
        margin=dict(l=40, r=40, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="left", x=0),
        hoverlabel=dict(bgcolor=SURFACE[mode], font_size=13),
    )
    return fig


# Fair territory spans exactly 90 degrees, foul line to foul line, in any
# real ballpark — home plate is the vertex, center field bisects it. Treating
# `hitpull` as degrees off dead-center and clamping to +/-45 keeps every
# point inside that true fan; ~1% of raw values sit past 45 (scoring/rounding
# noise beyond the boundary, not real territory) and get pinned to whichever
# foul line they overshot rather than plotted outside the field.
PULL_FAN_HALF_WIDTH_DEGREES = 45


def _pull_to_theta(pull: pd.Series) -> pd.Series:
    """Shared by spray_chart and spray_heatmap so both agree on what angle a
    given `hitpull` value plots at."""
    return 90 - pull.clip(-PULL_FAN_HALF_WIDTH_DEGREES, PULL_FAN_HALF_WIDTH_DEGREES)


def spray_chart(df: pd.DataFrame, pull_col: str = "hitpull", distance_col: str = "hitdistance", color_col: str = "outcome"):
    """Radial spray-chart approximation. This league's box scores never
    record true batted-ball x/y coordinates (see CLAUDE.md's "no batted-ball
    tracking data" note) — only a directional pull value (`pull_col`, raw
    and unadjusted: negative = left/third-base side, positive =
    right/first-base side) and a hit distance (`distance_col`). `theta` is a
    linear mapping of the pull value onto a 90-degree fan centered on
    straightaway center field (matching a real ballpark's fair-territory
    width); `r` is hit distance. This is an approximation, not a to-scale
    field diagram — the closest the data supports."""
    mode = chart_mode()
    has_color = bool(color_col) and color_col in df.columns

    fig = go.Figure()
    if df.empty:
        fig.update_layout(polar=dict(bgcolor=SURFACE[mode], radialaxis=dict(visible=True)), margin=dict(l=20, r=20, t=20, b=20))
        return fig

    color_map = outcome_color_map(mode) if (has_color and color_col == "outcome") else (assign_colors(df[color_col], mode) if has_color else {})
    max_distance = df[distance_col].max()
    groups = df.groupby(color_col) if has_color else [(None, df)]
    for name, group in groups:
        theta = _pull_to_theta(group[pull_col])
        color = color_map.get(name, categorical_palette(mode)[0])
        label = str(name) if name is not None else stat_label(distance_col)
        fig.add_trace(
            go.Scatterpolar(
                r=group[distance_col],
                theta=theta,
                mode="markers",
                name=label,
                marker=dict(size=8, color=color, opacity=0.8, line=dict(width=1, color=SURFACE[mode])),
                hovertemplate=f"<b>{label}</b><br>{stat_label(distance_col)}: %{{r:,.0f}}<extra></extra>",
            )
        )

    fig.update_layout(
        polar=dict(
            bgcolor=SURFACE[mode],
            sector=[0, 180],
            radialaxis=dict(visible=True, range=[0, max_distance * 1.1 if max_distance else 1]),
            angularaxis=dict(rotation=0, direction="counterclockwise", showticklabels=False, ticks=""),
        ),
        margin=dict(l=20, r=20, t=20, b=20),
        showlegend=has_color,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hoverlabel=dict(bgcolor=SURFACE[mode], font_size=13),
    )
    return fig


def spray_heatmap(df: pd.DataFrame, pull_col: str = "hitpull", bins: int = 9):
    """Direction-only counterpart to spray_chart, drawn on a schematic (not
    to scale) baseball field. `hitdistance` has real garbage in it on this
    league's box scores (a small number of rows are negative — see
    CLAUDE.md), so this chart drops distance entirely: every wedge spans the
    same fixed depth from infield to fence, and color is the only thing that
    varies, by how many batted balls landed in that slice of the true
    90-degree fair-territory fan. Shares `_pull_to_theta`'s +/-45 clamp with
    spray_chart, so a wedge here always lines up with where the same ball
    would land there."""
    mode = chart_mode()
    fig = go.Figure()
    adjusted = df[pull_col].dropna().clip(-PULL_FAN_HALF_WIDTH_DEGREES, PULL_FAN_HALF_WIDTH_DEGREES) if pull_col in df.columns else pd.Series(dtype=float)
    if adjusted.empty:
        fig.update_layout(polar=dict(bgcolor=SURFACE[mode], radialaxis=dict(visible=False)), margin=dict(l=20, r=20, t=20, b=20))
        return fig

    outer_r, infield_r = 1.0, 0.15
    bin_width = 2 * PULL_FAN_HALF_WIDTH_DEGREES / bins
    edges = [-PULL_FAN_HALF_WIDTH_DEGREES + i * bin_width for i in range(bins + 1)]
    counts = pd.cut(adjusted, bins=edges, include_lowest=True).value_counts(sort=False)
    total = counts.sum()

    centers = [(iv.left + iv.right) / 2 for iv in counts.index]
    thetas = [90 - c for c in centers]
    customdata = [
        [f"{abs(c):.0f}° toward {'1B' if c > 0 else '3B' if c < 0 else 'CF'}", (n / total * 100) if total else 0]
        for c, n in zip(centers, counts.values)
    ]

    fig.add_trace(
        go.Barpolar(
            r=[outer_r - infield_r] * len(counts),
            theta=thetas,
            width=[bin_width] * len(counts),
            base=infield_r,
            marker=dict(
                color=counts.values,
                colorscale=heat_colorscale(mode),
                cmin=0,
                showscale=True,
                colorbar=dict(title=dict(text="Batted balls", side="right"), thickness=14, len=0.7),
                line=dict(width=1, color=SURFACE[mode]),
            ),
            customdata=customdata,
            hovertemplate="<b>%{customdata[0]}</b><br>Batted balls: %{marker.color:.0f} (%{customdata[1]:.0f}%)<extra></extra>",
        )
    )

    # Schematic field outline (foul lines, an outfield fence arc, and a
    # basepath diamond) — enough geometry to read as a ballpark, not a
    # to-scale rendering, since fixed proportions stand in for real
    # (unreliable) distance data.
    line_style = dict(mode="lines", hoverinfo="skip", showlegend=False)
    fig.add_trace(go.Scatterpolar(r=[0, outer_r], theta=[45, 45], line=dict(color=MUTED, width=1.5), **line_style))
    fig.add_trace(go.Scatterpolar(r=[0, outer_r], theta=[135, 135], line=dict(color=MUTED, width=1.5), **line_style))
    fence_theta = [45 + i * (90 / 60) for i in range(61)]
    fig.add_trace(go.Scatterpolar(r=[outer_r] * len(fence_theta), theta=fence_theta, line=dict(color=MUTED, width=1.5), **line_style))
    fig.add_trace(
        go.Scatterpolar(
            r=[0, infield_r, infield_r * 2**0.5, infield_r, 0],
            theta=[90, 45, 90, 135, 90],
            line=dict(color=MUTED, width=1),
            **line_style,
        )
    )

    fig.update_layout(
        polar=dict(
            bgcolor=SURFACE[mode],
            sector=[0, 180],
            radialaxis=dict(visible=False, showline=False, range=[0, outer_r * 1.05]),
            angularaxis=dict(
                type="linear", rotation=0, direction="counterclockwise",
                showticklabels=False, ticks="", showline=False, showgrid=False,
            ),
        ),
        margin=dict(l=20, r=20, t=20, b=20),
        showlegend=False,
        hoverlabel=dict(bgcolor=SURFACE[mode], font_size=13),
    )
    return fig


def bar_chart(df: pd.DataFrame, x: str, y: str):
    """Single-stat comparison across categories in `x` (e.g. teams) — color
    matches each category's stable identity color, but since bars are already
    positionally labeled on the x-axis, a legend would be redundant and is
    left off (unlike overlapping marks, where color is the only cue)."""
    mode = chart_mode()
    color_map = _color_map(df[x], x, mode)

    fig = px.bar(df, x=x, y=y, color=x, color_discrete_map=color_map)
    fig.update_traces(
        marker_line_width=0,
        hovertemplate=f"<b>%{{x}}</b><br>{stat_label(y)}: %{{y:{stat_format(y)}}}<extra></extra>",
    )
    fig.update_xaxes(title=None)
    fig.update_yaxes(title=None, tickformat=stat_format(y))
    fig.update_layout(
        showlegend=False,
        margin=dict(l=10, r=10, t=30, b=10),
        title=dict(text=stat_label(y), font=dict(size=13), x=0.02, xanchor="left"),
        hoverlabel=dict(bgcolor=SURFACE[mode], font_size=13),
    )
    return fig
