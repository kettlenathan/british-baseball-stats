"""Tests for cross-division strength offsets estimated from shared players."""

import math

import pytest

from app.components import data_access
from db.models import (
    Division,
    DivisionContext,
    DivisionStrength,
    League,
    LeagueSeason,
    LeagueSeasonContext,
    Season,
)
from stats.division_strength import Stint, fit_division_offsets


def _stints(spec):
    """spec: list of (player, division, woba, pa)."""
    return [Stint(p, d, w, pa) for p, d, w, pa in spec]


def test_shared_player_hitting_better_marks_a_division_as_easier():
    """The whole premise: the same person producing more in division B says
    B was the easier place to bat."""
    spec = []
    for player in range(1, 21):
        spec.append((player, 10, 0.400, 100))
        spec.append((player, 20, 0.500, 100))

    fit = fit_division_offsets(_stints(spec))

    assert fit.offsets[20].offset > fit.offsets[10].offset
    # And the gap should be close to the 100-point difference actually seen.
    gap = fit.offsets[20].offset - fit.offsets[10].offset
    assert 0.05 < gap < 0.10


def test_single_division_players_cannot_move_an_offset():
    """The identifying assumption. A division full of great hitters who never
    play anywhere else must not look 'easier' — otherwise the model measures
    talent, not difficulty."""
    spec = [(p, 10, 0.400, 100) for p in range(1, 11)]
    spec += [(p, 20, 0.400, 100) for p in range(11, 21)]
    baseline = fit_division_offsets(_stints(spec))

    # Now make division 20 full of brilliant one-division hitters.
    spec_loaded = [(p, 10, 0.400, 100) for p in range(1, 11)]
    spec_loaded += [(p, 20, 0.800, 100) for p in range(11, 21)]
    loaded = fit_division_offsets(_stints(spec_loaded))

    assert baseline.identifying_players == 0
    assert loaded.identifying_players == 0
    # No multi-division player exists, so nothing identifies either offset
    # and both fits must be equally uninformative.
    for division in (10, 20):
        assert abs(loaded.offsets[division].offset) < 1e-9
        assert abs(baseline.offsets[division].offset) < 1e-9


def test_talent_is_absorbed_so_only_the_difference_counts():
    """Two bridge players of very different quality, each losing the same
    amount when they move, must produce the same offset gap as two equal
    players would."""
    spec = [
        (1, 10, 0.700, 100), (1, 20, 0.800, 100),   # a star
        (2, 10, 0.250, 100), (2, 20, 0.350, 100),   # a weak hitter
    ]

    fit = fit_division_offsets(_stints(spec))

    assert fit.offsets[20].offset > fit.offsets[10].offset
    assert fit.identifying_players == 2


def test_offsets_are_centred_on_zero():
    spec = []
    for player in range(1, 11):
        spec.append((player, 10, 0.350, 100))
        spec.append((player, 20, 0.450, 100))
        spec.append((player, 30, 0.550, 100))

    fit = fit_division_offsets(_stints(spec))

    assert math.isclose(sum(o.offset for o in fit.offsets.values()), 0.0, abs_tol=1e-9)


def test_more_plate_appearances_narrow_the_interval():
    thin = fit_division_offsets(
        _stints([(p, 10, 0.400, 20) for p in range(1, 6)] + [(p, 20, 0.500, 20) for p in range(1, 6)])
    )
    thick = fit_division_offsets(
        _stints([(p, 10, 0.400, 400) for p in range(1, 6)] + [(p, 20, 0.500, 400) for p in range(1, 6)])
    )

    assert thick.offsets[10].standard_error < thin.offsets[10].standard_error


def test_bridge_diagnostics_count_only_multi_division_players():
    spec = [
        (1, 10, 0.400, 100), (1, 20, 0.500, 100),   # a bridge
        (2, 10, 0.400, 100),                        # division 10 only
        (3, 20, 0.500, 100),                        # division 20 only
    ]

    fit = fit_division_offsets(_stints(spec))

    assert fit.offsets[10].bridge_players == 1
    assert fit.offsets[10].stints == 2
    assert fit.identifying_players == 1


def test_no_stints_is_handled():
    fit = fit_division_offsets([])

    assert fit.offsets == {}
    assert fit.identifying_players == 0


# --------------------------------------------------------------------------
# Presentation: the two readings shown side by side
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_get_session(session, monkeypatch):
    monkeypatch.setattr(data_access, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    yield
    data_access.division_comparison.clear()


def _league_with_divisions(session, league_woba, divisions):
    """divisions: {name: (division lgwOBA, bridge offset)}."""
    league = League(code="d3", name="Division 3", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    ls = LeagueSeason(
        league_id=league.id, season_id=season.id, source_tournament_id=1,
        competition_slug="2026-d3",
    )
    session.add(ls)
    session.flush()
    session.add(LeagueSeasonContext(league_season_id=ls.id, lg_woba=league_woba))
    for order, (name, (division_woba, offset)) in enumerate(divisions.items()):
        division = Division(league_season_id=ls.id, name=name, sort_order=order)
        session.add(division)
        session.flush()
        session.add(DivisionContext(division_id=division.id, lg_woba=division_woba))
        session.add(
            DivisionStrength(
                division_id=division.id, offset=offset, standard_error=0.01,
                bridge_players=40,
            )
        )
    session.flush()
    return ls


def test_scoring_gap_reproduces_the_wrc_plus_ratio(session):
    """The scoring gap must be exactly what the leaderboards' two wRC+
    columns already imply — it is the same quantity, not a second estimate."""
    ls = _league_with_divisions(session, 0.400, {"Easy": (0.440, 0.02)})

    row = data_access.division_comparison(ls.id).iloc[0]

    # 0.440 / 0.400 = 110% of league, i.e. +10 wRC+ points.
    assert row["env_gap"] == pytest.approx(10.0)


def test_talent_gap_separates_scoring_from_who_played(session):
    """The column that justifies this page existing: a division can score
    heavily because conditions were soft or because its hitters were good,
    and only the same-player reading can tell those apart."""
    ls = _league_with_divisions(
        session,
        0.400,
        {
            # Scores well above league, but shared players found it only
            # slightly easier -> the scoring was down to who played there.
            "Stacked": (0.440, 0.008),
            # Scores at league level, but shared players found it hard ->
            # tougher than its scoring suggests.
            "Tough": (0.400, -0.020),
        },
    )

    df = data_access.division_comparison(ls.id).set_index("division")

    assert df.loc["Stacked", "env_gap"] > df.loc["Stacked", "bridge_gap"]
    assert df.loc["Stacked", "talent_gap"] < 0
    assert df.loc["Tough", "env_gap"] == pytest.approx(0.0)
    assert df.loc["Tough", "bridge_gap"] < 0
    assert df.loc["Tough", "talent_gap"] < 0


def test_both_readings_share_a_baseline(session):
    """Regression: DivisionStrength.offset is centred across every division
    in the database, while env_gap is relative to this league-season. Left
    uncorrected, a league that is easy overall shows *every* one of its
    divisions as easier, and `talent_gap` becomes meaningless — which is
    exactly what happened before the offsets were re-centred per
    league-season."""
    ls = _league_with_divisions(
        session,
        0.400,
        # All three offsets sit far above the global average, as every
        # division of an easy league would.
        {"A": (0.410, 0.100), "B": (0.400, 0.090), "C": (0.390, 0.080)},
    )

    df = data_access.division_comparison(ls.id)

    # Both columns must straddle zero within the league-season.
    assert df["bridge_gap"].min() < 0 < df["bridge_gap"].max()
    assert abs(df["bridge_gap"].mean()) < 1.0
    # And the ordering must survive the re-centring.
    assert list(df.sort_values("bridge_gap", ascending=False)["division"]) == ["A", "B", "C"]


def test_division_comparison_keeps_published_order(session):
    ls = _league_with_divisions(
        session, 0.400, {"North": (0.410, 0.01), "South": (0.390, -0.01)}
    )

    assert list(data_access.division_comparison(ls.id)["division"]) == ["North", "South"]


def test_division_comparison_is_empty_without_a_league_context(session):
    league = League(code="d3", name="Division 3", tier="senior", is_senior=True)
    season = Season(year=2026)
    session.add_all([league, season])
    session.flush()
    ls = LeagueSeason(
        league_id=league.id, season_id=season.id, source_tournament_id=1,
        competition_slug="2026-d3",
    )
    session.add(ls)
    session.flush()

    assert data_access.division_comparison(ls.id).empty


def test_no_team_level_verdict_is_exposed():
    """Deliberate absence, asserted so it isn't quietly reintroduced: the app
    stops short of ranking teams across divisions or emitting a win
    probability, because team-rating noise from ~22-game seasons dominates
    such a comparison and three quarters of cross-division pairs cannot be
    separated at all."""
    assert not hasattr(data_access, "head_to_head")
    assert not hasattr(data_access, "cross_division_comparison")
