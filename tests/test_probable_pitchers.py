import datetime as dt

from db.models import (
    Game,
    League,
    LeagueSeason,
    PitchingGameLine,
    PlateAppearance,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from stats.probable_pitchers import identify_starter, probable_starters, staff_usage


def _base(session):
    league = League(code="nbl", name="NBL", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    ls = LeagueSeason(league_id=league.id, season_id=season.id, source_tournament_id=1, competition_slug="2026-nbl")
    session.add(ls)
    session.flush()
    home = Team(name="Home")
    away = Team(name="Away")
    session.add_all([home, away])
    session.flush()
    ts_home = TeamSeason(team_id=home.id, league_season_id=ls.id, source_team_id=1, display_name="Home")
    ts_away = TeamSeason(team_id=away.id, league_season_id=ls.id, source_team_id=2, display_name="Away")
    session.add_all([ts_home, ts_away])
    session.flush()
    return ls, ts_home, ts_away


def _pitcher(session, ts, source_id, name):
    player = Player(source_id=source_id, full_name=name)
    session.add(player)
    session.flush()
    ps = PlayerSeason(player_id=player.id, team_season_id=ts.id)
    session.add(ps)
    session.flush()
    return ps


def _batter(session, ts, source_id=999):
    return _pitcher(session, ts, source_id, f"Batter {source_id}")


def _game(session, ls, ts_home, ts_away, source_id, date):
    game = Game(
        source_id=source_id,
        league_season_id=ls.id,
        home_team_season_id=ts_home.id,
        away_team_season_id=ts_away.id,
        home_score=1,
        away_score=0,
        status="final",
        game_date=date,
    )
    session.add(game)
    session.flush()
    return game


def test_starter_identified_from_first_defensive_pa(session):
    ls, ts_home, ts_away = _base(session)
    starter = _pitcher(session, ts_home, 1, "Starter")
    reliever = _pitcher(session, ts_home, 2, "Reliever")
    batter = _batter(session, ts_away)
    game = _game(session, ls, ts_home, ts_away, 100, dt.date(2026, 6, 7))

    # Home team pitches the top half. Reliever appears later (higher inning /
    # play id) — the inning-1 pitcher must win even though the reliever's PA
    # row was inserted first.
    session.add_all(
        [
            PlateAppearance(
                source_play_id=205, game_id=game.id, inning=4, half="top",
                batter_player_season_id=batter.id, pitcher_player_season_id=reliever.id,
            ),
            PlateAppearance(
                source_play_id=201, game_id=game.id, inning=1, half="top",
                batter_player_season_id=batter.id, pitcher_player_season_id=starter.id,
            ),
            # Bottom half belongs to the away team's pitcher — must be ignored.
            PlateAppearance(
                source_play_id=202, game_id=game.id, inning=1, half="bottom",
                batter_player_season_id=batter.id, pitcher_player_season_id=reliever.id,
            ),
        ]
    )
    session.add_all(
        [
            PitchingGameLine(game_id=game.id, player_season_id=starter.id, team_season_id=ts_home.id, outs_recorded=12),
            PitchingGameLine(game_id=game.id, player_season_id=reliever.id, team_season_id=ts_home.id, outs_recorded=9),
        ]
    )
    session.commit()

    assert identify_starter(session, game, ts_home.id) == (starter.id, "play_by_play")


def test_starter_falls_back_to_max_outs_without_play_by_play(session):
    ls, ts_home, ts_away = _base(session)
    starter = _pitcher(session, ts_home, 1, "Starter")
    reliever = _pitcher(session, ts_home, 2, "Reliever")
    game = _game(session, ls, ts_home, ts_away, 100, dt.date(2026, 6, 7))
    session.add_all(
        [
            PitchingGameLine(game_id=game.id, player_season_id=reliever.id, team_season_id=ts_home.id, outs_recorded=6),
            PitchingGameLine(game_id=game.id, player_season_id=starter.id, team_season_id=ts_home.id, outs_recorded=15),
        ]
    )
    session.commit()

    assert identify_starter(session, game, ts_home.id) == (starter.id, "max_outs")
    assert identify_starter(session, game, ts_away.id) == (None, "unknown")


def test_recency_ranks_current_starter_over_early_season_workhorse(session):
    ls, ts_home, ts_away = _base(session)
    early = _pitcher(session, ts_home, 1, "Early Ace")
    current = _pitcher(session, ts_home, 2, "Current Ace")

    # "Early Ace" started 3 games in April/May then vanished (injury);
    # "Current Ace" started the 3 most recent games.
    dates = {
        early: [dt.date(2026, 4, 12), dt.date(2026, 4, 26), dt.date(2026, 5, 3)],
        current: [dt.date(2026, 7, 12), dt.date(2026, 7, 19), dt.date(2026, 7, 26)],
    }
    source_id = 100
    for pitcher, game_dates in dates.items():
        for date in game_dates:
            game = _game(session, ls, ts_home, ts_away, source_id, date)
            source_id += 1
            session.add(
                PitchingGameLine(
                    game_id=game.id, player_season_id=pitcher.id, team_season_id=ts_home.id, outs_recorded=15
                )
            )
    session.commit()

    ranked = probable_starters(session, ts_home.id)
    assert [r["player_season_id"] for r in ranked[:2]] == [current.id, early.id]
    assert ranked[0]["confidence"] == "High"
    assert "3 starts" in ranked[0]["evidence"]
    # All starts here were inferred via the max-outs fallback (no PA rows).
    assert "innings totals" in ranked[0]["evidence"]


def test_staff_usage_includes_relievers_and_ip_share(session):
    ls, ts_home, ts_away = _base(session)
    starter = _pitcher(session, ts_home, 1, "Starter")
    reliever = _pitcher(session, ts_home, 2, "Reliever")
    game = _game(session, ls, ts_home, ts_away, 100, dt.date(2026, 6, 7))
    session.add_all(
        [
            PitchingGameLine(game_id=game.id, player_season_id=starter.id, team_season_id=ts_home.id, outs_recorded=15),
            PitchingGameLine(
                game_id=game.id, player_season_id=reliever.id, team_season_id=ts_home.id, outs_recorded=6, save=True
            ),
        ]
    )
    session.commit()

    usage = staff_usage(session, ts_home.id)
    assert len(usage) == 2
    by_id = {row["player_season_id"]: row for row in usage}
    assert by_id[starter.id]["gs"] == 1
    assert by_id[reliever.id]["gs"] == 0
    assert by_id[reliever.id]["saves"] == 1
    assert abs(by_id[starter.id]["team_ip_share"] - 15 / 21) < 1e-9
    # Relievers never appear in probable_starters.
    assert [r["player_season_id"] for r in probable_starters(session, ts_home.id)] == [starter.id]


def test_empty_staff(session):
    _, ts_home, _ = _base(session)
    session.commit()
    assert staff_usage(session, ts_home.id) == []
    assert probable_starters(session, ts_home.id) == []
