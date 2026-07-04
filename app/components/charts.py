"""Shared Plotly figure builders — kept in one place so charts across pages
look consistent (color-by-team, hover template)."""

import pandas as pd
import plotly.express as px


def scatter_chart(df: pd.DataFrame, x: str, y: str, color_col: str | None = "team", hover_name: str = "player"):
    fig = px.scatter(
        df,
        x=x,
        y=y,
        color=color_col if color_col in df.columns else None,
        hover_name=hover_name if hover_name in df.columns else None,
        hover_data={c: True for c in df.columns if c not in (x, y, color_col, hover_name)},
    )
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10))
    return fig


def trend_chart(df: pd.DataFrame, x: str, y: str, color_col: str | None = None):
    fig = px.line(df, x=x, y=y, color=color_col if color_col in df.columns else None, markers=True)
    fig.update_layout(margin=dict(l=10, r=10, t=30, b=10))
    return fig
