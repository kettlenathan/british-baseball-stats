from scraper.scrape_boxscores import (
    _extract_lob,
    _extract_plate_appearances,
    _extract_risp_totals,
    _first_pitch_strike,
)


def _play(**overrides):
    base = {
        "id": 1, "playorder": 0, "pa": 0, "nopitch": 0,
        "batterid": 1, "pitcherid": 10, "inning": 1,
        "balls": 0, "strikes": 0,
        "ab": 0, "h": 0, "double": 0, "triple": 0, "homerun": 0,
        "bb": 0, "ibb": 0, "hbp": 0, "strikeout": 0, "sf": 0, "rbi": 0,
        "hitpull": 0, "hitdistance": 0, "hittype": 0,
        "runner1": 0, "runner2": 0, "runner3": 0,
        "runner1after": 0, "runner2after": 0, "runner3after": 0,
    }
    base.update(overrides)
    return base


def test_extract_risp_totals_only_counts_official_at_bats_with_runner_on_2nd_or_3rd():
    game_plays = {
        "1": {
            "top": [
                _play(batterid=1, ab=1, h=0, runner1=0, runner2=0, runner3=0),  # no RISP
                _play(batterid=1, ab=1, h=1, runner2=1020),  # RISP hit
                _play(batterid=2, ab=0, h=0, runner3=1020),  # walk with RISP — not an AB, excluded
                _play(batterid=1, ab=1, h=0, runner3=1020),  # RISP out
            ],
        },
    }
    ps_id_by_source = {1: 100, 2: 200}

    totals = _extract_risp_totals(game_plays, ps_id_by_source)

    assert totals[100] == {"risp_ab": 2, "risp_h": 1}
    assert 200 not in totals


def test_extract_risp_totals_skips_batters_with_no_player_season_mapping():
    game_plays = {"1": {"top": [_play(batterid=99, ab=1, h=1, runner2=1)]}}
    totals = _extract_risp_totals(game_plays, ps_id_by_source={})
    assert totals == {}


def test_extract_risp_totals_handles_missing_or_malformed_game_plays():
    assert _extract_risp_totals({}, {1: 100}) == {}
    assert _extract_risp_totals(None, {1: 100}) == {}


def test_extract_lob_reads_after_state_of_last_play_per_half_inning():
    game_plays = {
        "1": {
            # away team (top) leaves a runner on 2nd when the inning ends
            "top": [
                _play(runner2after=0),
                _play(runner1after=0, runner2after=1020, runner3after=0),
            ],
            # home team (bottom) leaves two runners on
            "bottom": [
                _play(runner1after=2010, runner2after=0, runner3after=2020),
            ],
        },
        "2": {
            "top": [_play()],  # bases empty, nobody left on
        },
    }

    home_lob, away_lob = _extract_lob(game_plays)

    assert away_lob == 1
    assert home_lob == 2


def test_extract_lob_returns_none_when_no_play_by_play_available():
    assert _extract_lob({}) == (None, None)
    assert _extract_lob(None) == (None, None)


PS_ID_BY_SOURCE = {1: 100, 10: 900}
GAME_ID = 555


def test_first_pitch_ball_in_play_is_a_strike_with_batted_ball_fields_populated():
    game_plays = {
        "1": {
            "top": [
                _play(id=1, playorder=0, pa=1, balls=0, strikes=0, ab=1, h=1, hitpull=-27, hitdistance=32, hittype=1),
            ],
        },
    }
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert len(rows) == 1
    row = rows[0]
    assert row["game_id"] == GAME_ID
    assert row["source_play_id"] == 1
    assert row["batter_player_season_id"] == 100
    assert row["pitcher_player_season_id"] == 900
    assert row["first_pitch_strike"] is True
    assert row["hitpull"] == -27
    assert row["hitdistance"] == 32
    assert row["hittype"] == 1


def test_first_pitch_hbp_is_not_a_strike_and_has_no_batted_ball_fields():
    game_plays = {
        "1": {"top": [_play(id=2, playorder=0, pa=1, balls=0, strikes=0, ab=0, hbp=1)]},
    }
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert len(rows) == 1
    row = rows[0]
    assert row["first_pitch_strike"] is False
    assert row["hitpull"] is None
    assert row["hitdistance"] is None
    assert row["hittype"] is None


def test_first_pitch_outcome_can_differ_from_the_pa_final_outcome():
    # Pitch 1: taken for a ball (first pitch, not a strike). Pitch 2: PA
    # ends in a single — first-pitch-strike must reflect pitch 1, not the
    # eventual hit.
    game_plays = {
        "1": {
            "top": [
                _play(id=3, playorder=0, pa=0, balls=0, strikes=0),
                _play(id=4, playorder=1, pa=1, balls=1, strikes=0, ab=1, h=1),
            ],
        },
    }
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert len(rows) == 1
    row = rows[0]
    assert row["source_play_id"] == 4
    assert row["h"] == 1
    assert row["first_pitch_strike"] is False


def test_first_pitch_strike_detected_via_count_diff_when_pa_continues():
    # Pitch 1: called strike (first pitch, count 0-0 -> 0-1). Pitch 2: PA
    # ends in a strikeout.
    game_plays = {
        "1": {
            "top": [
                _play(id=5, playorder=0, pa=0, balls=0, strikes=0),
                _play(id=6, playorder=1, pa=1, balls=0, strikes=1, strikeout=1),
            ],
        },
    }
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert len(rows) == 1
    assert rows[0]["first_pitch_strike"] is True
    assert rows[0]["so"] == 1


def test_nopitch_placeholder_excluded_from_pa_boundaries():
    # A "Play Ball" placeholder shouldn't be treated as part of the first
    # real batter's plate appearance, or produce a spurious PA of its own.
    game_plays = {
        "1": {
            "top": [
                _play(id=7, playorder=0, pa=0, nopitch=2, batterid=1),
                _play(id=8, playorder=1, pa=1, balls=0, strikes=0, ab=1, h=1),
            ],
        },
    }
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert len(rows) == 1
    assert rows[0]["source_play_id"] == 8


def test_extract_plate_appearances_drops_rows_with_unresolved_batter():
    game_plays = {"1": {"top": [_play(id=9, pa=1, batterid=999, balls=0, strikes=0, ab=1, h=1)]}}
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert rows == []


def test_extract_plate_appearances_keeps_row_with_unresolved_pitcher():
    game_plays = {"1": {"top": [_play(id=10, pa=1, pitcherid=999, balls=0, strikes=0, ab=1, h=1)]}}
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert len(rows) == 1
    assert rows[0]["pitcher_player_season_id"] is None


def test_extract_plate_appearances_includes_every_required_column():
    """Regression guard: PlateAppearance has several NOT NULL, no-default
    columns (source_play_id, game_id, inning, half, batter_player_season_id)
    — a row missing any of them passes this pure-function test fine but
    blows up as a NOT NULL constraint violation the moment `upsert()` tries
    to insert it for real, aborting the rest of scrape_boxscore() before its
    final commit (silently losing that game's data for the run)."""
    game_plays = {"1": {"top": [_play(id=11, pa=1, balls=0, strikes=0, ab=1, h=1)]}}
    rows = _extract_plate_appearances(game_plays, PS_ID_BY_SOURCE, GAME_ID)
    assert len(rows) == 1
    row = rows[0]
    for required in ("source_play_id", "game_id", "inning", "half", "batter_player_season_id"):
        assert row[required] is not None, f"{required} must not be None"
    assert row["game_id"] == GAME_ID
    assert row["inning"] == 1
    assert row["half"] == "top"


def test_first_pitch_strike_returns_none_when_no_zero_zero_record_found():
    # Defensive case: no record at a 0-0 count (shouldn't happen in real
    # data, but the buffer shouldn't crash or guess).
    assert _first_pitch_strike([_play(balls=1, strikes=1, pa=1)]) is None
