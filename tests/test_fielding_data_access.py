"""Read layer for the errors-by-position views (Team Page, Player Page,
Scouting Report)."""

import pytest

from app.components import data_access
from db.models import (
    FieldingSeasonStats,
    League,
    LeagueSeason,
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
        data_access.team_fielding_by_position,
        data_access.team_position_error_players,
        data_access.player_fielding_by_position,
        data_access.league_fielding_by_position,
    ):
        fn.clear()


def _build_fixture(session):
    league = League(code="nbl", name="National Baseball League", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    league_season = LeagueSeason(
        league_id=league.id, season_id=season.id, source_tournament_id=1, competition_slug="2026-nbl"
    )
    session.add(league_season)
    session.flush()

    team_seasons = {}
    for index, name in enumerate(("Bats", "Gloves")):
        team = Team(name=name)
        session.add(team)
        session.flush()
        team_season = TeamSeason(
            team_id=team.id,
            league_season_id=league_season.id,
            source_team_id=500 + index,
            display_name=name,
        )
        session.add(team_season)
        session.flush()
        team_seasons[name] = team_season

    def add_fielding(team_name, player_name, source_id, rows):
        player = Player(source_id=source_id, full_name=player_name)
        session.add(player)
        session.flush()
        player_season = PlayerSeason(
            player_id=player.id, team_season_id=team_seasons[team_name].id
        )
        session.add(player_season)
        session.flush()
        for position, games, po, a, e, dp in rows:
            session.add(
                FieldingSeasonStats(
                    player_season_id=player_season.id, position=position,
                    games=games, appearances=games, po=po, a=a, e=e, dp=dp,
                )
            )
        session.flush()

    add_fielding("Bats", "Sam Shortstop", 1, [("SS", 10, 20, 30, 6, 3), ("3B", 2, 3, 4, 1, 0)])
    add_fielding("Bats", "Ryan Reserve", 2, [("SS", 3, 4, 6, 2, 0), ("DH", 5, 0, 0, 0, 0)])
    add_fielding("Bats", "Chris Clean", 3, [("CF", 12, 25, 1, 0, 0)])
    add_fielding("Gloves", "Pat Perfect", 4, [("SS", 12, 24, 36, 2, 4)])
    session.commit()
    return league_season.id


def test_team_fielding_by_position_sums_players_and_orders_like_a_scorecard(session):
    league_season_id = _build_fixture(session)

    df = data_access.team_fielding_by_position(league_season_id, "Bats")

    # Scorecard order (3B before SS before CF), not alphabetical or by errors.
    assert df["position"].tolist() == ["3B", "SS", "CF"]
    shortstop = df[df["position"] == "SS"].iloc[0]
    assert shortstop["e"] == 8  # 6 + 2, summed across the two players
    assert shortstop["po"] == 24 and shortstop["a"] == 36 and shortstop["g"] == 13
    assert shortstop["fpct"] == pytest.approx((24 + 36) / (24 + 36 + 8))


def test_team_fielding_by_position_drops_non_fielding_lineup_slots(session):
    league_season_id = _build_fixture(session)

    df = data_access.team_fielding_by_position(league_season_id, "Bats")

    assert "DH" not in set(df["position"])


def test_team_fielding_by_position_is_empty_for_an_unknown_team(session):
    league_season_id = _build_fixture(session)
    assert data_access.team_fielding_by_position(league_season_id, "Nobody").empty


def test_team_position_error_players_lists_only_players_with_errors(session):
    league_season_id = _build_fixture(session)

    df = data_access.team_position_error_players(league_season_id, "Bats")

    assert "Chris Clean" not in set(df["player"])  # zero errors
    shortstops = df[df["position"] == "SS"]
    # Scorecard order across positions, biggest contributor first within one.
    assert shortstops["player"].tolist() == ["Sam Shortstop", "Ryan Reserve"]
    assert df["position"].tolist() == ["3B", "SS", "SS"]


def test_player_fielding_by_position_orders_by_errors(session):
    league_season_id = _build_fixture(session)

    df = data_access.player_fielding_by_position("Sam Shortstop", league_season_id)

    assert df["position"].tolist() == ["SS", "3B"]
    assert df.iloc[0]["e"] == 6


def test_player_fielding_by_position_sums_career_when_scope_is_none(session):
    league_season_id = _build_fixture(session)

    scoped = data_access.player_fielding_by_position("Sam Shortstop", league_season_id)
    career = data_access.player_fielding_by_position("Sam Shortstop", None)

    # Only one season exists, so career must match it exactly rather than
    # double-count or drop the season filter's rows.
    assert career["e"].tolist() == scoped["e"].tolist()
    assert career["position"].tolist() == scoped["position"].tolist()


def test_player_fielding_by_position_is_empty_for_an_unknown_player(session):
    _build_fixture(session)
    assert data_access.player_fielding_by_position("Nobody At All", None).empty


def test_league_fielding_by_position_averages_errors_across_teams(session):
    league_season_id = _build_fixture(session)

    df = data_access.league_fielding_by_position(league_season_id)

    shortstop = df[df["position"] == "SS"].iloc[0]
    assert shortstop["e"] == 10  # 6 + 2 (Bats) + 2 (Gloves)
    assert shortstop["e_per_team"] == pytest.approx(5.0)  # two teams in the league


def test_unattributed_errors_are_kept_so_positions_add_up_to_the_team_total(session):
    """UNK is surfaced, not filtered — dropping it would make the by-position
    numbers silently disagree with the team's real error total."""
    league_season_id = _build_fixture(session)
    player = Player(source_id=99, full_name="Unknown Spot")
    session.add(player)
    session.flush()
    team_season_id = session.query(TeamSeason).filter_by(display_name="Bats").one().id
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season_id)
    session.add(player_season)
    session.flush()
    session.add(
        FieldingSeasonStats(player_season_id=player_season.id, position="UNK", games=1, e=1)
    )
    session.commit()
    data_access.team_fielding_by_position.clear()

    df = data_access.team_fielding_by_position(league_season_id, "Bats")

    assert "UNK" in set(df["position"])
    assert df["position"].tolist()[-1] == "UNK"  # sorted last
    assert int(df["e"].sum()) == 10  # 6 + 1 (3B) + 2 (SS) + 1 (UNK)
