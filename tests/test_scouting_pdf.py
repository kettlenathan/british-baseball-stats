import datetime as dt

import pandas as pd

from app.components.scouting_pdf import build_scouting_pdf, spray_chart_png
from stats.lineup import LineupResult

_POINTS = pd.DataFrame(
    {
        "hitpull": [-30, -10, 0, 25, 60],  # 60 exercises the +/-45 clamp
        "hitdistance": [120, 200, 250, 90, -5],  # -5 exercises the garbage floor
        "hittype": [1, 2, 3, 1, 2],
        "outcome": ["Single", "Double", "Home Run", "Out", "Out"],
    }
)


def _full_data() -> dict:
    hitters = pd.DataFrame(
        [
            {
                "player": "Slugger", "bats": "L", "pa": 40, "avg": 0.514, "obp": 0.575, "slg": 0.9,
                "woba": 0.55, "shrunk_woba": 0.45, "wrc_plus": 160.0, "hr": 3, "sb": 2,
                "bb_pct": 0.125, "k_pct": 0.1, "tendency": "pull",
            },
            {
                "player": "Scrub", "bats": None, "pa": 30, "avg": 0.143, "obp": 0.2, "slg": 0.143,
                "woba": 0.15, "shrunk_woba": 0.28, "wrc_plus": 44.0, "hr": 0, "sb": 0,
                "bb_pct": 0.066, "k_pct": 0.4, "tendency": None,
            },
        ]
    )
    staff = pd.DataFrame(
        [
            {
                "player": "Ace", "throws": "L", "g": 5, "gs": 5, "ip": 30.1, "team_ip_share": 0.55,
                "era": 3.60, "whip": 1.13, "k9": 12.0, "bb9": 3.0, "fip": 3.4, "shrunk_fip": 3.8,
                "fps_pct": 0.66, "sv": 0, "confidence": "High",
            }
        ]
    )
    return {
        "our_team": "Us",
        "opponent": "Them",
        "league_label": "National Baseball League (2026)",
        "freshness": "2026-08-09 21:14",
        "fixture": {"game_date": dt.date(2026, 8, 16), "home_away": "Home", "venue": "Home Field"},
        "standings": pd.DataFrame(
            [
                {"team": "Them", "w": 10, "l": 4, "t": 0, "pct": 0.714},
                {"team": "Us", "w": 8, "l": 6, "t": 0, "pct": 0.571},
            ]
        ),
        "team_stats": pd.DataFrame(
            [
                {
                    "team": "Them", "r_pg": 6.1, "ra_pg": 4.2, "avg": 0.290, "obp": 0.360, "slg": 0.420,
                    "woba": 0.350, "era": 4.10, "whip": 1.40, "fip": 4.5,
                },
                {
                    "team": "League average", "r_pg": 5.5, "ra_pg": 5.5, "avg": 0.270, "obp": 0.340,
                    "slg": 0.380, "woba": 0.330, "era": 5.00, "whip": 1.55, "fip": 5.0,
                },
            ]
        ),
        "recent_games": pd.DataFrame(
            [{"game_date": dt.date(2026, 8, 2), "opponent": "Us", "home_away": "Home", "score": "4-2", "result": "W"}]
        ),
        "hitters": hitters,
        "hitter_details": [
            {"name": "Slugger", "note": "Pull-heavy left-handed bat.", "season_points": _POINTS, "career_points": _POINTS},
            # A hitter with no batted-ball data must render a placeholder, not crash.
            {"name": "Scrub", "note": None, "season_points": _POINTS.iloc[0:0], "career_points": _POINTS.iloc[0:0]},
        ],
        "defense": pd.DataFrame(
            [
                {"position": "SS", "g": 14, "po": 20, "a": 30, "e": 9, "dp": 3, "fpct": 0.847,
                 "e_per_team": 5.0, "e_vs_league": 4.0},
                {"position": "CF", "g": 14, "po": 25, "a": 1, "e": 0, "dp": 0, "fpct": 1.0,
                 "e_per_team": 1.5, "e_vs_league": -1.5},
                # Unattributed errors still render rather than being dropped.
                {"position": "UNK", "g": 1, "po": 0, "a": 0, "e": 1, "dp": 0, "fpct": 0.0,
                 "e_per_team": 0.4, "e_vs_league": 0.6},
            ]
        ),
        "defense_error_players": pd.DataFrame(
            [{"position": "SS", "player": "Butterfingers", "g": 12, "po": 18, "a": 25, "e": 8, "fpct": 0.843}]
        ),
        "catchers": pd.DataFrame(
            [
                {"player": "Noodle ARM", "g": 15, "sb_against": 40, "cs": 2, "sb_att": 42,
                 "cs_pct": 2 / 42, "pb": 6},
                {"player": "Backup CATCHER", "g": 4, "sb_against": 5, "cs": 5, "sb_att": 10,
                 "cs_pct": 0.5, "pb": 1},
            ]
        ),
        "league_catcher_cs_pct": 0.06,
        "staff": staff,
        "pitcher_details": [
            {
                "name": "Ace",
                "throws": "L",
                "evidence": "5 starts this season, most recently 02 Aug",
                "spray_points": _POINTS,
                "vs_hands": pd.DataFrame(
                    [{"vs_hand": "L", "pa": 20, "woba": 0.25}, {"vs_hand": "R", "pa": 50, "woba": 0.34}]
                ),
                "matchups": pd.DataFrame(
                    [{"player": "Our Batter", "pa": 6, "ab": 5, "h": 3, "doubles": 1, "hr": 1, "bb": 1, "so": 1, "avg": 0.6}]
                ),
            }
        ],
        "lineup": {
            "result": LineupResult(
                order=["A", "B", "C", "D", "E", "F", "G", "H", "I"],
                expected_runs=5.4,
                baselines={"recommended": 5.4, "by_woba_desc": 5.3, "as_selected": 5.1},
                rationale=[f"{i}. Player — placeholder rationale." for i in range(1, 10)],
            ),
            "lineup": pd.DataFrame(
                [
                    {
                        "slot": i, "player": name, "pa": 30 + i, "avg": 0.280, "obp": 0.350,
                        "slg": 0.400, "iso": 0.120, "bb_pct": 0.10, "k_pct": 0.20, "sb": 1,
                    }
                    for i, name in enumerate("ABCDEFGHI", start=1)
                ]
            ),
            "bench": pd.DataFrame(
                [
                    {
                        "player": "J", "role": "First bat off the bench vs LHP", "pa": 12, "avg": 0.250,
                        "obp": 0.300, "iso": 0.050, "k_pct": 0.30,
                        "avg_vs_lhp": 0.400, "pa_vs_lhp": 5, "avg_vs_rhp": 0.150, "pa_vs_rhp": 7,
                    },
                    {
                        "player": "K", "role": "First bat off the bench vs RHP", "pa": 10, "avg": 0.220,
                        "obp": 0.280, "iso": 0.000, "k_pct": 0.35,
                        "avg_vs_lhp": None, "pa_vs_lhp": 0, "avg_vs_rhp": 0.300, "pa_vs_rhp": 10,
                    },
                ]
            ),
            "vs_throws": "L",
        },
    }


def test_no_table_is_wider_than_the_printable_frame():
    """The failure this guards against is silent: reportlab sizes columns
    from content and will happily draw a 15-column table straight off the
    right edge of the page rather than raising."""
    from reportlab.platypus import Table

    import app.components.scouting_pdf as sp

    widths = []
    original = Table.wrap
    try:
        def record(self, avail_width, avail_height):
            width, height = original(self, avail_width, avail_height)
            widths.append(width)
            return width, height

        Table.wrap = record
        build_scouting_pdf(_full_data())
    finally:
        Table.wrap = original

    assert widths, "no tables were rendered — the fixture stopped exercising them"
    too_wide = [w for w in widths if w > sp._FRAME_WIDTH + 0.5]
    assert not too_wide, f"{len(too_wide)} table(s) exceed the {sp._FRAME_WIDTH:.0f}pt frame: {too_wide}"


def test_wide_table_is_compressed_and_narrow_table_is_left_compact():
    """A 15-column table has to be squeezed to the frame; a 3-column one must
    not be stretched across it just because the room exists."""
    import app.components.scouting_pdf as sp

    wide = sp._df_table(_full_data()["staff"], _full_data()["staff"].columns.tolist())
    assert wide.wrap(sp._FRAME_WIDTH, 10_000)[0] <= sp._FRAME_WIDTH + 0.5

    narrow = sp._df_table(pd.DataFrame([{"team": "Them", "w": 10, "l": 4}]), ["team", "w", "l"])
    assert narrow.wrap(sp._FRAME_WIDTH, 10_000)[0] < sp._FRAME_WIDTH * 0.75
    assert narrow.hAlign == "LEFT"


def test_spray_chart_png_renders():
    png = spray_chart_png(_POINTS, "This season")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    empty = spray_chart_png(_POINTS.iloc[0:0], "Career")
    assert empty[:8] == b"\x89PNG\r\n\x1a\n"


def test_full_report_builds():
    pdf = build_scouting_pdf(_full_data())
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 20_000  # several pages with embedded charts


def test_report_builds_without_a_defence_section():
    # An opponent with no fielding data at all, and one whose league
    # comparison couldn't be computed — neither should break the build.
    data = _full_data()
    data["defense"] = pd.DataFrame()
    data["defense_error_players"] = pd.DataFrame()
    data["catchers"] = pd.DataFrame()
    assert build_scouting_pdf(data)[:5] == b"%PDF-"

    data = _full_data()
    data["defense"] = data["defense"].drop(columns=["e_per_team", "e_vs_league"])
    assert build_scouting_pdf(data)[:5] == b"%PDF-"


def test_report_builds_without_a_league_catcher_baseline():
    # No league-wide CS% to compare against (e.g. a season with no recorded
    # attempts) must still render the catcher table, just without the verdict.
    data = _full_data()
    data["league_catcher_cs_pct"] = None
    assert build_scouting_pdf(data)[:5] == b"%PDF-"

    data = _full_data()
    data["catchers"] = data["catchers"].assign(cs_pct=None)
    assert build_scouting_pdf(data)[:5] == b"%PDF-"


def test_minimal_report_degrades_gracefully():
    # A historical opponent with no play-by-play, no fixture, no lineup:
    # every optional section absent.
    pdf = build_scouting_pdf(
        {"our_team": "Us", "opponent": "Them", "league_label": "NBL (2020)"}
    )
    assert pdf[:5] == b"%PDF-"
