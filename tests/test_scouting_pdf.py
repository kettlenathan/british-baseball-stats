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
            "vs_throws": "L",
        },
    }


def test_spray_chart_png_renders():
    png = spray_chart_png(_POINTS, "This season")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    empty = spray_chart_png(_POINTS.iloc[0:0], "Career")
    assert empty[:8] == b"\x89PNG\r\n\x1a\n"


def test_full_report_builds():
    pdf = build_scouting_pdf(_full_data())
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 20_000  # several pages with embedded charts


def test_minimal_report_degrades_gracefully():
    # A historical opponent with no play-by-play, no fixture, no lineup:
    # every optional section absent.
    pdf = build_scouting_pdf(
        {"our_team": "Us", "opponent": "Them", "league_label": "NBL (2020)"}
    )
    assert pdf[:5] == b"%PDF-"
