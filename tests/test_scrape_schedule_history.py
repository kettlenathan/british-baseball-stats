"""Regression test for the d2/aaa (and d3/aa) historical code mapping: the
scraper must fetch historical seasons under the old on-site code while
storing them under the canonical code, without the site's own returned
tournamentkey tripping the schedule-mismatch guard."""

from db.models import League, LeagueSeason
from scraper.scrape_schedule import scrape_schedule


def _canned_payload(tournamentkey: str, tournamentname: str) -> dict:
    return {
        "props": {
            "tournament": {
                "tournamentkey": tournamentkey,
                "id": 555,
                "tournamentname": tournamentname,
                "startdate": None,
                "enddate": None,
            },
            "games": [],
        }
    }


def test_historical_d2_fetch_maps_to_canonical_league_and_slug(session, monkeypatch):
    payload = _canned_payload("2021-aaa", "AAA 2021")
    monkeypatch.setattr(
        "scraper.scrape_schedule.fetch_inertia", lambda *args, **kwargs: payload
    )

    league_season_id, final_game_ids = scrape_schedule("d2", 2021, session)

    assert final_game_ids == []

    league = session.query(League).filter_by(code="d2").one()
    assert league.name == "Division 2"

    league_season = session.get(LeagueSeason, league_season_id)
    assert league_season.competition_slug == "2021-aaa"


def test_current_d2_fetch_still_works_unmapped(session, monkeypatch):
    payload = _canned_payload("2026-d2", "National Baseball League Division 2")
    monkeypatch.setattr(
        "scraper.scrape_schedule.fetch_inertia", lambda *args, **kwargs: payload
    )

    league_season_id, _ = scrape_schedule("d2", 2026, session)

    league = session.query(League).filter_by(code="d2").one()
    assert league.name == "Division 2"

    league_season = session.get(LeagueSeason, league_season_id)
    assert league_season.competition_slug == "2026-d2"


def test_league_name_is_year_independent(session, monkeypatch):
    """League is keyed on code alone while `tournamentname` describes one
    competition-*year*, so scraping an older season must not relabel the whole
    league. Scraping nbl 2024 once retitled the National Baseball League
    "NBL 2024" for every season on the site.
    """
    for year, tournament_name in ((2026, "NBL 2026"), (2024, "NBL 2024")):
        payload = _canned_payload(f"{year}-nbl", tournament_name)
        monkeypatch.setattr(
            "scraper.scrape_schedule.fetch_inertia", lambda *args, **kwargs: payload
        )
        scrape_schedule("nbl", year, session)

    league = session.query(League).filter_by(code="nbl").one()
    assert league.name == "National Baseball League"


def test_every_scraped_league_code_has_a_canonical_name():
    """Any code without one falls through to the year-specific scraped name,
    which is exactly how the bug above happened."""
    from scraper.discovery import CANONICAL_DISPLAY_NAMES

    assert set(CANONICAL_DISPLAY_NAMES) >= {"nbl", "d2", "d3", "d4", "d5"}
