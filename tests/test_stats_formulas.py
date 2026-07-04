from types import SimpleNamespace

import pytest

from stats.advanced_stats import era_plus, fip, wrc_plus
from stats.advanced_stats import woba as advanced_woba
from stats.rate_stats import batting_rate_stats, outs_to_ip, pitching_rate_stats


def make_batter(**overrides):
    defaults = dict(pa=12, ab=10, h=4, doubles=1, triples=0, hr=1, bb=2, ibb=0, hbp=0, so=3, sf=0)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_pitcher(**overrides):
    defaults = dict(outs_recorded=18, h=5, r=3, er=3, bb=2, ibb=0, so=6, hr=1, hbp=0, bf=25)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_batting_rate_stats_hand_computed():
    row = make_batter()
    result = batting_rate_stats(row)
    assert result["avg"] == pytest.approx(0.4)
    assert result["obp"] == pytest.approx(0.5)
    assert result["slg"] == pytest.approx(0.8)
    assert result["ops"] == pytest.approx(1.3)
    assert result["iso"] == pytest.approx(0.4)
    assert result["bb_pct"] == pytest.approx(2 / 12)
    assert result["k_pct"] == pytest.approx(3 / 12)


def test_batting_rate_stats_zero_ab_returns_none():
    row = make_batter(ab=0, h=0, doubles=0, triples=0, hr=0, bb=0, hbp=0, so=0, sf=0, pa=0)
    result = batting_rate_stats(row)
    assert result["avg"] is None
    assert result["obp"] is None
    assert result["slg"] is None
    assert result["bb_pct"] is None


def test_woba_hand_computed():
    row = make_batter()
    # 0.69*2 + 0.72*0 + 0.89*2 + 1.27*1 + 1.62*0 + 2.10*1 = 6.53; /12
    assert advanced_woba(row) == pytest.approx(6.53 / 12)


def test_wrc_plus():
    assert wrc_plus(0.400, 0.320) == pytest.approx(100 * 0.4 / 0.32)
    assert wrc_plus(None, 0.320) is None
    assert wrc_plus(0.4, 0.0) is None


def test_outs_to_ip():
    assert outs_to_ip(18) == pytest.approx(6.0)
    assert outs_to_ip(19) == pytest.approx(19 / 3)


def test_pitching_rate_stats_hand_computed():
    row = make_pitcher()
    result = pitching_rate_stats(row)
    assert result["ip"] == pytest.approx(6.0)
    assert result["era"] == pytest.approx(4.5)
    assert result["whip"] == pytest.approx(7 / 6)
    assert result["k9"] == pytest.approx(9.0)
    assert result["bb9"] == pytest.approx(3.0)


def test_pitching_rate_stats_zero_ip_returns_none():
    row = make_pitcher(outs_recorded=0)
    result = pitching_rate_stats(row)
    assert result["era"] is None
    assert result["whip"] is None


def test_fip_hand_computed():
    row = make_pitcher()
    # (13*1 + 3*(2+0) - 2*6)/6 + 3.10 = 7/6 + 3.10
    assert fip(row, fip_constant=3.10) == pytest.approx(7 / 6 + 3.10)


def test_fip_none_without_constant():
    row = make_pitcher()
    assert fip(row, fip_constant=None) is None


def test_era_plus():
    assert era_plus(player_era=4.5, lg_era=4.5) == pytest.approx(100.0)
    assert era_plus(player_era=None, lg_era=4.5) is None
    assert era_plus(player_era=0, lg_era=4.5) is None
