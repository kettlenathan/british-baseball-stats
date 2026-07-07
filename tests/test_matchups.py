from db.models import (
    BatterPitcherMatchup,
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
from stats.matchups import compute_matchups


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


def _add_player(session, team_season, source_id):
    player = Player(source_id=source_id, full_name=f"Player {source_id}")
    session.add(player)
    session.flush()
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(player_season)
    session.flush()
    return player_season


def test_compute_matchups_sums_results_per_batter_pitcher_pair(session):
    league_season, team_season, game = _build_fixture(session)
    batter = _add_player(session, team_season, source_id=1)
    pitcher_a = _add_player(session, team_season, source_id=2)
    pitcher_b = _add_player(session, team_season, source_id=3)

    session.add_all(
        [
            PlateAppearance(
                source_play_id=1, game_id=game.id, inning=1, half="top",
                batter_player_season_id=batter.id, pitcher_player_season_id=pitcher_a.id,
                ab=1, h=1,
            ),
            PlateAppearance(
                source_play_id=2, game_id=game.id, inning=2, half="top",
                batter_player_season_id=batter.id, pitcher_player_season_id=pitcher_a.id,
                ab=1, so=1,
            ),
            PlateAppearance(
                source_play_id=3, game_id=game.id, inning=3, half="top",
                batter_player_season_id=batter.id, pitcher_player_season_id=pitcher_b.id,
                ab=1, hr=1, h=1,
            ),
            # Unresolved pitcher — should be excluded from matchups entirely.
            PlateAppearance(
                source_play_id=4, game_id=game.id, inning=4, half="top",
                batter_player_season_id=batter.id, pitcher_player_season_id=None,
                ab=1, h=1,
            ),
        ]
    )
    session.commit()

    count = compute_matchups(session, league_season.id)
    assert count == 2

    vs_a = session.query(BatterPitcherMatchup).filter_by(
        batter_player_season_id=batter.id, pitcher_player_season_id=pitcher_a.id
    ).one()
    assert vs_a.pa == 2
    assert vs_a.ab == 2
    assert vs_a.h == 1
    assert vs_a.so == 1

    vs_b = session.query(BatterPitcherMatchup).filter_by(
        batter_player_season_id=batter.id, pitcher_player_season_id=pitcher_b.id
    ).one()
    assert vs_b.pa == 1
    assert vs_b.hr == 1


def test_compute_matchups_rerun_is_idempotent(session):
    league_season, team_season, game = _build_fixture(session)
    batter = _add_player(session, team_season, source_id=1)
    pitcher = _add_player(session, team_season, source_id=2)
    session.add(
        PlateAppearance(
            source_play_id=1, game_id=game.id, inning=1, half="top",
            batter_player_season_id=batter.id, pitcher_player_season_id=pitcher.id, ab=1, h=1,
        )
    )
    session.commit()

    compute_matchups(session, league_season.id)
    compute_matchups(session, league_season.id)

    rows = session.query(BatterPitcherMatchup).all()
    assert len(rows) == 1
    assert rows[0].pa == 1
