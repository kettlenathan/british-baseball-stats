import pytest

from db.models import (
    Game,
    League,
    LeagueSeason,
    PlateAppearance,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from stats.league_context import _percentile, _pull_tertiles


def _build_fixture(session):
    league = League(code="test", name="Test League", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()

    league_season = LeagueSeason(
        league_id=league.id, season_id=season.id, source_tournament_id=1, competition_slug="2026-test"
    )
    session.add(league_season)
    session.flush()

    team = Team(name="Test Team")
    session.add(team)
    session.flush()
    team_season = TeamSeason(
        team_id=team.id, league_season_id=league_season.id, source_team_id=100, display_name="Test Team"
    )
    session.add(team_season)
    session.flush()

    game = Game(
        source_id=1000,
        league_season_id=league_season.id,
        home_team_season_id=team_season.id,
        away_team_season_id=team_season.id,
        status="final",
    )
    session.add(game)
    session.flush()

    return league_season, team_season, game


def _add_player(session, team_season, source_id, bats):
    player = Player(source_id=source_id, full_name=f"Player {source_id}", bats=bats)
    session.add(player)
    session.flush()
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(player_season)
    session.flush()
    return player_season


def _pa(game, batter_ps_id, hitpull, source_play_id):
    return PlateAppearance(
        source_play_id=source_play_id,
        game_id=game.id,
        inning=1,
        half="top",
        batter_player_season_id=batter_ps_id,
        ab=1,
        h=1,
        hitpull=hitpull,
    )


def test_percentile_basic():
    assert _percentile([1, 2, 3, 4, 5], 0.5) == pytest.approx(3.0)
    assert _percentile([0, 10, 20], 1 / 3) == pytest.approx(6.666666, rel=1e-4)


def test_pull_tertiles_adjusts_for_batter_handedness(session):
    league_season, team_season, game = _build_fixture(session)
    rhh = _add_player(session, team_season, source_id=1, bats="R")
    lhh = _add_player(session, team_season, source_id=2, bats="L")

    # RHH pulling to left field (negative) and LHH pulling to right field
    # (positive) should both adjust to the same "pulled" sign.
    session.add_all(
        [
            _pa(game, rhh.id, hitpull=-30, source_play_id=1),
            _pa(game, lhh.id, hitpull=30, source_play_id=2),
        ]
    )
    session.commit()

    low, high = _pull_tertiles(session, league_season.id)
    assert low == pytest.approx(30.0)
    assert high == pytest.approx(30.0)


def test_pull_tertiles_excludes_switch_hitters_and_unknown_handedness(session):
    league_season, team_season, game = _build_fixture(session)
    switch = _add_player(session, team_season, source_id=3, bats="S")
    unknown = _add_player(session, team_season, source_id=4, bats=None)
    rhh = _add_player(session, team_season, source_id=5, bats="R")

    session.add_all(
        [
            _pa(game, switch.id, hitpull=40, source_play_id=1),
            _pa(game, unknown.id, hitpull=-40, source_play_id=2),
            _pa(game, rhh.id, hitpull=-10, source_play_id=3),
        ]
    )
    session.commit()

    low, high = _pull_tertiles(session, league_season.id)
    # Only the RHH's single PA (adjusted to +10) should be in the corpus.
    assert low == pytest.approx(10.0)
    assert high == pytest.approx(10.0)


def test_pull_tertiles_returns_none_when_no_batted_balls(session):
    league_season, _team_season, _game = _build_fixture(session)
    assert _pull_tertiles(session, league_season.id) == (None, None)
