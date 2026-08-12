import math

import pytest

from db.models import (
    BattingSeasonStats,
    BattingTrueTalent,
    League,
    LeagueSeason,
    LeagueSeasonContext,
    PitchingSeasonStats,
    PitchingTrueTalent,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from stats.shrinkage import (
    FALLBACK_BATTING_STABILIZATION_PA,
    FALLBACK_PITCHING_STABILIZATION_IP,
    FALLBACK_PLAYING_TIME_PRIOR,
    FALLBACK_PLAYING_TIME_SLOPE,
    PLAYING_TIME_PRIOR_DAMPING,
    _batting_component_variance,
    _pitching_component_variance,
    compute_batting_true_talent,
    compute_pitching_true_talent,
    estimate_batting_stabilization_pa,
    estimate_pitching_stabilization_ip,
    fit_playing_time_prior,
    posterior_sd,
    shrink_rate,
)


def test_shrink_rate_at_zero_n_returns_league_mean():
    assert shrink_rate(observed=None, n=0, league_mean=0.320, k=100) == pytest.approx(0.320)


def test_shrink_rate_large_n_approaches_observed():
    result = shrink_rate(observed=0.400, n=100_000, league_mean=0.320, k=100)
    assert result == pytest.approx(0.400, abs=1e-3)


def test_shrink_rate_symmetric_at_n_equals_k():
    result = shrink_rate(observed=0.400, n=100, league_mean=0.320, k=100)
    assert result == pytest.approx((0.400 + 0.320) / 2)


def test_shrink_rate_without_league_mean_returns_observed():
    assert shrink_rate(observed=0.400, n=50, league_mean=None, k=100) == pytest.approx(0.400)


def test_batting_component_variance_hand_computed():
    totals = {"h": 100, "doubles": 20, "triples": 2, "hr": 10, "bb": 30, "ibb": 5, "hbp": 5}
    v_e = _batting_component_variance(totals, league_pa=500)
    expected = (
        0.69**2 * (25 / 500)
        + 0.72**2 * (5 / 500)
        + 0.89**2 * (68 / 500)
        + 1.27**2 * (20 / 500)
        + 1.62**2 * (2 / 500)
        + 2.10**2 * (10 / 500)
    )
    assert v_e == pytest.approx(expected)


def test_batting_component_variance_zero_pa_returns_none():
    totals = {"h": 0, "doubles": 0, "triples": 0, "hr": 0, "bb": 0, "ibb": 0, "hbp": 0}
    assert _batting_component_variance(totals, league_pa=0) is None


def test_pitching_component_variance_hand_computed():
    totals = {"hr": 10, "bb": 40, "hbp": 5, "so": 100}
    v_e = _pitching_component_variance(totals, league_ip=200.0)
    expected = 13.0**2 * (10 / 200) + 3.0**2 * (45 / 200) + 2.0**2 * (100 / 200)
    assert v_e == pytest.approx(expected)


def test_pitching_component_variance_zero_ip_returns_none():
    assert _pitching_component_variance({"hr": 0, "bb": 0, "hbp": 0, "so": 0}, league_ip=0.0) is None


def test_estimate_batting_stabilization_pa_falls_back_without_variance():
    k, self_calibrated = estimate_batting_stabilization_pa(rows=[(50, 0.320)] * 10, v_e=None)
    assert k == FALLBACK_BATTING_STABILIZATION_PA
    assert self_calibrated is False


def test_estimate_batting_stabilization_pa_falls_back_with_too_few_players():
    rows = [(50, 0.320 + 0.01 * i) for i in range(5)]  # fewer than MIN_QUALIFYING_PLAYERS
    k, self_calibrated = estimate_batting_stabilization_pa(rows, v_e=0.001)
    assert k == FALLBACK_BATTING_STABILIZATION_PA
    assert self_calibrated is False


def test_estimate_batting_stabilization_pa_falls_back_when_variance_is_noise():
    # Identical observed rate across all players -> zero between-player
    # variance -> tau^2 goes negative against any positive sampling noise.
    rows = [(50, 0.320) for _ in range(10)]
    k, self_calibrated = estimate_batting_stabilization_pa(rows, v_e=0.001)
    assert k == FALLBACK_BATTING_STABILIZATION_PA
    assert self_calibrated is False


def test_estimate_batting_stabilization_pa_self_calibrates_with_real_spread():
    # Ten players, wide spread of observed rates, ample PA -> the sampling-
    # noise correction is tiny relative to that spread -> tau^2 stays
    # positive -> self-calibrates instead of falling back.
    rows = [(200, 0.250 + 0.02 * i) for i in range(10)]
    k, self_calibrated = estimate_batting_stabilization_pa(rows, v_e=0.001)
    assert self_calibrated is True
    assert k > 0


def test_estimate_pitching_stabilization_ip_falls_back_without_variance():
    k, self_calibrated = estimate_pitching_stabilization_ip(rows=[(20.0, 4.20)] * 10, v_e=None)
    assert k == FALLBACK_PITCHING_STABILIZATION_IP
    assert self_calibrated is False


def test_estimate_pitching_stabilization_ip_self_calibrates_with_real_spread():
    rows = [(50.0, 3.00 + 0.30 * i) for i in range(10)]
    k, self_calibrated = estimate_pitching_stabilization_ip(rows, v_e=0.01)
    assert self_calibrated is True
    assert k > 0


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
    team = Team(name="Test Team")
    session.add(team)
    session.flush()
    team_season = TeamSeason(
        team_id=team.id, league_season_id=league_season.id, source_team_id=100, display_name="Test Team"
    )
    session.add(team_season)
    session.flush()
    return league_season, team_season


def _add_batter(session, team_season, source_id, **stat_overrides):
    player = Player(source_id=source_id, full_name=f"Batter {source_id}")
    session.add(player)
    session.flush()
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(player_season)
    session.flush()
    defaults = dict(pa=0, ab=0, h=0, doubles=0, triples=0, hr=0, bb=0, ibb=0, hbp=0, so=0, sf=0)
    defaults.update(stat_overrides)
    session.add(BattingSeasonStats(player_season_id=player_season.id, **defaults))
    session.flush()
    return player_season


def _add_pitcher(session, team_season, source_id, **stat_overrides):
    player = Player(source_id=source_id, full_name=f"Pitcher {source_id}")
    session.add(player)
    session.flush()
    player_season = PlayerSeason(player_id=player.id, team_season_id=team_season.id)
    session.add(player_season)
    session.flush()
    defaults = dict(outs_recorded=0, h=0, r=0, er=0, bb=0, ibb=0, so=0, hr=0, hbp=0, bf=0)
    defaults.update(stat_overrides)
    session.add(PitchingSeasonStats(player_season_id=player_season.id, **defaults))
    session.flush()
    return player_season


def test_compute_batting_true_talent_returns_zero_without_league_context(session):
    league_season, team_season = _build_league_season(session)
    _add_batter(session, team_season, source_id=1, pa=20, ab=18, h=5)
    session.commit()

    assert compute_batting_true_talent(session, league_season.id) == 0
    assert session.query(BattingTrueTalent).count() == 0


def test_compute_batting_true_talent_shrinks_toward_league_mean(session):
    league_season, team_season = _build_league_season(session)
    _add_batter(session, team_season, source_id=1, pa=10, ab=9, h=5, doubles=1, hr=1, bb=1)
    _add_batter(session, team_season, source_id=2, pa=150, ab=130, h=40, doubles=8, hr=5, bb=15)
    session.add(LeagueSeasonContext(league_season_id=league_season.id, lg_woba=0.320))
    session.commit()

    count = compute_batting_true_talent(session, league_season.id)
    assert count == 2

    rows = {r.pa: r for r in session.query(BattingTrueTalent).all()}
    low_pa, high_pa = rows[10], rows[150]

    # Only two players (< MIN_QUALIFYING_PLAYERS) -> falls back to the
    # published stabilization constant rather than self-calibrating.
    assert low_pa.k_self_calibrated is False
    assert low_pa.stabilization_pa == pytest.approx(FALLBACK_BATTING_STABILIZATION_PA)

    # Both shrunk estimates sit between the observed rate and the prior they
    # were shrunk toward — which is the playing-time-aware prior, NOT the
    # flat league mean, so a lightly-used hitter's estimate can legitimately
    # fall below .320 even when their observed rate is above it.
    for row in (low_pa, high_pa):
        lo, hi = sorted([row.observed_woba, row.prior_woba])
        assert lo <= row.shrunk_woba <= hi
    assert low_pa.prior_woba < high_pa.prior_woba
    assert high_pa.reliability > low_pa.reliability
    assert abs(high_pa.shrunk_woba - high_pa.observed_woba) < abs(low_pa.shrunk_woba - low_pa.observed_woba)


def test_playing_time_prior_slopes_upward_and_averages_to_zero():
    """The fit must (a) rate lightly-used hitters below regulars and (b) not
    move the league's overall level while doing so — the deviation is
    PA-weighted zero by construction, so re-basing who the mean applies to
    can't quietly inflate or deflate the league."""
    rows = [(pa, 0.340 + 0.05 * math.log(pa / 40)) for pa in (5, 8, 12, 20, 30, 45, 60, 90, 120, 200)] * 5
    prior = fit_playing_time_prior(rows, league_mean=0.340)

    assert prior.self_calibrated
    assert prior.deviation(5) < prior.deviation(20) < prior.deviation(60) < prior.deviation(200)
    assert prior.deviation(5) < 0 < prior.deviation(200)

    total_pa = sum(pa for pa, _ in rows)
    weighted = sum(pa * prior.deviation(pa) for pa, _ in rows) / total_pa
    assert weighted == pytest.approx(0.0, abs=1e-9)


def test_playing_time_prior_is_damped_not_taken_at_face_value():
    """The raw fit overstates the durable effect (it also picks up hitters
    whose season was frozen at its worst); scripts/validate_low_pa_prior.py
    shows applying it undamped scores worse than doing nothing."""
    rows = [(pa, 0.340 + 0.05 * math.log(pa / 40)) for pa in (5, 8, 12, 20, 30, 45, 60, 90, 120, 200)] * 5
    damped = fit_playing_time_prior(rows, league_mean=0.340)
    undamped = fit_playing_time_prior(rows, league_mean=0.340, damping=1.0)
    assert damped.slope == pytest.approx(PLAYING_TIME_PRIOR_DAMPING * undamped.slope)
    assert 0 < damped.slope < undamped.slope


def test_playing_time_prior_falls_back_to_the_corpus_curve_not_to_flat():
    """A league-season too small to fit its own curve must not silently
    revert to "an unknown hitter is league average" — that assumption is what
    this layer exists to remove, and it is wrong in every league-season
    measured. It falls back to the corpus-wide curve instead, exactly as the
    stabilization point falls back to a published constant."""
    prior = fit_playing_time_prior([(20, 0.340), (30, 0.345)], league_mean=0.340)
    assert not prior.self_calibrated
    assert prior.slope == pytest.approx(FALLBACK_PLAYING_TIME_SLOPE)
    assert prior.deviation(5) < prior.deviation(200)

    # Only the *slope* is borrowed — the pivot is always recomputed from the
    # population at hand, so the fallback still averages to zero over it
    # instead of marking the whole group down. Borrowing a pivot fitted on
    # full seasons and applying it to a partial one is exactly how that goes
    # wrong.
    rows = [(pa, 0.340) for pa in (5, 10, 20, 30, 40)]
    fallback = fit_playing_time_prior(rows, league_mean=0.340)
    total_pa = sum(pa for pa, _ in rows)
    assert sum(pa * fallback.deviation(pa) for pa, _ in rows) / total_pa == pytest.approx(0.0, abs=1e-9)
    assert fallback.center_log_pa != FALLBACK_PLAYING_TIME_PRIOR.center_log_pa

    # A perverse fit (regulars supposedly worse than scrubs) is refused
    # rather than inverted.
    backwards = [(pa, 0.340 - 0.05 * math.log(pa / 40)) for pa in (5, 20, 60, 200)] * 15
    assert not fit_playing_time_prior(backwards, league_mean=0.340).self_calibrated


def test_posterior_sd_shrinks_with_evidence_and_starts_at_talent_spread():
    v_e, k = 0.25, 120.0
    tau = (v_e / k) ** 0.5
    # No data at all: uncertainty is exactly the between-player spread.
    assert posterior_sd(v_e, 0, k) == pytest.approx(tau)
    assert posterior_sd(v_e, 60, k) < posterior_sd(v_e, 7, k) < posterior_sd(v_e, 0, k)
    assert posterior_sd(None, 30, k) is None


def test_lightly_used_hitter_is_not_projected_as_league_average(session):
    """The headline behaviour change: an 0-for-7 hitter must land clearly
    below the league mean, not a whisker under it. Their prior is a lightly-
    used hitter's prior, and lightly-used hitters in this league are worse.

    This is what keeps the scouting report from recommending the least-known
    player (see test_scouting_data_access.py's "Enzo" case) — the protection
    lives here, in the estimate, rather than in a ranking penalty tuned to
    cancel a prior that was wrong in the first place."""
    league_season, team_season = _build_league_season(session)
    _add_batter(session, team_season, source_id=1, pa=7, ab=7, h=0, so=4)
    _add_batter(session, team_season, source_id=2, pa=60, ab=55, h=14, doubles=2, bb=4, so=10)
    session.add(LeagueSeasonContext(league_season_id=league_season.id, lg_woba=0.340))
    session.commit()

    compute_batting_true_talent(session, league_season.id)
    rows = {r.pa: r for r in session.query(BattingTrueTalent).all()}
    thin, vet = rows[7], rows[60]

    # The 7-PA hitter is shrunk toward a prior well below the league mean...
    assert thin.prior_woba < 0.340 - 0.02
    assert vet.prior_woba > thin.prior_woba
    # ...so despite 0-for-7 barely moving the needle, the estimate itself now
    # ranks him behind the vet's real (below-average) track record.
    assert thin.shrunk_woba < vet.shrunk_woba
    # And every row carries the uncertainty a consumer needs to rank on.
    assert thin.shrunk_woba_sd > vet.shrunk_woba_sd > 0


def test_compute_pitching_true_talent_shrinks_toward_league_mean(session):
    league_season, team_season = _build_league_season(session)
    _add_pitcher(session, team_season, source_id=1, outs_recorded=15, h=6, er=4, bb=3, so=5, hr=1)
    _add_pitcher(session, team_season, source_id=2, outs_recorded=300, h=90, er=45, bb=40, so=90, hr=10)
    session.add(LeagueSeasonContext(league_season_id=league_season.id, lg_fip=4.20, fip_constant=3.10))
    session.commit()

    count = compute_pitching_true_talent(session, league_season.id)
    assert count == 2

    rows = {round(r.ip, 2): r for r in session.query(PitchingTrueTalent).all()}
    low_ip, high_ip = rows[5.0], rows[100.0]

    for row in (low_ip, high_ip):
        lo, hi = sorted([row.observed_fip, 4.20])
        assert lo <= row.shrunk_fip <= hi
    assert high_ip.reliability > low_ip.reliability
