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
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
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


# Page geometry, kept next to the margins build_scouting_pdf sets — every
# table sizes itself against this rather than letting reportlab pick widths
# from content, which silently runs a 15-column table off the page.
_PAGE_MARGIN = 15 * mm
_FRAME_WIDTH = A4[0] - 2 * _PAGE_MARGIN

_TABLE_FONT_SIZE = 7.5
_HEADER_FONT_SIZE = 7
_CELL_SIDE_PADDING = 3  # reportlab's default is 6, which alone costs 180pt across 15 columns

# Columns holding prose rather than a number: these may wrap onto a second
# line instead of forcing a column wide enough for their longest value.
_WRAPPING_COLS = {"player", "team", "opponent", "role", "evidence", "venue", "confidence", "tendency"}

# A table narrower than the frame is left compact rather than stretched to
# the margins: spreading a 5-column standings table across 180mm leaves each
# number marooned in a sea of whitespace. Wide tables still get compressed to
# the frame. 1.2 gives a little breathing room over the tightest possible fit.
_COMFORT_FACTOR = 1.2

_TH_LEFT = ParagraphStyle(
    "ScoutTHLeft", fontName="Helvetica-Bold", fontSize=_HEADER_FONT_SIZE, leading=_HEADER_FONT_SIZE + 1.5,
    textColor=colors.white, alignment=0,
)
_TH_RIGHT = ParagraphStyle("ScoutTHRight", parent=_TH_LEFT, alignment=2)
_TD = ParagraphStyle("ScoutTD", fontName="Helvetica", fontSize=_TABLE_FONT_SIZE, leading=_TABLE_FONT_SIZE + 1.5)
_TD_BOLD = ParagraphStyle("ScoutTDBold", parent=_TD, fontName="Helvetica-Bold")


def _column_widths(body: list[list[str]], labels: list[str], cols: list[str], avail: float) -> list[float]:
    """Size columns to fit within `avail` points, never past it.

    Each column gets a minimum (its widest unbreakable token — a whole number,
    or the longest single word where wrapping is allowed) and a preferred
    width (everything on one line). The table targets its preferred width plus
    a little comfort, capped at `avail`; whatever room that leaves or takes is
    shared out in proportion to each column's own preferred width, so columns
    keep their natural relative sizes instead of one text column swallowing
    all the slack.
    """
    pad = 2 * _CELL_SIDE_PADDING
    minimums, preferred = [], []
    for index, col in enumerate(cols):
        values = [row[index] for row in body] or [""]
        value_full = max(stringWidth(v, "Helvetica", _TABLE_FONT_SIZE) for v in values)
        if col in _WRAPPING_COLS:
            value_min = max(
                (stringWidth(w, "Helvetica", _TABLE_FONT_SIZE) for v in values for w in v.split() or [v]),
                default=0,
            )
        else:
            value_min = value_full  # never break a number across lines

        label = labels[index]
        head_full = stringWidth(label, "Helvetica-Bold", _HEADER_FONT_SIZE)
        head_min = max(
            (stringWidth(w, "Helvetica-Bold", _HEADER_FONT_SIZE) for w in label.split() or [label]),
            default=0,
        )
        minimums.append(max(value_min, head_min) + pad)
        preferred.append(max(value_full, head_full) + pad)

    if sum(minimums) > avail:
        # Pathological (a very long unbreakable token) — scale down so the
        # table still ends at the margin rather than bleeding off the page.
        scale = avail / sum(minimums)
        return [w * scale for w in minimums]

    target = min(avail, sum(preferred) * _COMFORT_FACTOR)
    if target >= sum(preferred):
        surplus = target - sum(preferred)
        total = sum(preferred) or 1
        return [p + surplus * (p / total) for p in preferred]

    # Too wide for the page: take the overflow back out of each column in
    # proportion to how much it has to give above its own minimum, so the
    # roomiest columns give up the most and nothing is squeezed below the
    # width its content actually needs.
    widths = list(minimums)
    want = [p - m for p, m in zip(preferred, minimums)]
    if sum(want) > 0:
        room = target - sum(minimums)
        widths = [w + room * (n / sum(want)) for w, n in zip(widths, want)]
    return widths


def _df_table(
    df: pd.DataFrame, cols: list[str], highlight: str | None = None, avail: float = _FRAME_WIDTH
) -> Table:
    """A compact reportlab table from DataFrame columns, headers via
    theme.stat_label and numbers via theme.stat_format — the same display
    conventions as the app's own tables.

    Column widths are computed to fill exactly `avail` (see _column_widths):
    left to itself reportlab sizes columns from content and happily draws a
    wide table straight off the right edge of the page.
    """
    labels = [stat_label(c) for c in cols]
    body = [[_fmt(c, row[c]) if c in row else "—" for c in cols] for _, row in df.iterrows()]
    widths = _column_widths(body, labels, cols, avail)

    highlighted = {
        i for i, (_, row) in enumerate(df.iterrows(), start=1)
        if highlight is not None and highlight in (row.get("player"), row.get("team"))
    }
    # Headers align with the values under them — left over wrapped text,
    # right over the right-aligned numbers — rather than centred, which
    # leaves the label floating away from its own column of figures.
    header_cells = [
        Paragraph(label, _TH_LEFT if (col in _WRAPPING_COLS or i == 0) else _TH_RIGHT)
        for i, (col, label) in enumerate(zip(cols, labels))
    ]
    data = [header_cells]
    for row_index, row in enumerate(body, start=1):
        rendered = []
        for col, value in zip(cols, row):
            if col in _WRAPPING_COLS:
                rendered.append(Paragraph(value, _TD_BOLD if row_index in highlighted else _TD))
            else:
                rendered.append(value)
        data.append(rendered)

    table = Table(data, colWidths=widths, repeatRows=1)
    # reportlab centres a table narrower than the frame; left-align it so
    # every table starts on the same margin as the headings and body text.
    table.hAlign = "LEFT"
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _HEADER_BG),
        ("FONTSIZE", (0, 1), (-1, -1), _TABLE_FONT_SIZE),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("VALIGN", (0, 1), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), _CELL_SIDE_PADDING),
        ("RIGHTPADDING", (0, 0), (-1, -1), _CELL_SIDE_PADDING),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT_ROW]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey),
    ]
    for i in highlighted:
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
        # Sub-heading and its (short) table travel as one unit, so a page
        # break can't leave a heading stranded at the foot of a page or
        # orphan a row or two of a table that comfortably fits whole.
        story.append(
            KeepTogether(
                [
                    Paragraph("Team batting/pitching vs league", _H3),
                    _df_table(team_stats, [c for c in _OVERVIEW_TEAM_COLS if c in team_stats.columns]),
                ]
            )
        )
        story.append(Spacer(1, 3 * mm))
    recent = data.get("recent_games")
    if recent is not None and not recent.empty:
        story.append(
            KeepTogether(
                [
                    Paragraph("Recent games (last 3 weekends)", _H3),
                    _df_table(recent, [c for c in _RECENT_COLS if c in recent.columns]),
                ]
            )
        )
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
            "Ranked by true-talent wOBA (observed wOBA regressed by sample size toward what hitters with "
            "that much playing time actually hit) — the fairest ordering at amateur-season sample sizes.",
            _SMALL,
        )
    )
    story.append(_df_table(hitters, [c for c in _HITTER_TABLE_COLS if c in hitters.columns]))
    story.append(Spacer(1, 4 * mm))

    for detail in data.get("hitter_details", []):
        block = [Paragraph(detail["name"], _H3)]
        if detail.get("note"):
            block.append(Paragraph(detail["note"], _BODY))
        # Half the frame each, images centred in their cell — the PNG is
        # rendered at 3in/150dpi, so it's left at 62mm rather than stretched
        # to fill the column, which would upscale it and go soft.
        charts = Table(
            [
                [
                    _spray_image(detail["season_points"], "This season"),
                    _spray_image(detail["career_points"], "Career"),
                ]
            ],
            colWidths=[_FRAME_WIDTH / 2] * 2,
        )
        charts.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        block.append(charts)
        story.append(KeepTogether(block))
        story.append(Spacer(1, 3 * mm))
    return story


_DEFENSE_TABLE_COLS = ["position", "g", "po", "a", "e", "dp", "fpct", "e_per_team", "e_vs_league"]
_DEFENSE_ERROR_PLAYER_COLS = ["position", "player", "g", "po", "a", "e", "fpct"]
_CATCHER_TABLE_COLS = ["player", "g", "sb_against", "cs", "sb_att", "cs_pct", "pb"]


def _defense_section(data: dict) -> list:
    defense = data.get("defense")
    if defense is None or defense.empty:
        return [Paragraph("Their defence", _H2), Paragraph("No fielding data for this team.", _BODY)]

    # One position per row means this table is always short enough to sit on a
    # single page — kept whole with its heading rather than spilling a stray
    # couple of positions over a page break.
    block = [
        Paragraph("Their defence", _H2),
        Paragraph(
            "Errors by position, against the average team's errors at the same position — the only "
            "comparison that means anything, since shortstops and third basemen out-error corner "
            "outfielders everywhere. Fielding % is context, not a verdict: it rewards a fielder who "
            "never reaches the ball in the first place.",
            _SMALL,
        ),
        _df_table(defense, [c for c in _DEFENSE_TABLE_COLS if c in defense.columns]),
    ]
    if "e_vs_league" in defense.columns:
        weak = defense[defense["e_vs_league"] > 0].sort_values("e_vs_league", ascending=False)
        if not weak.empty:
            spots = ", ".join(
                f"{row.position} ({int(row.e)} E, {row.e_vs_league:+.1f} vs league)"
                for row in weak.head(3).itertuples()
            )
            block.append(Spacer(1, 2 * mm))
            block.append(Paragraph(f"<b>Worth testing:</b> {spots}.", _BODY))
    story = [KeepTogether(block)]

    error_players = data.get("defense_error_players")
    if error_players is not None and not error_players.empty:
        story.append(Spacer(1, 3 * mm))
        story.append(
            KeepTogether(
                [
                    Paragraph("Who makes them", _H3),
                    _df_table(
                        error_players,
                        [c for c in _DEFENSE_ERROR_PLAYER_COLS if c in error_players.columns],
                    ),
                ]
            )
        )

    catchers = data.get("catchers")
    if catchers is not None and not catchers.empty:
        block = [
            Paragraph("Can we run on them?", _H3),
            _df_table(catchers, [c for c in _CATCHER_TABLE_COLS if c in catchers.columns]),
        ]
        primary = catchers.iloc[0]
        league_cs_pct = data.get("league_catcher_cs_pct")
        if primary.get("cs_pct") is not None and league_cs_pct is not None:
            verdict = "Run at will" if primary["cs_pct"] < league_cs_pct else "Be selective"
            block.append(Spacer(1, 2 * mm))
            block.append(
                Paragraph(
                    f"<b>{verdict}:</b> their main catcher {primary['player']} has thrown out "
                    f"{primary['cs_pct']:.1%} of runners ({int(primary['cs'])} of "
                    f"{int(primary['sb_att'])} attempts), against a league average of "
                    f"{league_cs_pct:.1%}.",
                    _BODY,
                )
            )
        block.append(
            Paragraph(
                "Attempts are steals allowed plus runners caught. This league's scorers charge part "
                "of a team's steals allowed to the pitcher, so these are the catcher's own share — "
                "and a slow-working pitcher inflates their steals against.",
                _SMALL,
            )
        )
        story.append(Spacer(1, 3 * mm))
        story.append(KeepTogether(block))
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
        "Rankings use true-talent (empirical-Bayes shrunk) wOBA/FIP, so small samples are regressed rather "
        "than taken at face value. Lightly-used hitters are regressed toward what lightly-used hitters in "
        "this league actually hit, which is below average — not toward the league average itself.",
        "A hitter with under 20 PA is projected and ranked like anyone else, but no claim is made about "
        "them: their line shows the projection and its likely range instead of quoting rate stats that "
        "a handful of plate appearances can't support.",
        "Spray charts approximate location from the site's directional pull value and hit distance — no true "
        "batted-ball coordinates exist for this league. The fan is fixed 90-degree fair territory.",
        "Probable pitchers are inferred from usage history (recency-weighted starts). The league publishes "
        "no rotation information.",
        "Batter-vs-pitcher history has no minimum sample size; a 3-for-5 career line is noise, not a scouting edge.",
        "Errors are attributed to a position from the box score's own position field, falling back to the "
        "play-by-play's scorer notation (E6, E4T) when a fielder moved position mid-game; the ~1.5% neither "
        "source can place are shown as UNK rather than dropped. Error counts have no opportunity denominator, "
        "so they measure where mistakes happened, not fielding skill.",
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
    recent_games, hitters, hitter_details, defense, defense_error_players, catchers,
    staff, pitcher_details, lineup — see the section builders above for each
    shape."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_PAGE_MARGIN,
        rightMargin=_PAGE_MARGIN,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Scouting Report — {data['opponent']}",
    )
    story = (
        _header_section(data)
        + _overview_section(data)
        + _hitters_section(data)
        + [Spacer(1, 4 * mm)]
        + _defense_section(data)
        + [Spacer(1, 4 * mm)]
        + _pitchers_section(data)
        + _lineup_section(data)
        + [Spacer(1, 6 * mm)]
        + _methodology_section()
    )
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()
