"""Tests for regional divisions: parsing them off the standings page,
classifying regular season vs playoff, attaching teams and games, and the
per-division run-environment context.
"""

from db.models import (
    BattingGameLine,
    Division,
    DivisionContext,
    Game,
    League,
    LeagueSeason,
    LeagueSeasonContext,
    PitchingGameLine,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from scraper.scrape_schedule import _game_phase, _modal_round_id
from scraper.scrape_standings import (
    DivisionBlock,
    apply_divisions,
    parse_standings_divisions,
)
from stats.league_context import compute_division_contexts, compute_league_context


# --------------------------------------------------------------------------
# Standings page parsing
# --------------------------------------------------------------------------


def _standings_html(*blocks: tuple[str, list[int]], nav_team_id: int = 999) -> str:
    """Build markup shaped like the real standings page, including a team
    link in the nav that must *not* be picked up as division membership."""
    body = [
        '<div class="navbar"><a href="https://x/en/events/2026-d3/teams/'
        f'{nav_team_id}">Some Team</a></div>'
    ]
    for name, team_ids in blocks:
        rows = "".join(
            f'<tr><td><a href="https://x/en/events/2026-d3/teams/{tid}">T{tid}</a></td></tr>'
            for tid in team_ids
        )
        body.append(
            f'<div class="box-container"><h3>{name} </h3>'
            f'<table class="table table-hover standings-print">{rows}</table></div>'
        )
    return f"<html><body>{''.join(body)}</body></html>"


def test_parses_division_names_and_team_ids():
    html = _standings_html(("North", [41881, 41882]), ("South", [41870, 41871, 41872]))

    blocks = parse_standings_divisions(html)

    assert [b.name for b in blocks] == ["North", "South"]
    assert blocks[0].source_team_ids == [41881, 41882]
    assert blocks[1].source_team_ids == [41870, 41871, 41872]
    assert [b.sort_order for b in blocks] == [0, 1]


def test_decodes_html_entities_in_division_names():
    """The site publishes "AAA - South West &amp; Wales" and "South &amp;
    South West"; storing the raw entity would make the name unmatchable."""
    html = _standings_html(("AAA - South West &amp; Wales", [1, 2]))

    assert parse_standings_divisions(html)[0].name == "AAA - South West & Wales"


def test_ignores_team_links_outside_standings_tables():
    """The page carries team links in its nav and breadcrumbs. Counting those
    would silently add a team to whichever division happened to precede."""
    html = _standings_html(("North", [41881]), nav_team_id=12345)

    blocks = parse_standings_divisions(html)

    assert len(blocks) == 1
    assert blocks[0].source_team_ids == [41881]


def test_drops_empty_box_containers_and_renumbers():
    html = (
        '<div class="box-container"><h3>Empty</h3><table></table></div>'
        + _standings_html(("Real", [7]))
    )

    blocks = parse_standings_divisions(html)

    assert [(b.name, b.sort_order) for b in blocks] == [("Real", 0)]


def test_unnamed_block_still_gets_an_addressable_name():
    """(league_season_id, name) is the unique key, so two nameless blocks
    must not collide on the empty string."""
    html = (
        '<div class="box-container"><table>'
        '<tr><td><a href="/teams/1">A</a></td></tr></table></div>'
        '<div class="box-container"><table>'
        '<tr><td><a href="/teams/2">B</a></td></tr></table></div>'
    )

    blocks = parse_standings_divisions(html)

    assert [b.name for b in blocks] == ["Division 1", "Division 2"]


def test_no_standings_content_yields_no_divisions():
    assert parse_standings_divisions("<html><body>nothing here</body></html>") == []


# --------------------------------------------------------------------------
# Regular season vs playoff
# --------------------------------------------------------------------------


def test_modal_round_id_is_the_regular_season_round():
    games = [{"wbsc_tournament_round_id": 100} for _ in range(20)]
    games += [{"wbsc_tournament_round_id": 200} for _ in range(3)]

    assert _modal_round_id(games) == 100


def test_playoff_labels_are_detected_regardless_of_round():
    for label in [
        "Final",
        "Semifinal",
        "Semi Final 1",
        "Quarter Final 3",
        "Qualifier 1",
        "NBL Championship Series G1",
        "NBL Final - Game 1",
        "AA SF2",
        "Wildcard",
        "Wild Card",
        "3rd place",
        "SWWBL Final",
    ]:
        assert _game_phase(label, 100, 100) == "playoff", label


def test_regular_season_labels_win_over_an_odd_round_id():
    """2025 files two "Week 19" games on a playoff round. A round-id-only
    rule would misclassify them as playoffs."""
    assert _game_phase("Week 19", 5814, 4873) == "regular"
    assert _game_phase("Regular Season", 1044, 881) == "regular"
    assert _game_phase("12", 999, 100) == "regular"
    assert _game_phase("Week 1(r)", 999, 100) == "regular"


def test_unlabelled_game_on_an_off_round_is_a_playoff():
    """2023's NBL files playoff games under the bare label "A"; only the
    round id distinguishes them."""
    assert _game_phase("A", 2931, 2194) == "playoff"


def test_unlabelled_game_on_the_main_round_is_regular_season():
    assert _game_phase("A", 2194, 2194) == "regular"
    assert _game_phase(None, 100, 100) == "regular"


# --------------------------------------------------------------------------
# Attaching divisions to teams and games
# --------------------------------------------------------------------------


def _league_season(session, slug="2026-test"):
    league = League(code="test", name="Test League", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    league_season = LeagueSeason(
        league_id=league.id,
        season_id=season.id,
        source_tournament_id=1,
        competition_slug=slug,
    )
    session.add(league_season)
    session.flush()
    return league_season


def _team(session, league_season, source_team_id, name):
    team = Team(name=name)
    session.add(team)
    session.flush()
    team_season = TeamSeason(
        team_id=team.id,
        league_season_id=league_season.id,
        source_team_id=source_team_id,
        display_name=name,
    )
    session.add(team_season)
    session.flush()
    return team_season


def _game(
    session,
    league_season,
    home,
    away,
    *,
    source_id,
    home_score=5,
    away_score=3,
    phase="regular",
    source_group_id=None,
):
    game = Game(
        source_id=source_id,
        league_season_id=league_season.id,
        home_team_season_id=home.id,
        away_team_season_id=away.id,
        home_score=home_score,
        away_score=away_score,
        status="final",
        phase=phase,
        source_group_id=source_group_id,
    )
    session.add(game)
    session.flush()
    return game


def test_apply_divisions_attaches_teams_and_intra_division_games(session):
    ls = _league_season(session)
    north_a = _team(session, ls, 1, "North A")
    north_b = _team(session, ls, 2, "North B")
    south_a = _team(session, ls, 3, "South A")
    south_b = _team(session, ls, 4, "South B")

    intra_north = _game(session, ls, north_a, north_b, source_id=1)
    intra_south = _game(session, ls, south_a, south_b, source_id=2)
    cross = _game(session, ls, north_a, south_a, source_id=3, phase="playoff")

    written = apply_divisions(
        session,
        ls.id,
        [DivisionBlock("North", [1, 2], 0), DivisionBlock("South", [3, 4], 1)],
    )

    assert written == 2
    north = session.query(Division).filter_by(name="North").one()
    south = session.query(Division).filter_by(name="South").one()

    assert north_a.division_id == north.id
    assert south_b.division_id == south.id
    assert intra_north.division_id == north.id
    assert intra_south.division_id == south.id
    # A game between two divisions belongs to neither.
    assert cross.division_id is None


def test_team_absent_from_standings_keeps_no_division(session):
    ls = _league_season(session)
    listed = _team(session, ls, 1, "Listed")
    unlisted = _team(session, ls, 2, "Unlisted")
    game = _game(session, ls, listed, unlisted, source_id=1)

    apply_divisions(session, ls.id, [DivisionBlock("North", [1], 0)])

    assert unlisted.division_id is None
    # One side has no division, so the game can't be attributed either.
    assert game.division_id is None


def test_rerunning_converges_when_a_team_changes_division(session):
    """The site edits its groupings mid-season. A second run must move the
    team rather than leaving it in both."""
    ls = _league_season(session)
    a = _team(session, ls, 1, "A")
    b = _team(session, ls, 2, "B")
    game = _game(session, ls, a, b, source_id=1)

    apply_divisions(
        session, ls.id, [DivisionBlock("North", [1, 2], 0), DivisionBlock("South", [], 1)]
    )
    north = session.query(Division).filter_by(name="North").one()
    assert game.division_id == north.id

    # B moves to South; the game is now cross-division.
    apply_divisions(
        session, ls.id, [DivisionBlock("North", [1], 0), DivisionBlock("South", [2], 1)]
    )

    south = session.query(Division).filter_by(name="South").one()
    assert a.division_id == north.id
    assert b.division_id == south.id
    assert game.division_id is None
    assert session.query(Division).filter_by(league_season_id=ls.id).count() == 2


def test_divisions_dropped_from_the_page_are_deleted(session):
    ls = _league_season(session)
    _team(session, ls, 1, "A")

    apply_divisions(
        session, ls.id, [DivisionBlock("North", [1], 0), DivisionBlock("Defunct", [], 1)]
    )
    assert session.query(Division).filter_by(league_season_id=ls.id).count() == 2

    apply_divisions(session, ls.id, [DivisionBlock("North", [1], 0)])

    names = [d.name for d in session.query(Division).filter_by(league_season_id=ls.id)]
    assert names == ["North"]


def test_division_records_the_group_id_its_games_carry(session):
    ls = _league_season(session)
    a = _team(session, ls, 1, "A")
    b = _team(session, ls, 2, "B")
    _game(session, ls, a, b, source_id=1, source_group_id=13068)
    _game(session, ls, a, b, source_id=2, source_group_id=13068)
    # A single mistagged game must not win.
    _game(session, ls, a, b, source_id=3, source_group_id=99999)

    apply_divisions(session, ls.id, [DivisionBlock("North", [1, 2], 0)])

    assert session.query(Division).one().source_group_id == 13068


def test_empty_block_list_leaves_everything_alone(session):
    """A standings page that couldn't be read must not wipe existing
    divisions — the pipeline treats that failure as non-fatal."""
    ls = _league_season(session)
    _team(session, ls, 1, "A")
    apply_divisions(session, ls.id, [DivisionBlock("North", [1], 0)])

    assert apply_divisions(session, ls.id, []) == 0
    assert session.query(Division).count() == 1


# --------------------------------------------------------------------------
# Division context
# --------------------------------------------------------------------------


def _batting_line(session, game, team_season, player_season, **stats):
    defaults = dict(pa=4, ab=4, h=1, doubles=0, triples=0, hr=0, bb=0, ibb=0, hbp=0, sf=0, r=1)
    defaults.update(stats)
    line = BattingGameLine(
        game_id=game.id,
        player_season_id=player_season.id,
        team_season_id=team_season.id,
        **defaults,
    )
    session.add(line)
    return line


def _pitching_line(session, game, team_season, player_season, **stats):
    defaults = dict(outs_recorded=21, h=5, r=3, er=3, bb=2, so=5, hr=1, hbp=0)
    defaults.update(stats)
    line = PitchingGameLine(
        game_id=game.id,
        player_season_id=player_season.id,
        team_season_id=team_season.id,
        **defaults,
    )
    session.add(line)
    return line


def _player_season(session, team_season, name):
    player = Player(full_name=name, identity_key=name)
    session.add(player)
    session.flush()
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(player_season)
    session.flush()
    return player_season


def test_division_contexts_differ_by_run_environment(session):
    """The whole point of the division scope: two divisions in one league
    with different offensive environments must not share a mean."""
    ls = _league_season(session)
    n1, n2 = _team(session, ls, 1, "N1"), _team(session, ls, 2, "N2")
    s1, s2 = _team(session, ls, 3, "S1"), _team(session, ls, 4, "S2")
    hitters = _player_season(session, n1, "Slugger")
    weak = _player_season(session, s1, "Punchless")

    high = _game(session, ls, n1, n2, source_id=1, home_score=15, away_score=12)
    low = _game(session, ls, s1, s2, source_id=2, home_score=2, away_score=1)
    _batting_line(session, high, n1, hitters, pa=100, ab=100, h=50, hr=20)
    _batting_line(session, low, s1, weak, pa=100, ab=100, h=10, hr=0)
    session.flush()

    apply_divisions(
        session, ls.id, [DivisionBlock("North", [1, 2], 0), DivisionBlock("South", [3, 4], 1)]
    )
    assert compute_division_contexts(session, ls.id) == 2

    contexts = {
        session.get(Division, c.division_id).name: c
        for c in session.query(DivisionContext).all()
    }
    assert contexts["North"].lg_woba > contexts["South"].lg_woba
    assert contexts["North"].games == 1
    assert contexts["North"].pa == 100


def test_single_division_context_matches_the_league_wide_one(session):
    """When the two scopes cover the same games they must agree exactly —
    that is what proves a division context is the same calibration applied
    to a narrower slice, not a different formula."""
    ls = _league_season(session)
    a, b = _team(session, ls, 1, "A"), _team(session, ls, 2, "B")
    batter = _player_season(session, a, "Batter")
    pitcher = _player_season(session, b, "Pitcher")

    game = _game(session, ls, a, b, source_id=1, home_score=7, away_score=4)
    _batting_line(session, game, a, batter, pa=40, ab=36, h=12, doubles=3, hr=2, bb=4, r=7)
    _pitching_line(session, game, b, pitcher)
    session.flush()

    apply_divisions(session, ls.id, [DivisionBlock("Only", [1, 2], 0)])

    # The league scope reads season-stats tables, so aggregate first.
    from stats.aggregation import aggregate_batting, aggregate_pitching

    aggregate_batting(session, ls.id)
    aggregate_pitching(session, ls.id)
    compute_league_context(session, ls.id)
    compute_division_contexts(session, ls.id)

    league = session.query(LeagueSeasonContext).one()
    division = session.query(DivisionContext).one()

    for field in ("lg_obp", "lg_slg", "lg_woba", "lg_era", "lg_fip", "runs_per_win"):
        assert getattr(division, field) == getattr(league, field), field


def test_division_context_excludes_playoff_and_cross_division_games(session):
    """Playoffs involve only the best teams, and are included or excluded by
    bracket happenstance — counting them would make a division's environment
    depend on who happened to qualify."""
    ls = _league_season(session)
    n1, n2 = _team(session, ls, 1, "N1"), _team(session, ls, 2, "N2")
    s1 = _team(session, ls, 3, "S1")
    batter = _player_season(session, n1, "Batter")

    regular = _game(session, ls, n1, n2, source_id=1, home_score=3, away_score=2)
    playoff = _game(session, ls, n1, n2, source_id=2, home_score=30, away_score=0, phase="playoff")
    cross = _game(session, ls, n1, s1, source_id=3, home_score=30, away_score=0)
    _batting_line(session, regular, n1, batter, pa=10, ab=10, h=2)
    _batting_line(session, playoff, n1, batter, pa=10, ab=10, h=10, hr=10)
    _batting_line(session, cross, n1, batter, pa=10, ab=10, h=10, hr=10)
    session.flush()

    apply_divisions(
        session, ls.id, [DivisionBlock("North", [1, 2], 0), DivisionBlock("South", [3], 1)]
    )
    compute_division_contexts(session, ls.id)

    north = session.query(Division).filter_by(name="North").one()
    context = session.query(DivisionContext).filter_by(division_id=north.id).one()

    # Only the regular intra-division game counts: 10 PA, 2 hits.
    assert context.games == 1
    assert context.pa == 10


def test_league_season_without_divisions_writes_no_contexts(session):
    ls = _league_season(session)
    _team(session, ls, 1, "A")

    assert compute_division_contexts(session, ls.id) == 0
    assert session.query(DivisionContext).count() == 0
