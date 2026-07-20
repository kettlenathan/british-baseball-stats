"""scripts.refresh_data must fail loudly when the scrape collects nothing.

The site 403-blocking the CI host makes every schedule fetch fail; pipeline.run()
swallows those per-league and returns no league_season ids. The CLI must then
exit non-zero (so the scheduled workflow fails and skips publishing) instead of
recomputing nothing and reporting success.
"""

from unittest.mock import MagicMock

import pytest

from scripts import refresh_data


def _argv(monkeypatch, *extra):
    monkeypatch.setattr(
        "sys.argv",
        ["refresh_data", "--leagues", "nbl,d2", "--years", "2026", *extra],
    )


def test_exits_nonzero_when_every_scrape_fails(monkeypatch, capsys):
    _argv(monkeypatch, "--last-month")
    monkeypatch.setattr(refresh_data, "run", lambda *a, **k: [])
    recompute = MagicMock()
    monkeypatch.setattr(refresh_data, "recompute_league_season", recompute)

    with pytest.raises(SystemExit) as excinfo:
        refresh_data.main()

    assert excinfo.value.code == 1
    assert "no league-seasons were scraped" in capsys.readouterr().err
    recompute.assert_not_called()


def test_partial_success_still_recomputes_and_exits_zero(monkeypatch):
    # One league scraped, one failed: not a total failure, so the run should
    # proceed (recompute what it got) and exit normally.
    _argv(monkeypatch)
    monkeypatch.setattr(refresh_data, "run", lambda *a, **k: [7])
    recompute = MagicMock()
    monkeypatch.setattr(refresh_data, "recompute_league_season", recompute)
    monkeypatch.setattr(refresh_data, "get_session", MagicMock())

    refresh_data.main()  # must not raise

    recompute.assert_called_once()
    assert recompute.call_args.args[1] == 7
