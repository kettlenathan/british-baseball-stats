import pytest

from stats.lineup import (
    BatterProfile,
    build_profile,
    expected_runs,
    heuristic_order,
    league_component_rates,
    optimize_lineup,
    platoon_adjusted_woba,
    select_starters,
    slot_rationales,
)

# A plausible amateur-league environment: totals shaped like a real
# league-season (rates: BB 10%, HBP 2%, 1B 17%, 2B 5%, 3B 1%, HR 1%).
LEAGUE_TOTALS = {
    "pa": 10000, "h": 2400, "doubles": 500, "triples": 100, "hr": 100,
    "bb": 1000, "hbp": 200,
}
LEAGUE_RATES = league_component_rates(LEAGUE_TOTALS)


def _league_average_profile(name: str = "Avg") -> BatterProfile:
    return build_profile(name, {"pa": 0}, LEAGUE_RATES, target_woba=None)


def _profile_with_woba(name: str, target: float) -> BatterProfile:
    counts = {"pa": 100, "h": 24, "doubles": 5, "triples": 1, "hr": 1, "bb": 10, "hbp": 2}
    return build_profile(name, counts, LEAGUE_RATES, target_woba=target)


def test_no_data_batter_gets_league_profile():
    profile = _league_average_profile()
    league_woba = sum(
        w * r
        for w, r in [
            (0.69, LEAGUE_RATES["bb"]), (0.72, LEAGUE_RATES["hbp"]), (0.89, LEAGUE_RATES["single"]),
            (1.27, LEAGUE_RATES["double"]), (1.62, LEAGUE_RATES["triple"]), (2.10, LEAGUE_RATES["hr"]),
        ]
    )
    assert profile.implied_woba == pytest.approx(league_woba, abs=1e-9)
    assert 0 < profile.p_out < 1


def test_profile_hits_target_woba():
    profile = _profile_with_woba("Target", 0.420)
    assert profile.implied_woba == pytest.approx(0.420, abs=1e-6)


def test_profile_extreme_target_is_clamped_not_degenerate():
    # An absurd target can't push event probabilities past certainty.
    counts = {"pa": 4, "h": 4, "hr": 4, "doubles": 0, "triples": 0, "bb": 0, "hbp": 0}
    profile = build_profile("Tiny Sample", counts, LEAGUE_RATES, target_woba=2.5)
    assert 0.0 <= profile.p_out <= 1.0
    assert profile.p_onbase <= 0.95 + 1e-9


def test_platoon_shrinks_toward_overall():
    # 10 PA of a huge observed platoon split moves the estimate only slightly.
    adjusted = platoon_adjusted_woba(overall_woba=0.350, vs_hand_pa=10, vs_hand_woba=0.700)
    assert 0.350 < adjusted < 0.380
    # No vs-hand data at all: unchanged.
    assert platoon_adjusted_woba(0.350, 0, None) == pytest.approx(0.350)


def test_expected_runs_scale_sanity():
    team = [_league_average_profile(f"P{i}") for i in range(9)]
    runs = expected_runs(team, innings=7)
    # A ~.330-OBP league should score a plausible amateur total, not 0 or 30.
    assert 1.0 < runs < 12.0


def test_better_team_scores_more():
    average = [_profile_with_woba(f"A{i}", 0.340) for i in range(9)]
    better = [_profile_with_woba(f"B{i}", 0.400) for i in range(9)]
    assert expected_runs(better) > expected_runs(average) + 0.5


def test_order_matters_with_mixed_lineup():
    weak = [_profile_with_woba(f"W{i}", 0.260) for i in range(8)]
    slugger = _profile_with_woba("Slugger", 0.500)
    first = expected_runs([slugger] + weak)
    last = expected_runs(weak + [slugger])
    assert first != pytest.approx(last, abs=1e-4)
    # Leading off gets the slugger more PAs, worth real runs here.
    assert first > last


def test_identical_lineup_order_is_irrelevant():
    team = [_league_average_profile(f"P{i}") for i in range(9)]
    assert expected_runs(team) == pytest.approx(expected_runs(list(reversed(team))), abs=1e-9)


def test_ten_batter_lineup_supported():
    team = [_league_average_profile(f"P{i}") for i in range(10)]
    runs = expected_runs(team)
    assert runs > 0
    # Ten league-average hitters score like nine (same talent, longer cycle).
    nine = expected_runs(team[:9])
    assert runs == pytest.approx(nine, rel=0.05)


def test_heuristic_order_slots_best_hitters_up_front():
    wobas = [0.300, 0.450, 0.320, 0.410, 0.350, 0.280, 0.390, 0.310, 0.330]
    profiles = [_profile_with_woba(f"P{i}", w) for i, w in enumerate(wobas)]
    order = heuristic_order(profiles)
    top_four_slots = set(order[:4])
    best_four = set(sorted(range(9), key=lambda i: -wobas[i])[:4])
    assert top_four_slots == best_four
    worst = min(range(9), key=lambda i: wobas[i])
    assert order.index(worst) >= 7


def test_optimize_lineup_deterministic_and_beats_baselines():
    wobas = [0.300, 0.450, 0.320, 0.410, 0.350, 0.280, 0.390, 0.310, 0.330]
    profiles = [_profile_with_woba(f"P{i}", w) for i, w in enumerate(wobas)]

    result_a = optimize_lineup(profiles)
    result_b = optimize_lineup(profiles)
    assert result_a.order == result_b.order
    assert sorted(result_a.order) == sorted(p.name for p in profiles)
    assert result_a.expected_runs >= result_a.baselines["by_woba_desc"] - 1e-6
    assert result_a.expected_runs >= result_a.baselines["as_selected"] - 1e-6
    assert len(result_a.rationale) == 9
    assert result_a.rationale[0].startswith(f"1. {result_a.order[0]}")


def test_optimize_lineup_empty():
    result = optimize_lineup([])
    assert result.order == []
    assert result.expected_runs == 0.0


def test_select_starters_takes_best_nine_and_benches_rest():
    wobas = [0.300, 0.450, 0.320, 0.410, 0.350, 0.280, 0.390, 0.310, 0.330, 0.250, 0.420]
    profiles = [_profile_with_woba(f"P{i}", w) for i, w in enumerate(wobas)]
    starters, bench = select_starters(profiles)
    assert len(starters) == 9
    # The two weakest bats (P9 at .250, P5 at .280) sit.
    assert sorted(p.name for p in bench) == ["P5", "P9"]
    # With nine or fewer, everyone starts.
    starters, bench = select_starters(profiles[:9])
    assert len(starters) == 9
    assert bench == []


def test_slot_rationales_use_box_score_stats():
    order = ["Table Setter", "Slugger", "Rookie"]
    stats = {
        "Table Setter": {"pa": 60, "avg": 0.320, "obp": 0.450, "slg": 0.400, "iso": 0.080,
                         "bb_pct": 0.18, "k_pct": 0.10, "sb": 6},
        "Slugger": {"pa": 55, "avg": 0.300, "obp": 0.380, "slg": 0.640, "iso": 0.340,
                    "bb_pct": 0.10, "k_pct": 0.25, "sb": 0},
        "Rookie": {"pa": 0},
    }
    lines = slot_rationales(order, stats)
    assert len(lines) == 3
    assert lines[0].startswith("1. Table Setter")
    assert "OBP" in lines[0] and "0.450" in lines[0]
    assert "extra-base power" in lines[1] and "0.340" in lines[1]
    # No model internals anywhere.
    assert not any("wOBA" in line for line in lines)
    assert "no season data" in lines[2]
