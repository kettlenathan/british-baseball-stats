"""PDF generation for the Scouting Report page.

Presentation-only: consumes plain values/DataFrames the page assembled from
data_access, never opens a DB session. Charts are re-rendered with
matplotlib (Agg) rather than exported from the app's Plotly figures —
Plotly static export needs a Chrome binary via kaleido, which the Community
Cloud deployment can't reliably provide, while matplotlib + reportlab are
plain wheel installs. The spray fan mirrors app/components/charts.py's
geometry (same +/-45-degree clamp via PULL_FAN_HALF_WIDTH_DEGREES) and
takes its colors from theme.py, so the PDF reads as the same product as the
app. Light palette only — PDFs are print-first.
"""

import datetime as dt
import io
import math

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.components.charts import PULL_FAN_HALF_WIDTH_DEGREES  # noqa: E402
from app.components.theme import MUTED, OUTCOME_COLORS, stat_format, stat_label  # noqa: E402
from stats.war import WAR_DISCLAIMER  # noqa: E402

_ACCENT = "#2a78d6"  # theme.py CATEGORICAL light blue
_LIGHT_ROW = colors.HexColor("#f2f0eb")
_HEADER_BG = colors.HexColor(_ACCENT)

_OUTCOME_ORDER = ["Home Run", "Triple", "Double", "Single", "Out"]

_styles = getSampleStyleSheet()
_TITLE = ParagraphStyle("ScoutTitle", parent=_styles["Title"], fontSize=20, spaceAfter=4)
_H2 = ParagraphStyle(
    "ScoutH2", parent=_styles["Heading2"], textColor=colors.HexColor(_ACCENT), spaceBefore=10, spaceAfter=4
)
_H3 = ParagraphStyle("ScoutH3", parent=_styles["Heading3"], spaceBefore=6, spaceAfter=2)
_BODY = ParagraphStyle("ScoutBody", parent=_styles["BodyText"], fontSize=9, leading=12)
_SMALL = ParagraphStyle("ScoutSmall", parent=_styles["BodyText"], fontSize=7.5, leading=9.5, textColor=colors.grey)


def _fmt(col: str, value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA:
        return "—"
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%d %b")
    spec = stat_format(col)
    try:
        return format(value, spec)
    except (TypeError, ValueError):
        return str(value)


def _df_table(df: pd.DataFrame, cols: list[str], highlight: str | None = None) -> Table:
    """A compact reportlab table from DataFrame columns, headers via
    theme.stat_label and numbers via theme.stat_format — the same display
    conventions as the app's own tables."""
    header = [stat_label(c) for c in cols]
    rows = [[_fmt(c, row[c]) if c in row else "—" for c in cols] for _, row in df.iterrows()]
    table = Table([header] + rows, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT_ROW]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey),
    ]
    if highlight is not None:
        for i, (_, row) in enumerate(df.iterrows(), start=1):
            if highlight in (row.get("player"), row.get("team")):
                style.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return table


# --------------------------------------------------------------------------
# Spray chart rendering
# --------------------------------------------------------------------------


def spray_chart_png(points: pd.DataFrame, title: str) -> bytes:
    """One batter's (or pitcher's) balls in play on the same 90-degree
    fair-territory fan as charts.py's spray_chart: theta is the clamped
    pull value mapped so dead-center points straight up, r is hit distance
    (floored at 0 — a handful of raw distances are negative garbage)."""
    fig = plt.figure(figsize=(3.0, 2.4), dpi=150)
    ax = fig.add_subplot(projection="polar")
    ax.set_thetamin(45)
    ax.set_thetamax(135)
    ax.set_facecolor("#fcfcfb")

    palette = OUTCOME_COLORS["light"]
    if points.empty:
        ax.text(
            math.radians(90), 0.5, "No batted-ball data", ha="center", va="center", fontsize=7, color=MUTED
        )
        ax.set_rticks([])
    else:
        pull = points["hitpull"].clip(-PULL_FAN_HALF_WIDTH_DEGREES, PULL_FAN_HALF_WIDTH_DEGREES)
        theta = (90 - pull) * math.pi / 180
        r = points["hitdistance"].fillna(0).clip(lower=0)
        for outcome in _OUTCOME_ORDER:
            mask = points["outcome"] == outcome
            if not mask.any():
                continue
            ax.scatter(
                theta[mask], r[mask], s=12, color=palette[outcome], alpha=0.85,
                edgecolors="white", linewidths=0.4, label=outcome, zorder=3,
            )
        ax.set_rlim(0, max(float(r.max()) * 1.1, 1.0))
        ax.set_rticks([])
        ax.legend(loc="upper right", bbox_to_anchor=(1.32, 1.1), fontsize=5, frameon=False, handletextpad=0.2)

    ax.set_xticks([])
    ax.grid(False)
    ax.spines["polar"].set_color(MUTED)
    # Foul lines: the fan's own 45/135-degree edges.
    for angle in (45, 135):
        ax.plot([math.radians(angle)] * 2, list(ax.get_ylim()), color=MUTED, linewidth=1.0, zorder=2)
    ax.set_title(title, fontsize=8)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


def _spray_image(points: pd.DataFrame, title: str) -> Image:
    return Image(io.BytesIO(spray_chart_png(points, title)), width=62 * mm, height=48 * mm)


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _header_section(data: dict) -> list:
    story = [Paragraph(f"Scouting Report: {data['opponent']}", _TITLE)]
    subtitle = f"Prepared for {data['our_team']} — {data['league_label']}"
    story.append(Paragraph(subtitle, _BODY))
    fixture = data.get("fixture")
    if fixture:
        venue = f" at {fixture['venue']}" if fixture.get("venue") else ""
        story.append(
            Paragraph(
                f"<b>Next meeting:</b> {_fmt('game_date', fixture['game_date'])} "
                f"({fixture['home_away']}){venue}",
                _BODY,
            )
        )
    freshness = f" — data last refreshed {data['freshness']} UTC" if data.get("freshness") else ""
    story.append(Paragraph(f"Generated {dt.date.today().strftime('%d %b %Y')}{freshness}", _SMALL))
    story.append(Spacer(1, 4 * mm))
    return story


_OVERVIEW_STANDINGS_COLS = ["team", "w", "l", "t", "pct"]
_OVERVIEW_TEAM_COLS = ["team", "r_pg", "ra_pg", "avg", "obp", "slg", "woba", "era", "whip", "fip"]
_RECENT_COLS = ["game_date", "opponent", "home_away", "score", "result"]


def _overview_section(data: dict) -> list:
    story = [Paragraph("Opponent overview", _H2)]
    standings = data.get("standings")
    if standings is not None and not standings.empty:
        story.append(_df_table(standings, _OVERVIEW_STANDINGS_COLS, highlight=data["opponent"]))
        story.append(Spacer(1, 3 * mm))
    team_stats = data.get("team_stats")
    if team_stats is not None and not team_stats.empty:
        story.append(Paragraph("Team batting/pitching vs league", _H3))
        story.append(_df_table(team_stats, [c for c in _OVERVIEW_TEAM_COLS if c in team_stats.columns]))
        story.append(Spacer(1, 3 * mm))
    recent = data.get("recent_games")
    if recent is not None and not recent.empty:
        story.append(Paragraph("Recent games (last 3 weekends)", _H3))
        story.append(_df_table(recent, [c for c in _RECENT_COLS if c in recent.columns]))
    story.append(Spacer(1, 4 * mm))
    return story


_HITTER_TABLE_COLS = [
    "player", "bats", "pa", "avg", "obp", "slg", "woba", "shrunk_woba", "wrc_plus", "hr", "sb",
    "bb_pct", "k_pct", "tendency",
]


def _hitters_section(data: dict) -> list:
    story = [Paragraph("Their hitters", _H2)]
    hitters = data.get("hitters")
    if hitters is None or hitters.empty:
        story.append(Paragraph("No batting data for this team.", _BODY))
        return story
    story.append(
        Paragraph(
            "Ranked by true-talent wOBA (observed wOBA shrunk toward the league average by sample size) — "
            "the fairest ordering at amateur-season sample sizes.",
            _SMALL,
        )
    )
    story.append(_df_table(hitters, [c for c in _HITTER_TABLE_COLS if c in hitters.columns]))
    story.append(Spacer(1, 4 * mm))

    for detail in data.get("hitter_details", []):
        block = [Paragraph(detail["name"], _H3)]
        if detail.get("note"):
            block.append(Paragraph(detail["note"], _BODY))
        charts = Table(
            [
                [
                    _spray_image(detail["season_points"], "This season"),
                    _spray_image(detail["career_points"], "Career"),
                ]
            ]
        )
        charts.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        block.append(charts)
        story.append(KeepTogether(block))
        story.append(Spacer(1, 3 * mm))
    return story


_STAFF_TABLE_COLS = [
    "player", "throws", "g", "gs", "ip", "team_ip_share", "era", "whip", "k9", "bb9", "fip",
    "shrunk_fip", "fps_pct", "sv", "confidence",
]
_MATCHUP_COLS = ["player", "pa", "ab", "h", "doubles", "hr", "bb", "so", "avg"]


def _pitchers_section(data: dict) -> list:
    story = [Paragraph("Their pitching staff", _H2)]
    staff = data.get("staff")
    if staff is None or staff.empty:
        story.append(Paragraph("No pitching data for this team.", _BODY))
        return story
    story.append(
        Paragraph(
            "Probable starters are inferred from actual usage (who has started, weighted toward recent "
            "weekends) — the league publishes no rotations, so treat this as an informed guess, and "
            "expect two different starters across a doubleheader.",
            _SMALL,
        )
    )
    story.append(_df_table(staff, [c for c in _STAFF_TABLE_COLS if c in staff.columns]))
    story.append(Spacer(1, 4 * mm))

    for detail in data.get("pitcher_details", []):
        throws = {"L": "LHP", "R": "RHP"}.get(detail.get("throws"), "throws unknown")
        block = [Paragraph(f"{detail['name']} ({throws})", _H3)]
        if detail.get("evidence"):
            block.append(Paragraph(f"<b>Likelihood:</b> {detail['evidence']}", _BODY))
        vs_hands = detail.get("vs_hands")
        if vs_hands is not None and not vs_hands.empty:
            splits = ", ".join(
                f"vs {'LHB' if row['vs_hand'] == 'L' else 'RHB'}: {_fmt('woba', row['woba'])} wOBA "
                f"({int(row['pa'])} PA)"
                for _, row in vs_hands.iterrows()
            )
            block.append(Paragraph(f"<b>Career splits allowed:</b> {splits}", _BODY))
        block.append(_spray_image(detail["spray_points"], "Contact allowed (career)"))
        matchups = detail.get("matchups")
        if matchups is not None and not matchups.empty:
            block.append(Paragraph("Our batters' career history vs this pitcher (tiny samples — context, not proof):", _BODY))
            block.append(_df_table(matchups, [c for c in _MATCHUP_COLS if c in matchups.columns]))
        story.append(KeepTogether(block))
        story.append(Spacer(1, 3 * mm))
    return story


_LINEUP_TABLE_COLS = ["slot", "player", "pa", "avg", "obp", "slg", "iso", "bb_pct", "k_pct", "sb"]
_BENCH_TABLE_COLS = [
    "player", "role", "pa", "avg", "obp", "iso", "k_pct",
    "avg_vs_lhp", "pa_vs_lhp", "avg_vs_rhp", "pa_vs_rhp",
]


def _lineup_section(data: dict) -> list:
    story = [PageBreak(), Paragraph("Recommended lineup", _H2)]
    lineup = data.get("lineup")
    if not lineup:
        story.append(Paragraph("No lineup was generated for this report.", _BODY))
        return story
    result = lineup["result"]
    vs_note = {"L": " against a probable left-handed starter", "R": " against a probable right-handed starter"}.get(
        lineup.get("vs_throws"), ""
    )
    story.append(
        Paragraph(
            f"Starting nine{vs_note}: <b>{result.expected_runs:.2f} expected runs</b> per 7-inning game "
            f"(the same nine batted simply best-to-worst: {result.baselines.get('by_woba_desc', 0):.2f}).",
            _BODY,
        )
    )
    lineup_table = lineup.get("lineup")
    if lineup_table is not None and not lineup_table.empty:
        story.append(Spacer(1, 2 * mm))
        story.append(_df_table(lineup_table, [c for c in _LINEUP_TABLE_COLS if c in lineup_table.columns]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("<b>Why each hitter is where they are</b>", _BODY))
    for line in result.rationale:
        story.append(Paragraph(line, _BODY))
    story.append(Spacer(1, 2 * mm))
    story.append(
        Paragraph(
            "Order differences are worth fractions of a run per game — treat this as a tiebreaker between "
            "defensible orders, not a verdict. Positions are yours to assign.",
            _SMALL,
        )
    )
    bench = lineup.get("bench")
    if bench is not None and not bench.empty:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Bench", _H3))
        story.append(_df_table(bench, [c for c in _BENCH_TABLE_COLS if c in bench.columns]))
        story.append(
            Paragraph(
                "Pinch-hit calls compare each bench bat's record against left- and right-handed pitching, "
                "penalized for small samples — nobody is named a pinch-hit option on fewer than 20 PA "
                "this season.",
                _SMALL,
            )
        )
    return story


def _methodology_section() -> list:
    notes = [
        "Rankings use true-talent (empirical-Bayes shrunk) wOBA/FIP, so small samples are pulled toward "
        "league average rather than taken at face value.",
        "Spray charts approximate location from the site's directional pull value and hit distance — no true "
        "batted-ball coordinates exist for this league. The fan is fixed 90-degree fair territory.",
        "Probable pitchers are inferred from usage history (recency-weighted starts). The league publishes "
        "no rotation information.",
        "Batter-vs-pitcher history has no minimum sample size; a 3-for-5 career line is noise, not a scouting edge.",
        "The lineup model plays 7 innings with deterministic base advancement, no steals, sacrifices or double "
        "plays, and ignores mercy/curfew rules (they truncate blowouts roughly equally for any order).",
        WAR_DISCLAIMER,
    ]
    story = [Paragraph("Methodology & caveats", _H2)]
    for note in notes:
        story.append(Paragraph(f"• {note}", _SMALL))
    return story


def _page_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.grey)
    canvas.drawString(15 * mm, 8 * mm, "British Baseball Stats Explorer — scouting report")
    canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_scouting_pdf(data: dict) -> bytes:
    """Assemble the full report. `data` keys (all optional except our_team /
    opponent / league_label): fixture, freshness, standings, team_stats,
    recent_games, hitters, hitter_details, staff, pitcher_details, lineup —
    see the section builders above for each shape."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Scouting Report — {data['opponent']}",
    )
    story = (
        _header_section(data)
        + _overview_section(data)
        + _hitters_section(data)
        + [Spacer(1, 4 * mm)]
        + _pitchers_section(data)
        + _lineup_section(data)
        + [Spacer(1, 6 * mm)]
        + _methodology_section()
    )
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()
