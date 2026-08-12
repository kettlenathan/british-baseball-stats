"""Scrape one competition-year's schedule-and-results page.

One fetch gives us everything needed to populate League/Season/LeagueSeason,
Team/TeamSeason (derived from the home/away fields on each game), and Game
rows — see scraper/recon/findings.md, the ScheduleAndResults/index.tsx
component embeds the full season's games plus tournament metadata in one
response, no pagination.
"""

import datetime as dt
import re
from collections import Counter

from sqlalchemy.orm import Session

from config import BASE_URL
from db.models import Game, League, LeagueSeason, Season, Team, TeamSeason
from db.upsert import upsert
from scraper.discovery import league_display_name, resolve_fetch_code
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


# A game's label when it is part of the regular season: "Week 12", a bare
# round number ("12"), or the literal "Regular Season". The optional "(r)"
# suffix marks a replayed/rescheduled fixture and does not change the phase.
_REGULAR_LABEL_RE = re.compile(r"^\s*(week\s*\d+|\d+|regular season)\s*(\(r\))?\s*$", re.I)
# Playoff wording as it actually appears across the corpus. "Wildcard" and
# the abbreviated "AA SF1" / "NBL Final - Game 1" forms matter as much as the
# obvious ones: they are exactly the labels a round-id-only rule gets right
# and a naive "contains 'final'" rule misses.
_PLAYOFF_LABEL_RE = re.compile(
    r"final|qualifier|semi|quarter|championship|play-?off|wild ?card|bronze|medal"
    r"|\bsf\d*\b|\bf\d+\b|3rd place|third place",
    re.I,
)


def _modal_round_id(games: list[dict]) -> int | None:
    """The round id carrying most of a season's games — i.e. its regular season."""
    counts = Counter(g.get("wbsc_tournament_round_id") for g in games)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _game_phase(gametypelabel: str | None, round_id: int | None, modal_round_id: int | None) -> str:
    """Classify a game as "regular" or "playoff".

    No single field answers this. Playoff games usually sit on a different
    `wbsc_tournament_round_id` from the season's main one, but that rule
    alone is wrong in both directions: 2024's Division 4 files 327 plainly
    regular-season games on off-rounds, and 2025 files two "Week 19" games on
    a playoff round. So the game's own label is consulted first where it is
    unambiguous, and the round id only breaks ties.

    Checked against all 5,531 scraped games: 140 classify as playoff, and
    every distinct label in that set reads as one (Final, Semifinal,
    Wildcard, Qualifier, Championship Series, 3rd place).
    """
    label = gametypelabel or ""
    if _PLAYOFF_LABEL_RE.search(label):
        return "playoff"
    if _REGULAR_LABEL_RE.match(label):
        return "regular"
    if modal_round_id is not None and round_id != modal_round_id:
        return "playoff"
    return "regular"


def _game_status(gamestatus: int, gamestatustext: str) -> str:
    """Back-compatible status alone; prefer _classify_game, which also says
    *how* a final game finished."""
    return _classify_game(gamestatus, gamestatustext, 0, 0, 0)[0]


def _classify_game(
    gamestatus: int,
    gamestatustext: str,
    home_runs: int | None,
    away_runs: int | None,
    innings: int | None,
) -> tuple[str, str | None]:
    """Return (status, result_type) for one scheduled-page game record.

    A game can supply a *result* and a *box score* independently, and this
    league's data separates them constantly, so both are reported.

    Reading `gamestatustext.startswith("F")` alone — which is all this used
    to do — silently discarded 545 games that produced a real competitive
    result across the corpus:

    * **Forfeits** (`gamestatus` 4, blank text, 7-0 or 0-7, zero innings,
      no line score) — 360 of them. Awarded without play.
    * **Result-only** games (`gamestatus` 3, blank text, a real score like
      14-8, zero innings, no line score, no hits) — 185. Contested, but
      nobody ever entered a scoresheet.

    Both count toward the standings the federation itself publishes:
    including them lifts exact agreement with the site's own tables from 121
    of 436 team-seasons to 231, and reproduces 2026 Division 3 Central
    exactly. Neither can contribute a batting line or a run environment,
    which is what `result_type` is for.

    Zero innings *and* a zero line score are what separate these from played
    games — 99.9% of played finals carry both, and none of these carry
    either — so the discriminator is the absence of any record of play, not
    the score pattern. A genuine 7-0 win that went the distance has innings
    and is classified "played".
    """
    text = (gamestatustext or "").strip()
    home_runs = home_runs or 0
    away_runs = away_runs or 0
    played_innings = innings or 0

    if text.startswith("F"):
        # The site's own "final" marker. Trust it, but a blank line score
        # still means no box score exists to fetch.
        return "final", "played" if played_innings else "result_only"

    if gamestatus == 0:
        return "scheduled", None
    if gamestatus == -1:
        return "postponed", None
    if gamestatus == -2:
        return "in_progress", None
    if gamestatus == -3:
        # Cancelled. A handful carry a stray score, but with no innings and
        # no line score there is no result to count.
        return "cancelled", None

    if played_innings:
        # Abandoned part-way ("T6", "B5"): innings were played and a score
        # stands, so it is a result with a partial box score.
        return "final", "played"

    if home_runs + away_runs > 0:
        # No innings, no line score, but a score on the record.
        forfeit = {home_runs, away_runs} == {7, 0}
        return "final", "forfeit" if forfeit else "result_only"

    return "unknown", None


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
    """Scrape one competition-year.

    Returns (league_season_id, source ids of games with a box score to
    fetch) — i.e. final games that were actually *played*, which excludes
    forfeits and result-only games (see _classify_game).
    """
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
            "name": league_display_name(
                league_code,
                tournament_name=tournament.get("tournamentname"),
                override=league_name,
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

    # Phase depends on which round holds the bulk of the season, so it can
    # only be judged with every game in hand — hence one pass over `games`
    # before the upsert loop rather than a per-game lookup.
    modal_round_id = _modal_round_id(games)

    final_game_source_ids: list[int] = []
    for g in games:
        home_team_season_id = _upsert_team(session, league_season_id, g["homeid"], g["homelabel"], g.get("homeioc"))
        away_team_season_id = _upsert_team(session, league_season_id, g["awayid"], g["awaylabel"], g.get("awayioc"))

        status, result_type = _classify_game(
            g.get("gamestatus", 0),
            g.get("gamestatustext", ""),
            g.get("homeruns"),
            g.get("awayruns"),
            g.get("innings"),
        )
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
                "result_type": result_type,
                "venue": g.get("stadium") or g.get("location"),
                # Raw group tag as scraped. Game.division_id is resolved from
                # team membership later (scraper/scrape_standings.py), not
                # from this — it is absent for whole seasons, and standings
                # covers those.
                "source_group_id": g.get("wbsc_tournament_group_id"),
                "phase": _game_phase(
                    g.get("gametypelabel"), g.get("wbsc_tournament_round_id"), modal_round_id
                ),
            },
            ["source_id"],
        )
        # Only games that were actually played have a box score to fetch.
        # Forfeits and result-only games are final and count in the
        # standings, but requesting a scoresheet for them would spend a
        # rate-limited request per game to find nothing.
        if status == "final" and result_type == "played":
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
