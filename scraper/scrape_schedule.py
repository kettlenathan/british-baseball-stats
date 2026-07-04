"""Scrape one competition-year's schedule-and-results page.

One fetch gives us everything needed to populate League/Season/LeagueSeason,
Team/TeamSeason (derived from the home/away fields on each game), and Game
rows — see scraper/recon/findings.md, the ScheduleAndResults/index.tsx
component embeds the full season's games plus tournament metadata in one
response, no pagination.
"""

import datetime as dt
import re

from sqlalchemy.orm import Session

from config import BASE_URL
from db.models import Game, League, LeagueSeason, Season, Team, TeamSeason
from db.upsert import upsert
from scraper.discovery import CANONICAL_DISPLAY_NAMES, resolve_fetch_code
from scraper.http_client import fetch_inertia

_SLUG_RE = re.compile(r"^(\d{4})-(.+)$")


def _parse_slug(tournamentkey: str) -> tuple[int, str]:
    m = _SLUG_RE.match(tournamentkey)
    if not m:
        raise ValueError(f"Unrecognized tournament key format: {tournamentkey!r}")
    return int(m.group(1)), m.group(2)


def _parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _game_status(gamestatus: int, gamestatustext: str) -> str:
    text = (gamestatustext or "").strip()
    if text.startswith("F"):
        return "final"
    if gamestatus == 0:
        return "scheduled"
    if gamestatus == -1:
        return "postponed"
    if gamestatus == -2:
        return "in_progress"
    return "unknown"


def scrape_schedule(
    league_code: str,
    year: int,
    session: Session,
    *,
    league_name: str | None = None,
    is_senior: bool = True,
    force_refresh: bool = False,
    is_current_season: bool = True,
) -> tuple[int, list[int]]:
    """Scrape one competition-year. Returns (league_season_id, final_game_source_ids)."""
    fetch_code = resolve_fetch_code(league_code, year)
    slug = f"{year}-{fetch_code}"
    url = f"{BASE_URL}/en/events/{slug}/schedule-and-results"
    data = fetch_inertia(
        url,
        "schedule",
        session=session,
        source_id=slug,
        force_refresh=force_refresh,
        is_current_season=is_current_season,
    )
    tournament = data["props"]["tournament"]
    games = data["props"]["games"]

    league_id = upsert(
        session,
        League,
        {
            "code": league_code,
            "name": (
                league_name
                or CANONICAL_DISPLAY_NAMES.get(league_code)
                or tournament.get("tournamentname")
                or league_code
            ),
            "tier": "senior" if is_senior else None,
            "is_senior": is_senior,
            "notes": None,
        },
        ["code"],
    )
    season_id = upsert(session, Season, {"year": year}, ["year"])

    tourn_year, tourn_code = _parse_slug(tournament["tournamentkey"])
    if tourn_year != year or tourn_code != fetch_code:
        raise ValueError(
            f"Requested {slug} but site returned tournament for {tournament['tournamentkey']}"
        )

    start = tournament.get("startdate")
    end = tournament.get("enddate")
    league_season_id = upsert(
        session,
        LeagueSeason,
        {
            "league_id": league_id,
            "season_id": season_id,
            "source_tournament_id": tournament["id"],
            "competition_slug": slug,
            "start_date": dt.datetime.fromisoformat(start).date() if start else None,
            "end_date": dt.datetime.fromisoformat(end).date() if end else None,
        },
        ["source_tournament_id"],
    )
    session.commit()

    final_game_source_ids: list[int] = []
    for g in games:
        home_team_season_id = _upsert_team(session, league_season_id, g["homeid"], g["homelabel"], g.get("homeioc"))
        away_team_season_id = _upsert_team(session, league_season_id, g["awayid"], g["awaylabel"], g.get("awayioc"))

        status = _game_status(g.get("gamestatus", 0), g.get("gamestatustext", ""))
        game_dt = _parse_datetime(g.get("start"))
        upsert(
            session,
            Game,
            {
                "source_id": g["id"],
                "league_season_id": league_season_id,
                "game_date": game_dt.date() if game_dt else None,
                "home_team_season_id": home_team_season_id,
                "away_team_season_id": away_team_season_id,
                "home_score": g.get("homeruns"),
                "away_score": g.get("awayruns"),
                "status": status,
                "venue": g.get("stadium") or g.get("location"),
            },
            ["source_id"],
        )
        if status == "final":
            final_game_source_ids.append(g["id"])

    session.commit()
    return league_season_id, final_game_source_ids


def _upsert_team(session: Session, league_season_id: int, source_team_id: int, label: str, short_code: str | None) -> int:
    team_id = upsert(session, Team, {"name": label}, ["name"])
    team_season_id = upsert(
        session,
        TeamSeason,
        {
            "team_id": team_id,
            "league_season_id": league_season_id,
            "source_team_id": source_team_id,
            "display_name": label,
            "short_code": short_code,
        },
        ["league_season_id", "source_team_id"],
    )
    return team_season_id
