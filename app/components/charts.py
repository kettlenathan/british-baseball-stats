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

from app.components.theme import MUTED, SURFACE, assign_colors, categorical_palette, chart_mode, stat_format, stat_label


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def scatter_chart(df: pd.DataFrame, x: str, y: str, color_col: str | None = "team", hover_name: str = "player"):
    mode = chart_mode()
    has_color = bool(color_col) and color_col in df.columns
    has_hover_name = hover_name in df.columns

    fig = px.scatter(
        df,
        x=x,
        y=y,
        color=color_col if has_color else None,
        color_discrete_map=assign_colors(df[color_col], mode) if has_color else None,
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
        color_discrete_map=assign_colors(df[color_col], mode) if has_color else None,
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
    color_map = assign_colors(df[name_col], mode)
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


def bar_chart(df: pd.DataFrame, x: str, y: str):
    """Single-stat comparison across categories in `x` (e.g. teams) — color
    matches each category's stable identity color, but since bars are already
    positionally labeled on the x-axis, a legend would be redundant and is
    left off (unlike overlapping marks, where color is the only cue)."""
    mode = chart_mode()
    color_map = assign_colors(df[x], mode)

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
