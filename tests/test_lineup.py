import pytest

from stats.lineup import (
    BatterProfile,
    _shrunk_trait_ranks,
    build_profile,
    conservative_woba,
    evidence_label,
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


def test_conservative_woba_prefers_the_better_evidenced_of_equal_estimates():
    # Same point estimate: the hitter whose estimate has the tighter
    # posterior (more PA behind it) scores higher.
    assert conservative_woba(0.340, 60, sd=0.037) > conservative_woba(0.340, 7, sd=0.044)
    # A genuinely strong 20-PA start still beats a mediocre veteran — the
    # uncertainty term tempers small samples without burying them.
    assert conservative_woba(0.420, 20, sd=0.043) > conservative_woba(0.320, 60, sd=0.037)
    assert conservative_woba(None, 50) is None
    # Callers with no posterior SD to hand still get a conservative score
    # rather than an unpenalized one.
    assert conservative_woba(0.340, 7) < 0.340


def test_conservative_woba_no_longer_doubles_as_a_prior():
    """The old ad-hoc penalty was steep enough to reverse a 20-point gap in
    the estimates, because it was silently standing in for a prior that
    wrongly assumed unknown hitters were league average. That job now belongs
    to stats/shrinkage.py's playing-time prior (which is measured), so the
    ranking term is deliberately mild — see
    test_shrinkage.py::test_playing_time_prior_pulls_lightly_used_below_league
    and the end-to-end "Enzo" case in test_scouting_data_access.py for the
    property that actually protects the recommendation."""
    penalty_7pa = 0.340 - conservative_woba(0.340, 7, sd=0.044)
    penalty_60pa = 0.340 - conservative_woba(0.340, 60, sd=0.037)
    assert penalty_7pa < 0.05
    # And it must not meaningfully dock a well-evidenced regular.
    assert penalty_60pa < 0.04


def _nine_regulars(**overrides):
    """Eight ordinary 60-PA regulars plus whatever the test wants to add —
    a realistic lineup, since trait ranks are computed *within* the nine and
    a two-hitter lineup lets one outlier dominate the group it's compared to."""
    stats = {
        f"Regular {i}": {"pa": 60, "ab": 54, "h": 15, "avg": 0.278, "obp": 0.350, "slg": 0.400,
                         "iso": 0.122, "bb_pct": 0.10, "k_pct": 0.18, "sb": 1}
        for i in range(8)
    }
    stats.update(overrides)
    return stats


def test_slot_rationales_small_sample_makes_no_claims_but_still_informs():
    stats = _nine_regulars(
        # 8 PA of a .600 OBP: must not be cited as a strength.
        **{"Hot Start": {"pa": 8, "ab": 6, "h": 3, "doubles": 1, "bb": 2,
                         "avg": 0.500, "obp": 0.600, "slg": 0.900, "iso": 0.400,
                         "bb_pct": 0.25, "k_pct": 0.0, "sb": 2,
                         "shrunk_woba": 0.352, "shrunk_woba_sd": 0.044}}
    )
    order = ["Hot Start"] + [f"Regular {i}" for i in range(8)]
    lines = slot_rationales(order, stats)

    # The claim is suppressed — no rate stat from 8 PA is quoted as a strength.
    assert "0.600" not in lines[0]
    assert "OBP" not in lines[0] and "power" not in lines[0]
    # ...but the line still says what we think and how firmly, rather than
    # the old "too few to judge; treated as roughly league average".
    assert "8 PA so far" in lines[0]
    assert "0.352" in lines[0] and "0.308-0.396" in lines[0]
    assert "3-for-6" in lines[0]
    assert "league average" not in lines[0]


def test_slot_rationales_still_describes_the_well_evidenced():
    stats = _nine_regulars(
        **{"Table Setter": {"pa": 60, "ab": 48, "h": 16, "avg": 0.333, "obp": 0.450,
                            "slg": 0.400, "iso": 0.067, "bb_pct": 0.18, "k_pct": 0.10, "sb": 6}}
    )
    order = ["Table Setter"] + [f"Regular {i}" for i in range(8)]
    lines = slot_rationales(order, stats)
    assert "best OBP" in lines[0] and "0.450" in lines[0]


def test_shrunk_trait_ranks_do_not_cliff_at_a_threshold():
    """The old behaviour excluded anyone under 20 PA from trait ranks
    outright, so a hitter at 19 PA and one at 20 were treated completely
    differently. Shrinking the traits instead makes the transition
    continuous: the same hot line at 19 and at 20 PA gets the same rank."""
    hot = {"avg": 0.500, "obp": 0.600, "slg": 0.900, "iso": 0.400,
           "bb_pct": 0.25, "k_pct": 0.05, "sb": 2}
    ranks_at = {}
    for pa in (19, 20):
        stats = _nine_regulars(**{"Hot Start": {**hot, "pa": pa, "ab": pa - 2, "h": 6}})
        order = ["Hot Start"] + [f"Regular {i}" for i in range(8)]
        ranks_at[pa] = _shrunk_trait_ranks(order, stats)

    for key in ("obp", "iso", "k_pct", "bb_pct"):
        assert ranks_at[19][key]["Hot Start"] == ranks_at[20][key]["Hot Start"]
        assert ranks_at[19][key]["Regular 0"] == ranks_at[20][key]["Regular 0"]


def test_shrunk_trait_ranks_discount_a_thin_sample():
    """A hot 8-PA line and a proven 60-PA line at the *same* rate: shrinkage
    must leave the proven hitter ahead, since identical rates with unequal
    evidence are not equally believable."""
    line = {"avg": 0.400, "obp": 0.500, "slg": 0.700, "iso": 0.300,
            "bb_pct": 0.20, "k_pct": 0.08, "sb": 2}
    stats = _nine_regulars(
        **{
            "Thin": {**line, "pa": 8, "ab": 6, "h": 3},
            "Proven": {**line, "pa": 60, "ab": 50, "h": 20},
        }
    )
    order = ["Thin", "Proven"] + [f"Regular {i}" for i in range(8)]
    ranks = _shrunk_trait_ranks(order, stats)
    for key in ("obp", "iso", "bb_pct"):
        assert ranks[key]["Proven"] < ranks[key]["Thin"]


def test_evidence_label_states_a_projection_for_the_unplayed():
    assert evidence_label(0, 0.298, 0.046) == "No PA yet — projects 0.298 ±0.046"
    assert evidence_label(7, 0.282, 0.044) == "7 PA so far — projects 0.282 ±0.044"
    # Degrades rather than failing when there's no estimate to quote.
    assert evidence_label(7, None, None) == "7 PA so far"


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
