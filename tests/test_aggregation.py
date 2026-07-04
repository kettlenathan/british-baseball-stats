from db.models import (
    BattingGameLine,
    BattingSeasonStats,
    Game,
    League,
    LeagueSeason,
    PitchingGameLine,
    PitchingSeasonStats,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from stats.aggregation import aggregate_batting, aggregate_pitching


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
    opp_team = Team(name="Opponent Team")
    session.add(opp_team)
    session.flush()
    opp_team_season = TeamSeason(
        team_id=opp_team.id, league_season_id=league_season.id, source_team_id=101, display_name="Opponent Team"
    )
    session.add_all([team_season, opp_team_season])
    session.flush()

    player = Player(source_id=1, first_name="Test", last_name="Player", full_name="Test Player")
    session.add(player)
    session.flush()
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(player_season)
    session.flush()

    games = []
    for i in range(2):
        game = Game(
            source_id=1000 + i,
            league_season_id=league_season.id,
            home_team_season_id=team_season.id,
            away_team_season_id=opp_team_season.id,
            home_score=5,
            away_score=3,
            status="final",
        )
        session.add(game)
        games.append(game)
    session.flush()

    return player_season, team_season, games


def test_aggregate_batting_sums_across_games(session):
    player_season, team_season, games = _build_fixture(session)

    session.add(
        BattingGameLine(
            game_id=games[0].id, player_season_id=player_season.id, team_season_id=team_season.id,
            pa=4, ab=3, h=2, doubles=1, bb=1,
        )
    )
    session.add(
        BattingGameLine(
            game_id=games[1].id, player_season_id=player_season.id, team_season_id=team_season.id,
            pa=5, ab=4, h=1, hr=1, so=2,
        )
    )
    session.commit()

    count = aggregate_batting(session)
    assert count == 1

    row = session.query(BattingSeasonStats).filter_by(player_season_id=player_season.id).one()
    assert row.pa == 9
    assert row.ab == 7
    assert row.h == 3
    assert row.doubles == 1
    assert row.hr == 1
    assert row.bb == 1
    assert row.so == 2


def test_aggregate_pitching_sums_across_games(session):
    player_season, team_season, games = _build_fixture(session)

    session.add(
        PitchingGameLine(
            game_id=games[0].id, player_season_id=player_season.id, team_season_id=team_season.id,
            outs_recorded=18, h=5, er=3, bb=2, so=6, win=True,
        )
    )
    session.add(
        PitchingGameLine(
            game_id=games[1].id, player_season_id=player_season.id, team_season_id=team_season.id,
            outs_recorded=9, h=2, er=1, bb=1, so=3, loss=True,
        )
    )
    session.commit()

    count = aggregate_pitching(session)
    assert count == 1

    row = session.query(PitchingSeasonStats).filter_by(player_season_id=player_season.id).one()
    assert row.outs_recorded == 27
    assert row.h == 7
    assert row.er == 4
    assert row.bb == 3
    assert row.so == 9
    assert row.wins == 1
    assert row.losses == 1


def test_aggregate_batting_rerun_is_idempotent(session):
    player_season, team_season, games = _build_fixture(session)
    session.add(
        BattingGameLine(
            game_id=games[0].id, player_season_id=player_season.id, team_season_id=team_season.id,
            pa=4, ab=3, h=2,
        )
    )
    session.commit()

    aggregate_batting(session)
    aggregate_batting(session)  # re-run should not duplicate or change totals

    rows = session.query(BattingSeasonStats).filter_by(player_season_id=player_season.id).all()
    assert len(rows) == 1
    assert rows[0].pa == 4
