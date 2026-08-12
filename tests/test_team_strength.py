"""Tests for Bradley-Terry team ratings and strength of schedule."""

import math

import pytest

from db.models import (
    Division,
    Game,
    League,
    LeagueSeason,
    Season,
    Team,
    TeamSeason,
    TeamStrength,
)
from stats.team_strength import (
    DEFAULT_RIDGE_LAMBDA,
    GameResult,
    compute_team_strength,
    fit_team_strength,
)


def _round_robin(teams, wins_by, repeats=1):
    """Every team plays every other `repeats` times; `wins_by` decides the
    winner as a function of (home, away) -> home_won."""
    games = []
    for _ in range(repeats):
        for i, home in enumerate(teams):
            for away in teams[i + 1 :]:
                games.append(GameResult(home, away, wins_by(home, away)))
                games.append(GameResult(away, home, not wins_by(home, away)))
    return games


def test_stronger_team_gets_a_higher_rating():
    # Team 1 beats everyone; team 4 loses to everyone.
    games = _round_robin([1, 2, 3, 4], lambda h, a: h < a, repeats=3)

    fit = fit_team_strength(games)
    ratings = {t: r.rating for t, r in fit.ratings.items()}

    assert ratings[1] > ratings[2] > ratings[3] > ratings[4]


def test_ratings_are_centred_within_each_division():
    """0 must mean "division average" exactly, since that is what the app's
    captions claim — not approximately, as the penalty alone would give."""
    games = _round_robin([1, 2, 3], lambda h, a: h < a, repeats=2)
    games += _round_robin([4, 5, 6], lambda h, a: h < a, repeats=2)
    groups = {1: 10, 2: 10, 3: 10, 4: 20, 5: 20, 6: 20}

    fit = fit_team_strength(games, groups)

    for division in (10, 20):
        members = [r.rating for t, r in fit.ratings.items() if groups[t] == division]
        assert math.isclose(sum(members), 0.0, abs_tol=1e-9)


def test_undefeated_team_gets_a_finite_rating():
    """Milton Keynes went 18-0. Unpenalised Bradley-Terry has no finite
    estimate for that; the L2 penalty is what makes it usable."""
    games = [GameResult(1, opponent, True) for opponent in (2, 3, 4)] * 6
    games += _round_robin([2, 3, 4], lambda h, a: h < a, repeats=2)

    fit = fit_team_strength(games)

    rating = fit.ratings[1].rating
    assert math.isfinite(rating)
    assert rating > 0
    # And it should still be the best team.
    assert rating == max(r.rating for r in fit.ratings.values())


def test_strength_of_schedule_reflects_who_was_actually_played():
    """The Milton Keynes case: an easy schedule inside one's own division."""
    # 1 and 2 are strong, 3 and 4 are weak (established by their head-to-heads).
    games = _round_robin([1, 2, 3, 4], lambda h, a: h < a, repeats=2)
    # Now team 1 plays the weak teams four extra times; team 2 plays the
    # strong ones four extra times.
    games += [GameResult(1, 4, True)] * 4
    games += [GameResult(2, 1, False)] * 4

    fit = fit_team_strength(games)

    assert fit.ratings[1].sos < fit.ratings[2].sos


def test_balanced_schedule_gives_everyone_the_same_sos():
    games = _round_robin([1, 2, 3, 4], lambda h, a: h < a, repeats=2)

    fit = fit_team_strength(games)
    sos_values = [r.sos for r in fit.ratings.values()]

    assert max(sos_values) - min(sos_values) < 1e-9


def test_ties_count_as_half_a_win_each():
    games = [GameResult(1, 2, None)] * 10

    fit = fit_team_strength(games)

    assert fit.ratings[1].ties == 10
    assert fit.ratings[1].wins == 0
    # Two teams that only ever drew are equally rated.
    assert math.isclose(fit.ratings[1].rating, fit.ratings[2].rating, abs_tol=1e-9)


def test_home_advantage_is_detected_and_not_shrunk_away():
    """Home teams win 53.5% across the corpus; the intercept is deliberately
    left out of the penalty so that real effect isn't pulled toward zero."""
    # Identical teams, but whoever is at home wins. Built directly rather
    # than through _round_robin, whose reversed fixture flips the result.
    games = []
    teams = [1, 2, 3, 4]
    for _ in range(5):
        for i, home in enumerate(teams):
            for away in teams[i + 1 :]:
                games.append(GameResult(home, away, True))
                games.append(GameResult(away, home, True))

    fit = fit_team_strength(games)

    assert fit.home_advantage > 1.0
    # With a pure home effect and a balanced schedule, no team is better.
    ratings = [r.rating for r in fit.ratings.values()]
    assert max(ratings) - min(ratings) < 1e-6


def test_expected_win_pct_is_the_rating_on_a_readable_scale():
    games = _round_robin([1, 2, 3, 4], lambda h, a: h < a, repeats=3)

    fit = fit_team_strength(games)

    for rating in fit.ratings.values():
        assert math.isclose(
            rating.expected_win_pct, 1 / (1 + math.exp(-rating.rating)), rel_tol=1e-9
        )
    assert fit.ratings[1].expected_win_pct > 0.5
    assert fit.ratings[4].expected_win_pct < 0.5


def test_records_are_counted_correctly():
    games = [GameResult(1, 2, True), GameResult(1, 2, False), GameResult(2, 1, None)]

    fit = fit_team_strength(games)

    assert (fit.ratings[1].wins, fit.ratings[1].losses, fit.ratings[1].ties) == (1, 1, 1)
    assert (fit.ratings[2].wins, fit.ratings[2].losses, fit.ratings[2].ties) == (1, 1, 1)
    assert fit.ratings[1].games == 3


def test_small_sample_falls_back_to_a_fixed_penalty():
    games = _round_robin([1, 2, 3], lambda h, a: h < a, repeats=1)

    fit = fit_team_strength(games)

    assert fit.lambda_self_calibrated is False
    assert fit.ridge_lambda == DEFAULT_RIDGE_LAMBDA


def test_large_sample_self_calibrates_the_penalty():
    games = _round_robin([1, 2, 3, 4, 5, 6], lambda h, a: h < a, repeats=4)

    fit = fit_team_strength(games)

    assert fit.lambda_self_calibrated is True


def test_fit_is_deterministic():
    """The cross-validation folds are seeded, so repeated runs of the
    pipeline must not produce drifting ratings."""
    games = _round_robin([1, 2, 3, 4, 5], lambda h, a: h < a, repeats=4)

    first = fit_team_strength(games)
    second = fit_team_strength(games)

    assert first.ridge_lambda == second.ridge_lambda
    for team in first.ratings:
        assert first.ratings[team].rating == second.ratings[team].rating


def test_no_games_yields_no_ratings():
    fit = fit_team_strength([])

    assert fit.ratings == {}


def test_more_games_gives_a_tighter_standard_error():
    """Compared at a fixed penalty: the penalty itself is chosen per fit, and
    a stronger one narrows the interval on its own, which would confound a
    comparison between two whole fits."""
    import numpy as np

    from stats.team_strength import _design, _fit_penalised

    def se_for(repeats):
        games = _round_robin([1, 2, 3, 4], lambda h, a: h < a, repeats=repeats)
        teams = sorted({t for g in games for t in (g.home, g.away)})
        X, y, w = _design(games, teams)
        _, _, covariance = _fit_penalised(X, y, w, ridge_lambda=4.0)
        return np.sqrt(covariance[0, 0])

    assert se_for(8) < se_for(1)


def test_undefeated_team_has_a_wide_interval_despite_a_high_rating():
    """An unbeaten record puts a floor under a team's strength but no
    ceiling, so the rating is high *and* poorly determined. Reporting the
    rating without that uncertainty would overstate what 18-0 establishes."""
    unbeaten = [GameResult(1, opponent, True) for opponent in (2, 3, 4)] * 6
    unbeaten += _round_robin([2, 3, 4], lambda h, a: h < a, repeats=4)

    fit = fit_team_strength(unbeaten)

    top = fit.ratings[1]
    others = [r.rating_se for t, r in fit.ratings.items() if t != 1]
    assert top.rating > 0
    assert top.rating_se > max(others)


# --------------------------------------------------------------------------
# DB layer
# --------------------------------------------------------------------------


@pytest.fixture
def league_season(session):
    league = League(code="d3", name="Division 3", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    ls = LeagueSeason(
        league_id=league.id, season_id=season.id, source_tournament_id=1, competition_slug="2026-d3"
    )
    session.add(ls)
    session.flush()
    return ls


def _team(session, ls, name, source_team_id, division=None):
    team = Team(name=name)
    session.add(team)
    session.flush()
    ts = TeamSeason(
        team_id=team.id,
        league_season_id=ls.id,
        source_team_id=source_team_id,
        display_name=name,
        division_id=division.id if division else None,
    )
    session.add(ts)
    session.flush()
    return ts


def _game(session, ls, home, away, home_score, away_score, source_id, *, phase="regular", division=None):
    game = Game(
        source_id=source_id,
        league_season_id=ls.id,
        home_team_season_id=home.id,
        away_team_season_id=away.id,
        home_score=home_score,
        away_score=away_score,
        status="final",
        phase=phase,
        division_id=division.id if division else None,
    )
    session.add(game)
    session.flush()
    return game


def test_compute_team_strength_writes_rows(session, league_season):
    division = Division(league_season_id=league_season.id, name="North", sort_order=0)
    session.add(division)
    session.flush()
    strong = _team(session, league_season, "Strong", 1, division)
    weak = _team(session, league_season, "Weak", 2, division)
    for i in range(6):
        _game(session, league_season, strong, weak, 10, 1, source_id=i, division=division)

    assert compute_team_strength(session, league_season.id) == 2

    rows = {r.team_season_id: r for r in session.query(TeamStrength).all()}
    assert rows[strong.id].rating > rows[weak.id].rating
    assert rows[strong.id].wins == 6
    assert rows[weak.id].losses == 6
    assert rows[strong.id].ridge_lambda is not None


def test_compute_team_strength_ignores_playoffs_and_cross_division(session, league_season):
    north = Division(league_season_id=league_season.id, name="North", sort_order=0)
    south = Division(league_season_id=league_season.id, name="South", sort_order=1)
    session.add_all([north, south])
    session.flush()
    n1 = _team(session, league_season, "N1", 1, north)
    n2 = _team(session, league_season, "N2", 2, north)
    s1 = _team(session, league_season, "S1", 3, south)

    _game(session, league_season, n1, n2, 5, 1, source_id=1, division=north)
    _game(session, league_season, n1, n2, 9, 0, source_id=2, phase="playoff", division=north)
    _game(session, league_season, n1, s1, 9, 0, source_id=3)  # cross-division: no division_id

    compute_team_strength(session, league_season.id)

    row = session.query(TeamStrength).filter_by(team_season_id=n1.id).one()
    assert row.games == 1
    assert row.wins == 1


def test_compute_team_strength_is_idempotent(session, league_season):
    division = Division(league_season_id=league_season.id, name="North", sort_order=0)
    session.add(division)
    session.flush()
    a = _team(session, league_season, "A", 1, division)
    b = _team(session, league_season, "B", 2, division)
    for i in range(4):
        _game(session, league_season, a, b, 3, 2, source_id=i, division=division)

    compute_team_strength(session, league_season.id)
    first = session.query(TeamStrength).filter_by(team_season_id=a.id).one().rating
    compute_team_strength(session, league_season.id)

    assert session.query(TeamStrength).count() == 2
    assert session.query(TeamStrength).filter_by(team_season_id=a.id).one().rating == first


def test_league_season_with_no_division_games_writes_nothing(session, league_season):
    a = _team(session, league_season, "A", 1)
    b = _team(session, league_season, "B", 2)
    _game(session, league_season, a, b, 3, 2, source_id=1)  # no division

    assert compute_team_strength(session, league_season.id) == 0
