import pytest

from app.components import data_access
from db.models import (
    BattingSeasonStats,
    BattingWar,
    Game,
    League,
    LeagueSeason,
    LeagueSeasonContext,
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
        data_access.player_batting_career,
        data_access.player_pitching_career,
        data_access.player_batting_comparison,
        data_access.player_pitching_comparison,
        data_access.team_history,
        data_access.team_season_stats,
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


def test_player_batting_career_aggregates_multi_team_same_year(session):
    """A player traded mid-season gets two PlayerSeason rows in the same
    year — the Player Page should show one combined row, not two, with
    counting stats (including fielding) summed and rate stats recomputed
    from the sums rather than averaged."""
    league = League(code="nbl", name="National Baseball League")
    season = Season(year=2025)
    session.add_all([league, season])
    session.flush()

    ls = LeagueSeason(league_id=league.id, season_id=season.id, source_tournament_id=10, competition_slug="2025-nbl")
    session.add(ls)
    session.flush()

    team_a = Team(name="Alpha")
    team_b = Team(name="Beta")
    session.add_all([team_a, team_b])
    session.flush()

    ts_a = TeamSeason(team_id=team_a.id, league_season_id=ls.id, source_team_id=1, display_name="Alpha")
    ts_b = TeamSeason(team_id=team_b.id, league_season_id=ls.id, source_team_id=2, display_name="Beta")
    session.add_all([ts_a, ts_b])
    session.flush()

    player = Player(source_id=99, first_name="Multi", last_name="Team", full_name="Multi Team")
    session.add(player)
    session.flush()

    ps_a = PlayerSeason(player_id=player.id, team_season_id=ts_a.id)
    ps_b = PlayerSeason(player_id=player.id, team_season_id=ts_b.id)
    session.add_all([ps_a, ps_b])
    session.flush()

    session.add_all(
        [
            BattingSeasonStats(
                player_season_id=ps_a.id, pa=20, ab=18, h=6, doubles=1, hr=1, bb=2, so=3,
                field_po=10, field_a=2, field_e=1, field_dp=1,
            ),
            BattingSeasonStats(
                player_season_id=ps_b.id, pa=10, ab=9, h=3, hr=0, bb=1, so=2,
                field_po=5, field_a=1, field_e=0, field_dp=0,
            ),
        ]
    )
    session.add_all(
        [
            BattingWar(player_season_id=ps_a.id, woba=0.35, wraa=1.0, war=0.5, formula_version="v1"),
            BattingWar(player_season_id=ps_b.id, woba=0.30, wraa=0.2, war=0.1, formula_version="v1"),
        ]
    )
    session.commit()

    df = data_access.player_batting_career("Multi Team")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["year"] == 2025
    assert row["team"] == "Alpha, Beta"
    assert row["pa"] == 30
    assert row["hr"] == 1
    assert row["po"] == 15
    assert row["a"] == 3
    assert row["e"] == 1
    assert row["dp"] == 1
    assert row["war"] == pytest.approx(0.6)
    assert row["avg"] == pytest.approx(9 / 27)
    assert row["fpct"] == pytest.approx((15 + 3) / (15 + 3 + 1))


def test_team_season_stats_aggregates_roster_and_games(session):
    league = League(code="nbl", name="National Baseball League")
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()

    ls = LeagueSeason(league_id=league.id, season_id=season.id, source_tournament_id=20, competition_slug="2026-nbl")
    session.add(ls)
    session.flush()
    session.add(LeagueSeasonContext(league_season_id=ls.id, lg_woba=0.320, lg_era=4.50, fip_constant=3.10))

    team_a = Team(name="Alpha")
    team_b = Team(name="Beta")
    session.add_all([team_a, team_b])
    session.flush()

    ts_a = TeamSeason(team_id=team_a.id, league_season_id=ls.id, source_team_id=1, display_name="Alpha")
    ts_b = TeamSeason(team_id=team_b.id, league_season_id=ls.id, source_team_id=2, display_name="Beta")
    session.add_all([ts_a, ts_b])
    session.flush()

    batter_a = Player(source_id=1, first_name="Bat", last_name="A", full_name="Bat A")
    batter_b = Player(source_id=2, first_name="Bat", last_name="B", full_name="Bat B")
    pitcher_a = Player(source_id=3, first_name="Pitch", last_name="A", full_name="Pitch A")
    pitcher_b = Player(source_id=4, first_name="Pitch", last_name="B", full_name="Pitch B")
    session.add_all([batter_a, batter_b, pitcher_a, pitcher_b])
    session.flush()

    ps_bat_a = PlayerSeason(player_id=batter_a.id, team_season_id=ts_a.id)
    ps_bat_b = PlayerSeason(player_id=batter_b.id, team_season_id=ts_b.id)
    ps_pitch_a = PlayerSeason(player_id=pitcher_a.id, team_season_id=ts_a.id)
    ps_pitch_b = PlayerSeason(player_id=pitcher_b.id, team_season_id=ts_b.id)
    session.add_all([ps_bat_a, ps_bat_b, ps_pitch_a, ps_pitch_b])
    session.flush()

    session.add_all(
        [
            BattingSeasonStats(
                player_season_id=ps_bat_a.id, pa=20, ab=18, h=6, doubles=1, hr=1, bb=2, so=3,
                field_po=10, field_a=2, field_e=1, risp_ab=4, risp_h=2,
            ),
            BattingSeasonStats(
                player_season_id=ps_bat_b.id, pa=15, ab=14, h=4, bb=1, so=2,
                field_po=5, field_a=1, field_e=0, risp_ab=2, risp_h=1,
            ),
        ]
    )
    session.add_all(
        [
            BattingWar(player_season_id=ps_bat_a.id, woba=0.35, wraa=1.0, war=0.5, formula_version="v1"),
            BattingWar(player_season_id=ps_bat_b.id, woba=0.30, wraa=0.2, war=0.1, formula_version="v1"),
        ]
    )
    session.add_all(
        [
            PitchingSeasonStats(player_season_id=ps_pitch_a.id, outs_recorded=27, h=5, er=3, bb=2, so=6, hr=1),
            PitchingSeasonStats(player_season_id=ps_pitch_b.id, outs_recorded=27, h=8, er=5, bb=3, so=4, hr=2),
        ]
    )
    session.add_all(
        [
            PitchingWar(player_season_id=ps_pitch_a.id, fip=3.2, war=0.4, formula_version="v1"),
            PitchingWar(player_season_id=ps_pitch_b.id, fip=4.8, war=-0.1, formula_version="v1"),
        ]
    )

    session.add_all(
        [
            Game(
                source_id=5001, league_season_id=ls.id, home_team_season_id=ts_a.id, away_team_season_id=ts_b.id,
                home_score=5, away_score=3, status="final", home_lob=4, away_lob=6,
            ),
            Game(
                source_id=5002, league_season_id=ls.id, home_team_season_id=ts_b.id, away_team_season_id=ts_a.id,
                home_score=2, away_score=6, status="final", home_lob=3, away_lob=2,
            ),
        ]
    )
    session.commit()

    df = data_access.team_season_stats(ls.id)
    alpha = df[df["team"] == "Alpha"].iloc[0]
    beta = df[df["team"] == "Beta"].iloc[0]

    assert (alpha["w"], alpha["l"], alpha["pct"]) == (2, 0, 1.0)
    assert (beta["w"], beta["l"], beta["pct"]) == (0, 2, 0.0)
    assert alpha["r_pg"] == pytest.approx(5.5)
    assert alpha["ra_pg"] == pytest.approx(2.5)
    assert alpha["lob_pg"] == pytest.approx(3.0)
    assert beta["lob_pg"] == pytest.approx(4.5)
    assert alpha["avg"] == pytest.approx(6 / 18)
    assert alpha["avg_risp"] == pytest.approx(2 / 4)
    assert alpha["fpct"] == pytest.approx((10 + 2) / (10 + 2 + 1))
    assert alpha["war"] == pytest.approx(0.5 + 0.4)
    assert beta["war"] == pytest.approx(0.1 + -0.1)
    assert alpha["wrc_plus"] is not None
    assert alpha["era_plus"] is not None
    assert alpha["fip"] is not None
