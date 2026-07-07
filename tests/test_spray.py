from db.models import (
    BatterSpraySeasonStats,
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
from stats.spray import compute_batter_spray


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
        source_play_id=source_play_id, game_id=game.id, inning=1, half="top",
        batter_player_season_id=batter_ps_id, ab=1, h=1, hitpull=hitpull,
    )


def test_compute_batter_spray_labels_plurality_bucket(session):
    league_season, team_season, game = _build_fixture(session)
    batter = _add_player(session, team_season, source_id=1, bats="R")

    # RHH: adj_pull = -hitpull, bucketed against fixed +/-15 thirds of the
    # 90-degree fan. Two pulled (-30 -> adj +30), one center (5 -> adj -5),
    # one oppo (30 -> adj -30).
    session.add_all(
        [
            _pa(game, batter.id, hitpull=-30, source_play_id=1),
            _pa(game, batter.id, hitpull=-30, source_play_id=2),
            _pa(game, batter.id, hitpull=5, source_play_id=3),
            _pa(game, batter.id, hitpull=30, source_play_id=4),
        ]
    )
    session.commit()

    count = compute_batter_spray(session, league_season.id)
    assert count == 1

    row = session.query(BatterSpraySeasonStats).filter_by(player_season_id=batter.id).one()
    assert row.pull_count == 2
    assert row.center_count == 1
    assert row.oppo_count == 1
    assert row.tendency_label == "pull"


def test_compute_batter_spray_at_exact_third_boundary(session):
    league_season, team_season, game = _build_fixture(session)
    # LHH: adj_pull = hitpull as-is. Exactly +/-15 sits in "center" (bucket
    # boundaries are `> 15` for pull and `< -15` for oppo, not >=/<=).
    batter = _add_player(session, team_season, source_id=1, bats="L")
    session.add_all(
        [
            _pa(game, batter.id, hitpull=15, source_play_id=1),
            _pa(game, batter.id, hitpull=-15, source_play_id=2),
        ]
    )
    session.commit()

    compute_batter_spray(session, league_season.id)
    row = session.query(BatterSpraySeasonStats).filter_by(player_season_id=batter.id).one()
    assert row.center_count == 2
    assert row.pull_count == 0
    assert row.oppo_count == 0


def test_compute_batter_spray_skips_switch_hitters(session):
    league_season, team_season, game = _build_fixture(session)
    switch = _add_player(session, team_season, source_id=2, bats="S")

    session.add(_pa(game, switch.id, hitpull=-30, source_play_id=1))
    session.commit()

    count = compute_batter_spray(session, league_season.id)
    assert count == 0
    assert session.query(BatterSpraySeasonStats).count() == 0


def test_compute_batter_spray_returns_zero_without_batted_ball_data(session):
    league_season, _team_season, _game = _build_fixture(session)
    assert compute_batter_spray(session, league_season.id) == 0
