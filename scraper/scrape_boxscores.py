"""Scrape one game's box score.

Per scraper/recon/findings.md: the BoxScore/index Inertia payload's
`viewData.original.boxScore` is a dict keyed by team_id, each value a dict
keyed by batting-order spot ("1".."9", plus "90" for pitchers who didn't
bat) -> list of player-appearance records (substitutions produce multiple
records per spot). Each record carries batting, pitching, and fielding
fields together on the same object — a two-way player has both non-zero.

Records are grouped by playerid and summed before upserting, since a player
who appears in multiple lineup-spot entries (pinch hit / substitution) would
otherwise only have their last entry's stats kept.
"""

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from config import BASE_URL
from db.models import BattingGameLine, Game, PitchingGameLine, Player, PlayerSeason, TeamSeason
from db.upsert import upsert
from scraper.discovery import resolve_fetch_code
from scraper.http_client import fetch_inertia

_NON_TEAM_KEYS = {"totals", "pitchers"}

_BATTING_SUM_FIELDS = {
    "pa": "pa",
    "ab": "ab",
    "r": "r",
    "h": "h",
    "double": "doubles",
    "triple": "triples",
    "hr": "hr",
    "rbi": "rbi",
    "bb": "bb",
    "ibb": "ibb",
    "hbp": "hbp",
    "so": "so",
    "sf": "sf",
    "sh": "sh",
    "sb": "sb",
    "cs": "cs",
    "gdp": "gdp",
    "field_po": "field_po",
    "field_a": "field_a",
    "field_e": "field_e",
    "field_dp": "field_dp",
    "field_sba": "field_sba",
    "field_csb": "field_csb",
    "field_pb": "field_pb",
}

_PITCHING_SUM_FIELDS = {
    "pitch_h": "h",
    "pitch_r": "r",
    "pitch_er": "er",
    "pitch_bb": "bb",
    "pitch_ibb": "ibb",
    "pitch_so": "so",
    "pitch_hr": "hr",
    "pitch_hbp": "hbp",
    "pitch_bf": "bf",
}


def _outs_from_ip(ip_str: str | None) -> int:
    if not ip_str:
        return 0
    whole_str, _, frac_str = str(ip_str).partition(".")
    whole = int(whole_str or 0)
    frac = int(frac_str or 0)
    return whole * 3 + frac


def _flatten_and_group(box_score: dict[str, Any] | list) -> dict[int, list[dict[str, Any]]]:
    by_player: dict[int, list[dict[str, Any]]] = {}
    if not isinstance(box_score, dict):
        # Some "final" games (observed: weather-shortened/suspended games)
        # have no player-level stats ever entered — the site returns an
        # empty list instead of the usual per-team dict. Nothing to scrape.
        return by_player
    for team_key, spots in box_score.items():
        if team_key in _NON_TEAM_KEYS:
            continue
        for _spot, records in spots.items():
            for rec in records:
                by_player.setdefault(rec["playerid"], []).append(rec)
    return by_player


def scrape_boxscore(
    league_code: str,
    year: int,
    game_source_id: int,
    session: Session,
    *,
    force_refresh: bool = False,
    is_current_season: bool = True,
) -> None:
    slug = f"{year}-{resolve_fetch_code(league_code, year)}"
    url = f"{BASE_URL}/en/events/{slug}/schedule-and-results/box-score/{game_source_id}"
    data = fetch_inertia(
        url,
        "boxscore",
        session=session,
        source_id=str(game_source_id),
        force_refresh=force_refresh,
        is_current_season=is_current_season,
    )
    original = data["props"]["viewData"]["original"]
    box_score = original["boxScore"]

    game = session.query(Game).filter_by(source_id=game_source_id).one()
    team_season_by_source_id = {
        ts.source_team_id: ts.id
        for ts in session.query(TeamSeason).filter(
            TeamSeason.id.in_([game.home_team_season_id, game.away_team_season_id])
        )
    }

    by_player = _flatten_and_group(box_score)

    for source_player_id, records in by_player.items():
        last = records[-1]
        player_info = last.get("player") or {}
        birth_year = None
        dob = player_info.get("dob")
        if dob and str(dob).isdigit():
            birth_year = int(dob)

        first_name = last.get("firstname") or player_info.get("firstname")
        last_name = last.get("lastname") or player_info.get("lastname")
        full_name = f"{first_name} {last_name}".strip() if first_name or last_name else str(source_player_id)

        player_id = upsert(
            session,
            Player,
            {
                "source_id": source_player_id,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": full_name,
                "birth_year": birth_year,
                "bats": player_info.get("bats"),
                "throws": player_info.get("throws"),
                "nationality": player_info.get("nationality"),
            },
            ["source_id"],
        )

        team_season_id = team_season_by_source_id.get(last["teamid"])
        if team_season_id is None:
            # Player attributed to a team not in this game's home/away pair
            # (shouldn't happen) — skip rather than corrupt the row.
            continue

        player_season_id = upsert(
            session,
            PlayerSeason,
            {
                "player_id": player_id,
                "team_season_id": team_season_id,
                "jersey_number": last.get("uniform"),
                "position_primary": last.get("pos"),
            },
            ["player_id", "team_season_id"],
        )

        batting_totals = {model_field: 0 for model_field in _BATTING_SUM_FIELDS.values()}
        pitching_totals = {model_field: 0 for model_field in _PITCHING_SUM_FIELDS.values()}
        outs_recorded = 0
        pitch_win = pitch_loss = pitch_save = False

        for rec in records:
            for src_field, model_field in _BATTING_SUM_FIELDS.items():
                batting_totals[model_field] += int(rec.get(src_field) or 0)
            for src_field, model_field in _PITCHING_SUM_FIELDS.items():
                pitching_totals[model_field] += int(rec.get(src_field) or 0)
            outs_recorded += _outs_from_ip(rec.get("pitch_ip"))
            pitch_win = pitch_win or bool(rec.get("pitch_win"))
            pitch_loss = pitch_loss or bool(rec.get("pitch_loss"))
            pitch_save = pitch_save or bool(rec.get("pitch_save"))

        if batting_totals["pa"] > 0 or batting_totals["ab"] > 0:
            upsert(
                session,
                BattingGameLine,
                {
                    "game_id": game.id,
                    "player_season_id": player_season_id,
                    "team_season_id": team_season_id,
                    "position": last.get("pos"),
                    **batting_totals,
                },
                ["game_id", "player_season_id"],
            )

        if pitching_totals["bf"] > 0 or outs_recorded > 0:
            upsert(
                session,
                PitchingGameLine,
                {
                    "game_id": game.id,
                    "player_season_id": player_season_id,
                    "team_season_id": team_season_id,
                    "outs_recorded": outs_recorded,
                    "win": pitch_win,
                    "loss": pitch_loss,
                    "save": pitch_save,
                    **pitching_totals,
                },
                ["game_id", "player_season_id"],
            )

    session.commit()
