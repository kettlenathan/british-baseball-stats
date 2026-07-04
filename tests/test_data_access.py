import pytest

from app.components import data_access
from db.models import (
    BattingSeasonStats,
    BattingWar,
    Game,
    League,
    LeagueSeason,
    PitchingSeasonStats,
    PitchingWar,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)


@pytest.fixture(autouse=True)
def _patch_get_session(session, monkeypatch):
    monkeypatch.setattr(data_access, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    yield
    for fn in (
        data_access.player_career,
        data_access.player_batting_comparison,
        data_access.player_pitching_comparison,
        data_access.team_history,
        data_access.all_player_names,
        data_access.all_team_names,
    ):
        fn.clear()


def _build_fixture(session) -> dict[str, str]:
    league = League(code="nbl", name="National Baseball League", tier="senior", is_senior=True)
    season_2025 = Season(year=2025)
    season_2026 = Season(year=2026)
    session.add_all([league, season_2025, season_2026])
    session.flush()

    ls_2025 = LeagueSeason(
        league_id=league.id, season_id=season_2025.id, source_tournament_id=1, competition_slug="2025-nbl"
    )
    ls_2026 = LeagueSeason(
        league_id=league.id, season_id=season_2026.id, source_tournament_id=2, competition_slug="2026-nbl"
    )
    session.add_all([ls_2025, ls_2026])
    session.flush()

    team_a = Team(name="Alpha")
    team_b = Team(name="Beta")
    session.add_all([team_a, team_b])
    session.flush()

    ts_a1 = TeamSeason(team_id=team_a.id, league_season_id=ls_2025.id, source_team_id=1, display_name="Alpha")
    ts_a2 = TeamSeason(team_id=team_a.id, league_season_id=ls_2026.id, source_team_id=1, display_name="Alpha")
    ts_b1 = TeamSeason(team_id=team_b.id, league_season_id=ls_2025.id, source_team_id=2, display_name="Beta")
    ts_b2 = TeamSeason(team_id=team_b.id, league_season_id=ls_2026.id, source_team_id=2, display_name="Beta")
    session.add_all([ts_a1, ts_a2, ts_b1, ts_b2])
    session.flush()

    player_one = Player(source_id=1, first_name="Player", last_name="One", full_name="Player One")
    player_two = Player(source_id=2, first_name="Player", last_name="Two", full_name="Player Two")
    session.add_all([player_one, player_two])
    session.flush()

    ps_one_2025 = PlayerSeason(player_id=player_one.id, team_season_id=ts_a1.id)
    ps_one_2026 = PlayerSeason(player_id=player_one.id, team_season_id=ts_a2.id)
    ps_two_2025 = PlayerSeason(player_id=player_two.id, team_season_id=ts_b1.id)
    session.add_all([ps_one_2025, ps_one_2026, ps_two_2025])
    session.flush()

    session.add_all(
        [
            BattingSeasonStats(player_season_id=ps_one_2025.id, pa=20, ab=18, h=6, doubles=1, hr=1, bb=2, so=3),
            BattingSeasonStats(player_season_id=ps_one_2026.id, pa=25, ab=22, h=8, doubles=2, hr=2, bb=3, so=4),
            BattingSeasonStats(player_season_id=ps_two_2025.id, pa=15, ab=14, h=4, bb=1, so=2),
        ]
    )
    session.add_all(
        [
            BattingWar(player_season_id=ps_one_2025.id, woba=0.35, wraa=1.0, war=0.5, formula_version="v1"),
            BattingWar(player_season_id=ps_one_2026.id, woba=0.40, wraa=2.0, war=1.0, formula_version="v1"),
            BattingWar(player_season_id=ps_two_2025.id, woba=0.30, wraa=0.0, war=0.1, formula_version="v1"),
        ]
    )

    # Player Two also pitched in 2025 (two-way-ish for pitching-only coverage); Player One never pitched.
    session.add(
        PitchingSeasonStats(player_season_id=ps_two_2025.id, outs_recorded=27, h=5, er=2, bb=3, so=6, wins=1)
    )
    session.add(PitchingWar(player_season_id=ps_two_2025.id, fip=3.5, war=0.3, formula_version="v1"))

    session.add_all(
        [
            Game(
                source_id=101, league_season_id=ls_2025.id, home_team_season_id=ts_a1.id,
                away_team_season_id=ts_b1.id, home_score=5, away_score=3, status="final",
            ),
            Game(
                source_id=102, league_season_id=ls_2026.id, home_team_season_id=ts_a2.id,
                away_team_season_id=ts_b2.id, home_score=2, away_score=6, status="final",
            ),
        ]
    )
    session.commit()

    return {"player_one": "Player One", "player_two": "Player Two", "team_a": "Alpha", "team_b": "Beta"}


def test_player_batting_comparison_includes_all_selected_players(session):
    names = _build_fixture(session)
    df = data_access.player_batting_comparison([names["player_one"], names["player_two"]])

    assert set(df["player"]) == {"Player One", "Player Two"}
    assert len(df[df["player"] == "Player One"]) == 2
    assert len(df[df["player"] == "Player Two"]) == 1


def test_player_pitching_comparison_only_includes_pitchers(session):
    names = _build_fixture(session)
    df = data_access.player_pitching_comparison([names["player_one"], names["player_two"]])

    assert list(df["player"]) == ["Player Two"]
    assert df.iloc[0]["w"] == 1
    assert df.iloc[0]["fip"] == 3.5


def test_team_history_computes_win_pct_per_year(session):
    names = _build_fixture(session)
    df = data_access.team_history([names["team_a"], names["team_b"]])

    alpha_2025 = df[(df["team"] == "Alpha") & (df["year"] == 2025)].iloc[0]
    assert (alpha_2025["w"], alpha_2025["l"], alpha_2025["pct"]) == (1, 0, 1.0)

    beta_2026 = df[(df["team"] == "Beta") & (df["year"] == 2026)].iloc[0]
    assert (beta_2026["w"], beta_2026["l"], beta_2026["pct"]) == (1, 0, 1.0)


def test_team_history_single_team_returns_only_its_own_rows(session):
    names = _build_fixture(session)
    df = data_access.team_history([names["team_a"]])

    assert set(df["team"]) == {"Alpha"}
    assert len(df) == 2
