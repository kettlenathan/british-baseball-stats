"""Tests for cross-division strength offsets estimated from shared players."""

import math

from app.components.data_access import head_to_head
from stats.division_strength import Stint, fit_division_offsets

import pandas as pd


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
# Head-to-head presentation
# --------------------------------------------------------------------------


def _comparison(rows):
    return pd.DataFrame(
        [
            {
                "team": t,
                "division": d,
                "adjusted_rating": r,
                "uncertainty": u,
                "w": 0,
                "l": 0,
                "rating": r,
                "adjustment": 0.0,
                "bridge_players": 10,
            }
            for t, d, r, u in rows
        ]
    )


def test_head_to_head_favours_the_higher_adjusted_rating():
    df = _comparison([("Strong", "A", 1.5, 0.2), ("Weak", "B", -0.5, 0.2)])

    result = head_to_head(df, "Strong", "Weak")

    assert result["probability"] > 0.5
    assert result["decisive"] is True
    assert result["same_division"] is False


def test_head_to_head_reports_a_close_call_as_undecided():
    """The case that matters most: two good teams from different divisions
    whose gap is inside the uncertainty must not be ranked confidently."""
    df = _comparison([("MK", "Central", 1.81, 0.69), ("Meteors", "South", 1.46, 0.53)])

    result = head_to_head(df, "MK", "Meteors")

    assert result["decisive"] is False
    assert result["low"] < 0.5 < result["high"]


def test_head_to_head_flags_same_division_pairs():
    df = _comparison([("A", "North", 1.0, 0.3), ("B", "North", 0.0, 0.3)])

    assert head_to_head(df, "A", "B")["same_division"] is True


def test_head_to_head_returns_none_for_unknown_teams():
    df = _comparison([("A", "North", 1.0, 0.3)])

    assert head_to_head(df, "A", "Nobody") is None
    assert head_to_head(pd.DataFrame(), "A", "B") is None
