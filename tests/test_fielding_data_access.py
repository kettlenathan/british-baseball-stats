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
        data_access.team_catcher_throwing,
        data_access.player_catcher_throwing,
        data_access.league_catcher_throwing,
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
        for row in rows:
            position, games, po, a, e, dp = row[:6]
            sba, csb, pb = (list(row[6:]) + [0, 0, 0])[:3]
            session.add(
                FieldingSeasonStats(
                    player_season_id=player_season.id, position=position,
                    games=games, appearances=games, po=po, a=a, e=e, dp=dp,
                    sba=sba, csb=csb, pb=pb,
                )
            )
        session.flush()

    add_fielding("Bats", "Sam Shortstop", 1, [("SS", 10, 20, 30, 6, 3), ("3B", 2, 3, 4, 1, 0)])
    add_fielding("Bats", "Ryan Reserve", 2, [("SS", 3, 4, 6, 2, 0), ("DH", 5, 0, 0, 0, 0)])
    add_fielding("Bats", "Chris Clean", 3, [("CF", 12, 25, 1, 0, 0)])
    add_fielding("Gloves", "Pat Perfect", 4, [("SS", 12, 24, 36, 2, 4)])
    # Catchers: a busy one who throws nobody out, a backup with a good arm,
    # and a pitcher carrying his own share of the steals allowed.
    add_fielding("Bats", "Cal CATCHER", 5, [("C", 15, 90, 10, 3, 1, 40, 2, 6)])
    add_fielding("Bats", "Barry BACKUP", 6, [("C", 4, 20, 3, 0, 0, 5, 5, 1)])
    add_fielding("Bats", "Pete PITCHER", 7, [("P", 9, 2, 5, 1, 0, 8, 0, 0)])
    add_fielding("Gloves", "Gary GLOVE", 8, [("C", 12, 70, 8, 2, 1, 25, 5, 3)])
    session.commit()
    return league_season.id


def test_team_fielding_by_position_sums_players_and_orders_like_a_scorecard(session):
    league_season_id = _build_fixture(session)

    df = data_access.team_fielding_by_position(league_season_id, "Bats")

    # Scorecard order (P, C, 3B, SS, CF), not alphabetical or by errors.
    assert df["position"].tolist() == ["P", "C", "3B", "SS", "CF"]
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
    assert "Barry BACKUP" not in set(df["player"])  # zero errors
    shortstops = df[df["position"] == "SS"]
    # Scorecard order across positions, biggest contributor first within one.
    assert shortstops["player"].tolist() == ["Sam Shortstop", "Ryan Reserve"]
    assert df["position"].tolist() == ["P", "C", "3B", "SS", "SS"]


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


def test_team_catcher_throwing_computes_attempts_and_cs_rate(session):
    league_season_id = _build_fixture(session)

    df = data_access.team_catcher_throwing(league_season_id, "Bats")

    # Busiest catcher first, and only catchers — the pitcher's share of the
    # steals allowed must not appear in a catcher table.
    assert df["player"].tolist() == ["Cal CATCHER", "Barry BACKUP"]
    starter = df.iloc[0]
    assert starter["sb_against"] == 40 and starter["cs"] == 2
    # Attempts are steals allowed PLUS runners caught, not steals alone.
    assert starter["sb_att"] == 42
    assert starter["cs_pct"] == pytest.approx(2 / 42)
    backup = df.iloc[1]
    assert backup["sb_att"] == 10 and backup["cs_pct"] == pytest.approx(0.5)


def test_catcher_throwing_excludes_catchers_never_run_on(session):
    league_season_id = _build_fixture(session)
    player = Player(source_id=99, full_name="Untested CATCHER")
    session.add(player)
    session.flush()
    ts_id = session.query(TeamSeason).filter_by(display_name="Bats").one().id
    player_season = PlayerSeason(player_id=player.id, team_season_id=ts_id)
    session.add(player_season)
    session.flush()
    session.add(FieldingSeasonStats(player_season_id=player_season.id, position="C", games=2))
    session.commit()
    data_access.team_catcher_throwing.clear()

    df = data_access.team_catcher_throwing(league_season_id, "Bats")

    # Zero attempts means no rate to report — a 0-for-0 catcher shown as 0.0%
    # would read as "never throws anyone out".
    assert "Untested CATCHER" not in set(df["player"])


def test_player_catcher_throwing_is_empty_for_a_non_catcher(session):
    league_season_id = _build_fixture(session)
    assert data_access.player_catcher_throwing("Pete PITCHER", league_season_id).empty
    assert not data_access.player_catcher_throwing("Cal CATCHER", league_season_id).empty


def test_league_catcher_throwing_pools_every_catcher_in_the_season(session):
    league_season_id = _build_fixture(session)

    league = data_access.league_catcher_throwing(league_season_id)

    # 40+5+25 allowed, 2+5+5 caught — the pitcher's 8 are excluded.
    assert league["sb_against"] == 70
    assert league["cs"] == 12
    assert league["sb_att"] == 82
    assert league["cs_pct"] == pytest.approx(12 / 82)


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
    # 1 (P) + 3 (C) + 1 (3B) + 6+2 (SS) + 1 (UNK)
    assert int(df["e"].sum()) == 14
