"""Tests for the app layer's division-aware queries — the division column
on the leaderboards and standings, the dual wRC+/ERA+ baselines, and the
Team Page's division placing."""

import pytest

from app.components import data_access
from app.components.filters import filter_by_division
from db.models import (
    BattingSeasonStats,
    BattingWar,
    Division,
    DivisionContext,
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
from stats.team_strength import compute_team_strength


@pytest.fixture(autouse=True)
def _patch_get_session(session, monkeypatch):
    monkeypatch.setattr(data_access, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    yield
    for fn in (
        data_access.list_divisions,
        data_access.division_environments,
        data_access.batting_leaderboard,
        data_access.pitching_leaderboard,
        data_access.standings,
        data_access.team_division,
    ):
        fn.clear()


def _build(session, *, with_divisions=True):
    """A league-season with two divisions of two teams each, where North is
    a much higher-scoring environment than South."""
    league = League(code="d3", name="Division 3", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    ls = LeagueSeason(
        league_id=league.id,
        season_id=season.id,
        source_tournament_id=1,
        competition_slug="2026-d3",
    )
    session.add(ls)
    session.flush()

    divisions = {}
    if with_divisions:
        # South declared first to prove display order follows sort_order,
        # not insertion or alphabetical order.
        for name, order in (("North", 0), ("South", 1)):
            d = Division(league_season_id=ls.id, name=name, sort_order=order)
            session.add(d)
            session.flush()
            divisions[name] = d
        session.add_all(
            [
                DivisionContext(division_id=divisions["North"].id, lg_woba=0.500, lg_era=15.0, games=10, pa=400),
                DivisionContext(division_id=divisions["South"].id, lg_woba=0.400, lg_era=8.0, games=10, pa=400),
            ]
        )
    session.add(LeagueSeasonContext(league_season_id=ls.id, lg_woba=0.450, lg_era=11.0))
    session.flush()

    team_seasons = {}
    for i, (name, division) in enumerate(
        [("N1", "North"), ("N2", "North"), ("S1", "South"), ("S2", "South")]
    ):
        team = Team(name=name)
        session.add(team)
        session.flush()
        ts = TeamSeason(
            team_id=team.id,
            league_season_id=ls.id,
            source_team_id=100 + i,
            display_name=name,
            division_id=divisions[division].id if with_divisions else None,
        )
        session.add(ts)
        session.flush()
        team_seasons[name] = ts

    return ls, team_seasons, divisions


def _add_batter(session, team_season, name, *, pa=100, woba=0.500):
    player = Player(full_name=name, display_name=name, identity_key=name)
    session.add(player)
    session.flush()
    ps = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(ps)
    session.flush()
    session.add(BattingSeasonStats(player_season_id=ps.id, pa=pa, ab=pa, h=pa // 3))
    session.add(BattingWar(player_season_id=ps.id, woba=woba, war=1.0, formula_version="test"))
    session.flush()
    return ps


def _add_pitcher(session, team_season, name, *, outs=63, er=7):
    player = Player(full_name=name, display_name=name, identity_key=name)
    session.add(player)
    session.flush()
    ps = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(ps)
    session.flush()
    session.add(PitchingSeasonStats(player_season_id=ps.id, outs_recorded=outs, er=er, h=10, bb=3, so=15))
    session.add(PitchingWar(player_season_id=ps.id, fip=4.0, war=1.0, formula_version="test"))
    session.flush()
    return ps


def _add_game(session, ls, home, away, *, source_id, home_score, away_score, phase="regular", division=None):
    game = Game(
        source_id=source_id,
        league_season_id=ls.id,
        home_team_season_id=home.id,
        away_team_season_id=away.id,
        home_score=home_score,
        away_score=away_score,
        status="final",
        phase=phase,
        division_id=division.id if division else None,
    )
    session.add(game)
    session.flush()
    return game


def test_list_divisions_reports_teams_in_published_order(session):
    ls, _, _ = _build(session)

    df = data_access.list_divisions(ls.id)

    assert list(df["division"]) == ["North", "South"]
    assert list(df["teams"]) == [2, 2]


def test_list_divisions_empty_when_none_recorded(session):
    ls, _, _ = _build(session, with_divisions=False)

    assert data_access.list_divisions(ls.id).empty


def test_batting_leaderboard_carries_both_baselines(session):
    """The same wOBA must rate differently against the league and against a
    division — that difference is the entire point of carrying two."""
    ls, teams, _ = _build(session)
    _add_batter(session, teams["N1"], "North Hitter", woba=0.500)
    _add_batter(session, teams["S1"], "South Hitter", woba=0.500)

    df = data_access.batting_leaderboard(ls.id).set_index("player")

    assert df.loc["North Hitter", "division"] == "North"
    assert df.loc["South Hitter", "division"] == "South"
    # Identical league-wide rating for identical wOBA...
    assert df.loc["North Hitter", "wrc_plus"] == df.loc["South Hitter", "wrc_plus"]
    # ...but the North hitter did it in the stronger-scoring division, so
    # against their own peers they rate lower than the South hitter does.
    assert df.loc["North Hitter", "wrc_plus_div"] < df.loc["South Hitter", "wrc_plus_div"]


def test_pitching_leaderboard_carries_both_baselines(session):
    ls, teams, _ = _build(session)
    _add_pitcher(session, teams["N1"], "North Arm")
    _add_pitcher(session, teams["S1"], "South Arm")

    df = data_access.pitching_leaderboard(ls.id).set_index("player")

    assert df.loc["North Arm", "era_plus"] == df.loc["South Arm", "era_plus"]
    # North allows more runs league-wide, so the same ERA is worth more there.
    assert df.loc["North Arm", "era_plus_div"] > df.loc["South Arm", "era_plus_div"]


def test_leaderboard_division_is_none_without_divisions(session):
    ls, teams, _ = _build(session, with_divisions=False)
    _add_batter(session, teams["N1"], "Somebody")

    df = data_access.batting_leaderboard(ls.id)

    assert df["division"].isna().all()
    assert df["wrc_plus_div"].isna().all()
    # The league-wide figure must still be produced.
    assert df["wrc_plus"].notna().all()


def test_standings_groups_by_division_in_published_order(session):
    ls, teams, divisions = _build(session)
    # Give South the best record in the league, to prove block order follows
    # sort_order rather than win percentage.
    _add_game(session, ls, teams["S1"], teams["S2"], source_id=1, home_score=10, away_score=0,
              division=divisions["South"])
    _add_game(session, ls, teams["N1"], teams["N2"], source_id=2, home_score=3, away_score=2,
              division=divisions["North"])

    df = data_access.standings(ls.id)

    assert list(df["division"].dropna().unique()) == ["North", "South"]
    north = df[df["division"] == "North"].reset_index(drop=True)
    assert north.loc[0, "team"] == "N1"


def test_standings_excludes_playoff_games_by_default(session):
    ls, teams, divisions = _build(session)
    _add_game(session, ls, teams["N1"], teams["N2"], source_id=1, home_score=3, away_score=2,
              division=divisions["North"])
    _add_game(session, ls, teams["N1"], teams["N2"], source_id=2, home_score=9, away_score=0,
              phase="playoff", division=divisions["North"])

    regular = data_access.standings(ls.id).set_index("team")
    assert regular.loc["N1", "w"] == 1

    data_access.standings.clear()
    everything = data_access.standings(ls.id, regular_season_only=False).set_index("team")
    assert everything.loc["N1", "w"] == 2


def test_team_division_reports_placing_within_its_own_division(session):
    ls, teams, divisions = _build(session)
    _add_game(session, ls, teams["N1"], teams["N2"], source_id=1, home_score=5, away_score=1,
              division=divisions["North"])
    # South's winner has a better record than N1 but must not affect N1's rank.
    _add_game(session, ls, teams["S1"], teams["S2"], source_id=2, home_score=20, away_score=0,
              division=divisions["South"])

    info = data_access.team_division(ls.id, "N2")

    assert info["division"] == "North"
    assert (info["rank"], info["of"]) == (2, 2)
    assert info["lg_woba"] == 0.500


def test_team_division_is_none_without_divisions(session):
    ls, _, _ = _build(session, with_divisions=False)

    assert data_access.team_division(ls.id, "N1") is None


def test_division_environments_exposes_the_scoring_gap(session):
    ls, teams, divisions = _build(session)
    _add_game(session, ls, teams["N1"], teams["N2"], source_id=1, home_score=15, away_score=13,
              division=divisions["North"])
    _add_game(session, ls, teams["S1"], teams["S2"], source_id=2, home_score=2, away_score=1,
              division=divisions["South"])

    df = data_access.division_environments(ls.id).set_index("division")

    assert df.loc["North", "r_per_team_game"] == 14.0
    assert df.loc["South", "r_per_team_game"] == 1.5
    assert df.loc["North", "lg_woba"] > df.loc["South", "lg_woba"]


def test_standings_carries_rating_and_sos_once_fitted(session):
    ls, teams, divisions = _build(session)
    # Alternating venues: with N1 always at home the fit would rightly credit
    # home advantage rather than N1, and no rating difference would emerge.
    for i in range(6):
        home, away = (teams["N1"], teams["N2"]) if i % 2 else (teams["N2"], teams["N1"])
        home_score, away_score = (8, 1) if i % 2 else (1, 8)
        _add_game(session, ls, home, away, source_id=i, home_score=home_score,
                  away_score=away_score, division=divisions["North"])
    compute_team_strength(session, ls.id)

    df = data_access.standings(ls.id).set_index("team")

    assert df.loc["N1", "rating"] > df.loc["N2", "rating"]
    assert df.loc["N1", "sos"] is not None


def test_standings_omits_rating_when_nothing_is_fitted(session):
    """The columns must not appear at all rather than arriving full of nulls,
    since the page keys its explanatory caption off their presence."""
    ls, teams, divisions = _build(session)
    _add_game(session, ls, teams["N1"], teams["N2"], source_id=1, home_score=3,
              away_score=2, division=divisions["North"])

    df = data_access.standings(ls.id)

    assert "rating" not in df.columns


def test_team_division_includes_strength_when_available(session):
    ls, teams, divisions = _build(session)
    # Alternating venues: with N1 always at home the fit would rightly credit
    # home advantage rather than N1, and no rating difference would emerge.
    for i in range(6):
        home, away = (teams["N1"], teams["N2"]) if i % 2 else (teams["N2"], teams["N1"])
        home_score, away_score = (8, 1) if i % 2 else (1, 8)
        _add_game(session, ls, home, away, source_id=i, home_score=home_score,
                  away_score=away_score, division=divisions["North"])
    compute_team_strength(session, ls.id)

    info = data_access.team_division(ls.id, "N1")

    assert info["rating"] > 0
    assert 0.5 < info["expected_win_pct"] <= 1.0
    assert info["sos"] is not None


def test_team_division_strength_fields_are_none_when_unfitted(session):
    ls, teams, divisions = _build(session)
    _add_game(session, ls, teams["N1"], teams["N2"], source_id=1, home_score=3,
              away_score=2, division=divisions["North"])

    info = data_access.team_division(ls.id, "N1")

    assert info["division"] == "North"
    assert info["rating"] is None


def test_filter_by_division_narrows_and_passes_through(session):
    ls, teams, _ = _build(session)
    _add_batter(session, teams["N1"], "North Hitter")
    _add_batter(session, teams["S1"], "South Hitter")
    df = data_access.batting_leaderboard(ls.id)

    assert list(filter_by_division(df, "North")["player"]) == ["North Hitter"]
    # None means "all divisions", not "no rows".
    assert len(filter_by_division(df, None)) == 2
