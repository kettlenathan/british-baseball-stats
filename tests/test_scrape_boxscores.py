from scraper.scrape_boxscores import _extract_lob, _extract_risp_totals


def _play(**overrides):
    base = {
        "ab": 0, "h": 0, "batterid": 1,
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
