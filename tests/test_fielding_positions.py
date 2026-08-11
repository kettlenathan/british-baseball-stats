"""Errors-by-position attribution: the narrative parser, the box-score/
narrative combination rule, and the season rollup.

The property that matters most here and is asserted repeatedly: summing the
per-position rows must reproduce the box score's own per-player totals, since
those are what the site itself publishes (see docs/fielding_metrics_plan.md).
"""

from db.models import (
    FieldingGameLine,
    FieldingSeasonStats,
    Game,
    League,
    LeagueSeason,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from scraper.scrape_boxscores import (
    UNKNOWN_POSITION,
    _extract_fielding_lines,
    _extract_narrative_errors,
)
from stats.aggregation import aggregate_fielding


def _record(pos, *, po=0, a=0, e=0, dp=0, teamid=500):
    return {"pos": pos, "field_po": po, "field_a": a, "field_e": e, "field_dp": dp, "teamid": teamid}


def _rows_by_position(rows):
    return {r["position"]: r for r in rows}


# --------------------------------------------------------------------------
# Narrative parsing
# --------------------------------------------------------------------------


def test_narrative_errors_map_position_numbers_to_the_fielding_team():
    game_plays = {
        "1": {
            # Top half: away team batting, so the HOME team is fielding.
            "top": [
                {"id": 1, "narrative": "STROMAN Zach reaches on fielding error. E6."},
                {"id": 2, "narrative": "HOPE Chris reaches on throwing error. E1T. STEERS Lewis scores."},
            ],
            # Bottom half: the away team is fielding.
            "bottom": [
                {"id": 3, "narrative": "PALLMANN Philip reaches on dropped fly error. E7."},
            ],
        }
    }

    errors = _extract_narrative_errors(game_plays, home_source_team_id=10, away_source_team_id=20)

    assert dict(errors[10]) == {"SS": 1, "P": 1}
    assert dict(errors[20]) == {"LF": 1}


def test_narrative_errors_ignore_surnames_beginning_with_e():
    game_plays = {
        "1": {"top": [{"id": 1, "narrative": "EVANS Tom singles. ELLIS Sam to 2nd. EDMONDS scores."}]}
    }
    assert _extract_narrative_errors(game_plays, 10, 20) == {}


def test_narrative_errors_counts_every_token_including_advancement():
    game_plays = {
        "1": {
            "top": [
                {
                    "id": 1,
                    "narrative": (
                        "DICKSON Ryan reaches on throwing error. E3T. "
                        "THEBERGE Marc to 3rd on E3T. DICKSON Ryan to 2nd on E3T."
                    ),
                }
            ]
        }
    }
    assert dict(_extract_narrative_errors(game_plays, 10, 20)[10]) == {"1B": 3}


def test_narrative_errors_survive_missing_or_malformed_play_by_play():
    assert _extract_narrative_errors({}, 10, 20) == {}
    assert _extract_narrative_errors(None, 10, 20) == {}
    assert _extract_narrative_errors({"1": {"top": [{"id": 1}]}}, 10, 20) == {}


def test_narrative_errors_skip_a_half_whose_fielding_team_is_unknown():
    game_plays = {"1": {"top": [{"id": 1, "narrative": "E6."}]}}
    assert _extract_narrative_errors(game_plays, home_source_team_id=None, away_source_team_id=20) == {}


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------


def test_single_position_record_attributes_everything_to_that_position():
    by_player = {1: [_record("SS", po=2, a=4, e=1, dp=1)]}

    rows = _extract_fielding_lines(by_player, {1: 100}, {1: 900}, {}, game_id=7)

    assert len(rows) == 1
    row = rows[0]
    assert row["position"] == "SS"
    assert (row["po"], row["a"], row["e"], row["dp"]) == (2, 4, 1, 1)
    assert row["appearances"] == 1
    assert row["game_id"] == 7 and row["player_season_id"] == 100 and row["team_season_id"] == 900


def test_multi_position_record_splits_errors_using_the_narrative():
    # One stint at SS then P, carrying 1 error — the narrative says it was at
    # shortstop, so it must not land on the pitcher.
    by_player = {1: [_record("SS/P", po=1, a=1, e=1)]}
    narrative = _extract_narrative_errors(
        {"1": {"top": [{"id": 1, "narrative": "reaches on fielding error. E6."}]}}, 500, 600
    )

    rows = _rows_by_position(_extract_fielding_lines(by_player, {1: 100}, {1: 900}, narrative, game_id=1))

    assert rows["SS"]["e"] == 1
    assert rows["P"]["e"] == 0
    # PO/A go to where the stint started, and both positions count as played.
    assert (rows["SS"]["po"], rows["SS"]["a"]) == (1, 1)
    assert rows["SS"]["appearances"] == 1 and rows["P"]["appearances"] == 1


def test_multi_position_errors_the_narrative_cannot_place_fall_back_to_unknown():
    by_player = {1: [_record("SS/P", e=2)]}

    rows = _rows_by_position(_extract_fielding_lines(by_player, {1: 100}, {1: 900}, {}, game_id=1))

    assert rows[UNKNOWN_POSITION]["e"] == 2
    assert rows["SS"]["e"] == 0 and rows["P"]["e"] == 0


def test_narrative_only_places_errors_within_the_positions_the_record_names():
    # The narrative says third base, but this player was never at third — the
    # error is theirs (the box score says so) but its position is unknowable.
    by_player = {1: [_record("SS/P", e=1)]}
    narrative = {500: __import__("collections").Counter({"3B": 1})}

    rows = _rows_by_position(_extract_fielding_lines(by_player, {1: 100}, {1: 900}, narrative, game_id=1))

    assert rows[UNKNOWN_POSITION]["e"] == 1
    assert "3B" not in rows


def test_single_position_records_retire_their_narrative_tokens_first():
    """A player who was unambiguously at shortstop and a player who passed
    through shortstop mid-stint must not both claim the same E6."""
    by_player = {
        1: [_record("SS", e=1)],
        2: [_record("SS/P", e=1)],
    }
    narrative = _extract_narrative_errors(
        {"1": {"top": [{"id": 1, "narrative": "fielding error. E6."}]}}, 500, 600
    )

    rows = _extract_fielding_lines(by_player, {1: 100, 2: 200}, {1: 900, 2: 900}, narrative, game_id=1)
    by_player_rows = {(r["player_season_id"], r["position"]): r for r in rows}

    assert by_player_rows[(100, "SS")]["e"] == 1
    assert by_player_rows[(200, "SS")]["e"] == 0
    assert by_player_rows[(200, UNKNOWN_POSITION)]["e"] == 1


def test_repeated_position_in_a_path_is_collapsed_but_keeps_its_order():
    # "P/SS/P" is one stint that started at pitcher — PO/A belong there.
    by_player = {1: [_record("P/SS/P", po=3, a=1, e=1)]}
    narrative = _extract_narrative_errors(
        {"1": {"top": [{"id": 1, "narrative": "fielding error. E6."}]}}, 500, 600
    )

    rows = _rows_by_position(_extract_fielding_lines(by_player, {1: 100}, {1: 900}, narrative, game_id=1))

    assert set(rows) == {"P", "SS"}
    assert rows["P"]["po"] == 3 and rows["P"]["a"] == 1
    assert rows["SS"]["e"] == 1


def test_totals_are_preserved_across_every_kind_of_record():
    """The invariant the whole feature rests on: per-position rows must sum
    back to the box score's own per-player totals."""
    by_player = {
        1: [_record("3B", po=1, a=3, e=2, dp=1), _record("SS/P", po=1, a=1, e=1)],
        2: [_record("C", po=8, a=1, e=1, dp=0)],
        3: [_record("", po=0, a=0, e=1)],
    }
    narrative = _extract_narrative_errors(
        {"1": {"top": [{"id": 1, "narrative": "E5. E5. E6. E2."}]}}, 500, 600
    )

    rows = _extract_fielding_lines(by_player, {1: 100, 2: 200, 3: 300}, {1: 900, 2: 900, 3: 900}, narrative, 1)

    for ps_id, records in ((100, by_player[1]), (200, by_player[2]), (300, by_player[3])):
        for field, source in (("po", "field_po"), ("a", "field_a"), ("e", "field_e"), ("dp", "field_dp")):
            assert sum(r[field] for r in rows if r["player_season_id"] == ps_id) == sum(
                rec[source] for rec in records
            ), f"{field} not preserved for player_season {ps_id}"


def test_record_with_no_position_and_no_activity_produces_no_row():
    assert _extract_fielding_lines({1: [_record("")]}, {1: 100}, {1: 900}, {}, 1) == []


def test_players_without_a_resolved_player_season_are_skipped():
    assert _extract_fielding_lines({1: [_record("SS", e=1)]}, {}, {}, {}, 1) == []


# --------------------------------------------------------------------------
# Season rollup
# --------------------------------------------------------------------------


def _build_league_season(session):
    league = League(code="test", name="Test League", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    league_season = LeagueSeason(
        league_id=league.id, season_id=season.id, source_tournament_id=1, competition_slug="2026-test"
    )
    session.add(league_season)
    session.flush()
    team = Team(name="Bats")
    opponent = Team(name="Gloves")
    session.add_all([team, opponent])
    session.flush()
    team_season = TeamSeason(
        team_id=team.id, league_season_id=league_season.id, source_team_id=500, display_name="Bats"
    )
    opponent_season = TeamSeason(
        team_id=opponent.id, league_season_id=league_season.id, source_team_id=501, display_name="Gloves"
    )
    session.add_all([team_season, opponent_season])
    session.flush()
    return league_season, team_season, opponent_season


def _add_player(session, team_season, name, source_id):
    player = Player(source_id=source_id, full_name=name)
    session.add(player)
    session.flush()
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(player_season)
    session.flush()
    return player_season


def _add_game(session, league_season, team_season, opponent_season, source_id):
    game = Game(
        source_id=source_id,
        league_season_id=league_season.id,
        home_team_season_id=team_season.id,
        away_team_season_id=opponent_season.id,
        status="final",
    )
    session.add(game)
    session.flush()
    return game


def test_aggregate_fielding_sums_per_position_and_counts_distinct_games(session):
    league_season, team_season, opponent_season = _build_league_season(session)
    player_season = _add_player(session, team_season, "Sam Fielder", 1)
    game_one = _add_game(session, league_season, team_season, opponent_season, 101)
    game_two = _add_game(session, league_season, team_season, opponent_season, 102)

    session.add_all(
        [
            FieldingGameLine(
                game_id=game_one.id, player_season_id=player_season.id, team_season_id=team_season.id,
                position="SS", appearances=1, po=2, a=3, e=1, dp=1,
            ),
            FieldingGameLine(
                game_id=game_two.id, player_season_id=player_season.id, team_season_id=team_season.id,
                position="SS", appearances=2, po=1, a=1, e=2, dp=0,
            ),
            FieldingGameLine(
                game_id=game_two.id, player_season_id=player_season.id, team_season_id=team_season.id,
                position="P", appearances=1, po=0, a=1, e=0, dp=0,
            ),
        ]
    )
    session.commit()

    aggregate_fielding(session, league_season.id)

    rows = {
        row.position: row
        for row in session.query(FieldingSeasonStats).filter_by(player_season_id=player_season.id)
    }
    assert (rows["SS"].po, rows["SS"].a, rows["SS"].e, rows["SS"].dp) == (3, 4, 3, 1)
    # Two games, three appearances — a player can hold a position twice in one game.
    assert rows["SS"].games == 2
    assert rows["SS"].appearances == 3
    assert rows["P"].a == 1 and rows["P"].games == 1


def test_aggregate_fielding_clears_rows_for_positions_that_no_longer_exist(session):
    league_season, team_season, opponent_season = _build_league_season(session)
    player_season = _add_player(session, team_season, "Sam Fielder", 1)
    game = _add_game(session, league_season, team_season, opponent_season, 101)

    session.add(
        FieldingGameLine(
            game_id=game.id, player_season_id=player_season.id, team_season_id=team_season.id,
            position="UNK", appearances=1, e=1,
        )
    )
    session.commit()
    aggregate_fielding(session, league_season.id)
    assert {r.position for r in session.query(FieldingSeasonStats)} == {"UNK"}

    # The fact rows are rebuilt with the error now placed at shortstop — the
    # stale UNK season row must not survive the recompute.
    session.query(FieldingGameLine).delete()
    session.add(
        FieldingGameLine(
            game_id=game.id, player_season_id=player_season.id, team_season_id=team_season.id,
            position="SS", appearances=1, e=1,
        )
    )
    session.commit()
    aggregate_fielding(session, league_season.id)

    assert {r.position for r in session.query(FieldingSeasonStats)} == {"SS"}
