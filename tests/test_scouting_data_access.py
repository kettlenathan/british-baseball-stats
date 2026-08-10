import datetime as dt

import pandas as pd
import pytest

from app.components import data_access
from db.models import (
    BatterPitcherMatchup,
    BatterSpraySeasonStats,
    BattingSeasonStats,
    BattingTrueTalent,
    Game,
    League,
    LeagueSeason,
    LeagueSeasonContext,
    PitchingGameLine,
    PitchingSeasonStats,
    PitchingTrueTalent,
    PlateAppearance,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)

_CACHED_FUNCTIONS = (
    "next_fixtures",
    "scouting_hitters",
    "scouting_pitching_staff",
    "roster_vs_pitcher",
    "batters_vs_hand",
    "pitcher_vs_hands",
    "league_batting_component_totals",
    "lineup_recommendation",
    "data_freshness",
)


@pytest.fixture(autouse=True)
def _patch_get_session(session, monkeypatch):
    monkeypatch.setattr(data_access, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    yield
    for name in _CACHED_FUNCTIONS:
        getattr(data_access, name).clear()


def _fixture(session):
    """One league-season: Us vs Them, one played game with play-by-play, one
    scheduled fixture between the same teams next weekend."""
    league = League(code="nbl", name="NBL", tier="senior", is_senior=True)
    year = Season(year=2026)
    session.add_all([league, year])
    session.flush()
    ls = LeagueSeason(league_id=league.id, season_id=year.id, source_tournament_id=1, competition_slug="2026-nbl")
    session.add(ls)
    session.flush()
    session.add(LeagueSeasonContext(league_season_id=ls.id, lg_woba=0.340, lg_era=5.00, lg_fip=5.00, fip_constant=4.0))

    us_team, them_team = Team(name="Us"), Team(name="Them")
    session.add_all([us_team, them_team])
    session.flush()
    us = TeamSeason(team_id=us_team.id, league_season_id=ls.id, source_team_id=1, display_name="Us")
    them = TeamSeason(team_id=them_team.id, league_season_id=ls.id, source_team_id=2, display_name="Them")
    session.add_all([us, them])
    session.flush()

    def player(source_id, name, ts, bats=None, throws=None):
        p = Player(source_id=source_id, full_name=name, bats=bats, throws=throws)
        session.add(p)
        session.flush()
        ps = PlayerSeason(player_id=p.id, team_season_id=ts.id)
        session.add(ps)
        session.flush()
        return ps

    our_batter = player(1, "Our Batter", us, bats="R")
    their_slugger = player(2, "Their Slugger", them, bats="L")
    their_scrub = player(3, "Their Scrub", them, bats="R")
    their_ace = player(4, "Their Ace", them, throws="L")
    their_reliever = player(5, "Their Reliever", them, throws="R")

    session.add_all(
        [
            BattingSeasonStats(player_season_id=their_slugger.id, pa=40, ab=35, h=18, doubles=4, hr=3, bb=5, so=4),
            BattingSeasonStats(player_season_id=their_scrub.id, pa=30, ab=28, h=4, bb=2, so=12),
            BattingSeasonStats(player_season_id=our_batter.id, pa=35, ab=30, h=10, doubles=2, hr=1, bb=5, so=6),
        ]
    )
    session.add_all(
        [
            BattingTrueTalent(player_season_id=their_slugger.id, pa=40, observed_woba=0.55, shrunk_woba=0.45),
            BattingTrueTalent(player_season_id=their_scrub.id, pa=30, observed_woba=0.15, shrunk_woba=0.28),
            BattingTrueTalent(player_season_id=our_batter.id, pa=35, observed_woba=0.40, shrunk_woba=0.38),
        ]
    )
    session.add(
        BatterSpraySeasonStats(
            player_season_id=their_slugger.id, pull_count=10, center_count=3, oppo_count=2, tendency_label="pull"
        )
    )
    session.add_all(
        [
            PitchingSeasonStats(
                player_season_id=their_ace.id, outs_recorded=45, h=12, er=6, bb=5, so=20, bf=70,
                fps_pa=60, fps_strikes=40,
            ),
            PitchingSeasonStats(player_season_id=their_reliever.id, outs_recorded=9, h=3, er=1, bb=2, so=4, bf=15),
        ]
    )
    session.add(PitchingTrueTalent(player_season_id=their_ace.id, ip=15.0, observed_fip=3.2, shrunk_fip=3.8))

    played = Game(
        source_id=100, league_season_id=ls.id, game_date=dt.date(2026, 8, 2),
        home_team_season_id=them.id, away_team_season_id=us.id,
        home_score=4, away_score=2, status="final",
    )
    upcoming = Game(
        source_id=101, league_season_id=ls.id, game_date=dt.date(2026, 8, 16),
        home_team_season_id=us.id, away_team_season_id=them.id, status="scheduled", venue="Home Field",
    )
    session.add_all([played, upcoming])
    session.flush()

    # Them were home, so they pitch the top half: Their Ace started, and
    # Our Batter faced him leading off.
    session.add_all(
        [
            PlateAppearance(
                source_play_id=1, game_id=played.id, inning=1, half="top",
                batter_player_season_id=our_batter.id, pitcher_player_season_id=their_ace.id,
                ab=1, h=1, doubles=1,
            ),
            PlateAppearance(
                source_play_id=2, game_id=played.id, inning=4, half="top",
                batter_player_season_id=our_batter.id, pitcher_player_season_id=their_reliever.id,
                ab=1, so=1,
            ),
            # Bottom half: Their Slugger bats against Our Batter (two-way).
            PlateAppearance(
                source_play_id=3, game_id=played.id, inning=1, half="bottom",
                batter_player_season_id=their_slugger.id, pitcher_player_season_id=our_batter.id,
                ab=1, h=1, hr=1,
            ),
        ]
    )
    session.add_all(
        [
            PitchingGameLine(game_id=played.id, player_season_id=their_ace.id, team_season_id=them.id, outs_recorded=15),
            PitchingGameLine(
                game_id=played.id, player_season_id=their_reliever.id, team_season_id=them.id, outs_recorded=6
            ),
        ]
    )
    session.add(
        BatterPitcherMatchup(
            batter_player_season_id=our_batter.id, pitcher_player_season_id=their_ace.id,
            pa=6, ab=5, h=3, doubles=1, hr=1, bb=1, so=1,
        )
    )
    session.commit()
    return ls


def test_next_fixtures_finds_upcoming_opponent(session):
    ls = _fixture(session)
    fixtures = data_access.next_fixtures(ls.id, "Us")
    assert len(fixtures) == 1
    assert fixtures.iloc[0]["opponent"] == "Them"
    assert fixtures.iloc[0]["home_away"] == "Home"
    # The played game must not appear; unknown team yields empty.
    assert data_access.next_fixtures(ls.id, "Nobody").empty


def test_scouting_hitters_ranked_by_true_talent(session):
    ls = _fixture(session)
    hitters = data_access.scouting_hitters(ls.id, "Them")
    assert list(hitters["player"]) == ["Their Slugger", "Their Scrub"]
    slugger = hitters.iloc[0]
    assert slugger["shrunk_woba"] == pytest.approx(0.45)
    assert slugger["tendency"] == "pull"
    assert slugger["bats"] == "L"
    assert slugger["pa"] == 40


def test_scouting_pitching_staff_ranks_starter_first(session):
    ls = _fixture(session)
    staff = data_access.scouting_pitching_staff(ls.id, "Them")
    assert list(staff["player"]) == ["Their Ace", "Their Reliever"]
    ace = staff.iloc[0]
    assert ace["throws"] == "L"
    assert ace["gs"] == 1
    assert ace["evidence"] is not None
    assert ace["fps_pct"] == pytest.approx(40 / 60)
    assert ace["shrunk_fip"] == pytest.approx(3.8)
    reliever = staff.iloc[1]
    assert reliever["gs"] == 0
    assert pd.isna(reliever["evidence"])


def test_roster_vs_pitcher(session):
    ls = _fixture(session)
    history = data_access.roster_vs_pitcher(ls.id, "Us", "Their Ace")
    assert len(history) == 1
    row = history.iloc[0]
    assert row["player"] == "Our Batter"
    assert row["pa"] == 6
    assert row["avg"] == pytest.approx(3 / 5)
    assert data_access.roster_vs_pitcher(ls.id, "Us", "Their Reliever").empty


def test_batters_vs_hand_and_pitcher_vs_hands(session):
    _fixture(session)
    vs_lhp = data_access.batters_vs_hand(["Our Batter"], "L")
    assert len(vs_lhp) == 1
    assert vs_lhp.iloc[0]["pa"] == 1  # only the PA against the lefty ace
    assert vs_lhp.iloc[0]["woba"] > 0  # the double

    splits = data_access.pitcher_vs_hands("Our Batter")
    assert list(splits["vs_hand"]) == ["L"]  # faced only the lefty slugger
    assert splits.iloc[0]["pa"] == 1


def test_lineup_recommendation_end_to_end(session):
    ls = _fixture(session)
    out = data_access.lineup_recommendation(ls.id, "Us", ["Our Batter", "Unknown Sub"], vs_throws="L")
    result = out["result"]
    # Nine or fewer available: everyone plays, nobody benched.
    assert sorted(result.order) == ["Our Batter", "Unknown Sub"]
    assert result.expected_runs > 0
    assert out["bench"].empty
    lineup = out["lineup"]
    assert list(lineup["slot"]) == [1, 2]
    assert set(lineup["player"]) == {"Our Batter", "Unknown Sub"}
    # The lineup table explains slots in box-score stats, not model values.
    assert {"avg", "obp", "slg", "k_pct"} <= set(lineup.columns)
    # Rationale is phrased from season stats / lack thereof, never wOBA.
    assert any("no season data" in line for line in result.rationale)
    assert not any("wOBA" in line for line in result.rationale)
    profiles = out["profiles"]
    assert len(profiles) == 2
    sub = profiles[profiles["player"] == "Unknown Sub"].iloc[0]
    assert sub["pa"] == 0
    known = profiles[profiles["player"] == "Our Batter"].iloc[0]
    assert known["vs_hand_pa"] == 1


def test_lineup_recommendation_benches_beyond_nine(session):
    ls = _fixture(session)
    subs = [f"Sub {i}" for i in range(10)]
    out = data_access.lineup_recommendation(ls.id, "Us", ["Our Batter", *subs], vs_throws="L")
    result = out["result"]
    # Exactly nine start; Our Batter (above-league talent) must be among them.
    assert len(result.order) == 9
    assert "Our Batter" in result.order
    assert len(out["lineup"]) == 9
    bench = out["bench"]
    assert len(bench) == 2
    assert set(bench["player"]) <= set(subs)
    # Someone is named first bat off the bench for each hand.
    roles = " ".join(bench["role"])
    assert "vs LHP" in roles
    assert "vs RHP" in roles
