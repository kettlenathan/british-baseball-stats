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

The same payload's `viewData.original.gamePlays.all` is a play-by-play feed
(sibling of boxScore, not surfaced in the site's own UI) — a dict keyed by
inning number, each value {"top": [...], "bottom": [...]} of per-play
records with before/after base-runner state. See
scraper/recon/risp_lob_plan.md for how this is used: RISP at-bats/hits per
batter (folded into batting_game_lines) and runners left on base per team
per game (folded into games.home_lob/away_lob). `batterid` in these records
is confirmed to be the same id space as boxScore's `playerid` (verified: a
game's gamePlays batterids are always a subset of its boxScore playerids);
`pitcherid` is assumed to be the same id space but this is less thoroughly
verified — see PlateAppearance.pitcher_player_season_id's nullability.

The same feed also yields one row per plate appearance (db.models.
PlateAppearance) for batter pull/spray tendency, batter-vs-pitcher matchups,
and pitcher first-pitch-strike% — see `_extract_plate_appearances` below.
Several of this league's per-pitch fields are confirmed always zero and
never usable (`ball`/`called`/`swing`/`foul`/`inplay`, and the true
`hitx`/`hity`/`exitvelo` batted-ball coordinates) — first-pitch-strike is
instead derived by diffing the `balls`/`strikes` count between pitches (see
`_first_pitch_strike`), and batted-ball location is approximated from
`hitpull`/`hitdistance`/`hittype` instead of true coordinates.

Each play's free-text `narrative` is the one place the fielder's *position*
on an error is recorded (standard scorer notation: "E6", "E4T"), since the
`errortype` field is another dead always-zero one and no play record names
the fielder. That notation is what lets `_extract_fielding_lines` break a
player-game's fielding totals down by position (db.models.FieldingGameLine).
"""

import re
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from config import BASE_URL
from db.models import (
    BattingGameLine,
    FieldingGameLine,
    Game,
    PitchingGameLine,
    PlateAppearance,
    Player,
    PlayerSeason,
    TeamSeason,
)
from db.upsert import upsert
from scraper.discovery import resolve_fetch_code
from scraper.http_client import fetch_inertia

_NON_TEAM_KEYS = {"totals", "pitchers"}

# Standard scorer's position numbering, as it appears in the play-by-play
# narrative's error notation (see _extract_narrative_errors).
_POSITION_BY_NUMBER = {
    "1": "P", "2": "C", "3": "1B", "4": "2B", "5": "3B",
    "6": "SS", "7": "LF", "8": "CF", "9": "RF",
}

# "E6" (fielding error by the shortstop), "E4T" (throwing error by the second
# baseman), "E7" (dropped fly by the left fielder). Requiring a digit right
# after the E is what keeps surnames out — this league has plenty of players
# called EVANS and ELLIS, and none of them match.
_ERROR_TOKEN = re.compile(r"\bE([1-9])[A-Z]*\b")

# Positions a fielding line can be filed under. Anything the box score names
# that isn't a real defensive position (DH/PH/PR, or a blank `pos`) can still
# carry fielding counts in this data, so they're kept rather than dropped;
# UNKNOWN_POSITION is the residue neither source could place.
UNKNOWN_POSITION = "UNK"

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


def _extract_risp_totals(game_plays: dict[str, Any], ps_id_by_source: dict[int, int]) -> dict[int, dict[str, int]]:
    """Returns {player_season_id: {"risp_ab": n, "risp_h": n}} — an at-bat
    counts as RISP if a runner occupied 2nd or 3rd *before* the play. Plays
    that aren't an official at-bat (walks, HBP, sac plays: ab != 1) don't
    count, matching the standard AVG definition."""
    totals: dict[int, dict[str, int]] = {}
    if not isinstance(game_plays, dict):
        return totals
    for halves in game_plays.values():
        if not isinstance(halves, dict):
            continue
        for plays in halves.values():
            if not isinstance(plays, list):
                continue
            for play in plays:
                if play.get("ab") != 1:
                    continue
                if not (play.get("runner2") or play.get("runner3")):
                    continue
                ps_id = ps_id_by_source.get(play.get("batterid"))
                if ps_id is None:
                    continue
                entry = totals.setdefault(ps_id, {"risp_ab": 0, "risp_h": 0})
                entry["risp_ab"] += 1
                entry["risp_h"] += int(play.get("h") or 0)
    return totals


def _extract_lob(game_plays: dict[str, Any]) -> tuple[int | None, int | None]:
    """Returns (home_lob, away_lob): for each half-inning, runners left on
    base are read off the *last* play's after-state (top of the inning is
    always the away team batting, bottom always home — a structural rule,
    not dependent on the per-play `home` flag). None if no play-by-play was
    available for this game at all."""
    if not isinstance(game_plays, dict) or not game_plays:
        return None, None

    home_lob = 0
    away_lob = 0
    found_any = False
    for halves in game_plays.values():
        if not isinstance(halves, dict):
            continue
        for half_name, plays in halves.items():
            if not isinstance(plays, list) or not plays:
                continue
            last = plays[-1]
            left = sum(1 for f in ("runner1after", "runner2after", "runner3after") if last.get(f))
            found_any = True
            if half_name == "bottom":
                home_lob += left
            elif half_name == "top":
                away_lob += left
    return (home_lob, away_lob) if found_any else (None, None)


def _first_pitch_strike(pa_records: list[dict[str, Any]]) -> bool | None:
    """This league's per-pitch called/swing/foul/inplay flags are confirmed
    always zero (dead fields, never populated) — so a first-pitch strike
    can't be read directly off a flag. Instead, find the true first pitch
    (the earliest record at a 0-0 count) and diff the balls/strikes count
    against the next record in the PA: strikes increased -> strike, balls
    increased -> ball. If the first pitch is itself the PA-ending record
    (ball in play or HBP on 0-0), it's a strike unless it was an HBP. Returns
    None if undeterminable (no 0-0 record found)."""
    fp_idx = next(
        (i for i, r in enumerate(pa_records) if r.get("balls") == 0 and r.get("strikes") == 0), None
    )
    if fp_idx is None:
        return None
    first_pitch = pa_records[fp_idx]
    if fp_idx == len(pa_records) - 1:
        return not first_pitch.get("hbp")
    next_pitch = pa_records[fp_idx + 1]
    if (next_pitch.get("strikes") or 0) > (first_pitch.get("strikes") or 0):
        return True
    if (next_pitch.get("balls") or 0) > (first_pitch.get("balls") or 0):
        return False
    return None


def _extract_plate_appearances(
    game_plays: dict[str, Any], ps_id_by_source: dict[int, int], game_id: int
) -> list[dict[str, Any]]:
    """One row per completed plate appearance — a maximal run of a half-
    inning's plays (in `playorder`) up to and including the first record
    with `pa` set, skipping `nopitch` placeholder records (e.g. "Play Ball"
    at game start). Feeds batter pull/spray tendency, batter-vs-pitcher
    matchups, and pitcher first-pitch-strike% (see stats/spray.py,
    stats/matchups.py, stats/aggregation.py). Rows whose batter can't be
    resolved are dropped; `pitcher_player_season_id` may be None (see
    db/models.py:PlateAppearance)."""
    rows: list[dict[str, Any]] = []
    if not isinstance(game_plays, dict):
        return rows
    for halves in game_plays.values():
        if not isinstance(halves, dict):
            continue
        for half_name, plays in halves.items():
            if not isinstance(plays, list) or not plays:
                continue
            buffer: list[dict[str, Any]] = []
            for rec in sorted(plays, key=lambda p: p.get("playorder") or 0):
                if rec.get("nopitch"):
                    continue
                buffer.append(rec)
                if not rec.get("pa"):
                    continue
                terminal = buffer[-1]
                batter_ps_id = ps_id_by_source.get(terminal.get("batterid"))
                if batter_ps_id is not None:
                    is_ball_in_play = (
                        terminal.get("ab") == 1
                        and not terminal.get("strikeout")
                        and not terminal.get("bb")
                        and not terminal.get("hbp")
                    )
                    rows.append(
                        {
                            "source_play_id": terminal["id"],
                            "game_id": game_id,
                            "inning": terminal.get("inning"),
                            "half": half_name,
                            "batter_player_season_id": batter_ps_id,
                            "pitcher_player_season_id": ps_id_by_source.get(terminal.get("pitcherid")),
                            "ab": int(terminal.get("ab") or 0),
                            "h": int(terminal.get("h") or 0),
                            "doubles": int(terminal.get("double") or 0),
                            "triples": int(terminal.get("triple") or 0),
                            "hr": int(terminal.get("homerun") or 0),
                            "bb": int(terminal.get("bb") or 0),
                            "ibb": int(terminal.get("ibb") or 0),
                            "hbp": int(terminal.get("hbp") or 0),
                            "so": int(terminal.get("strikeout") or 0),
                            "sf": int(terminal.get("sf") or 0),
                            "rbi": int(terminal.get("rbi") or 0),
                            "first_pitch_strike": _first_pitch_strike(buffer),
                            "hitpull": terminal.get("hitpull") if is_ball_in_play else None,
                            "hitdistance": terminal.get("hitdistance") if is_ball_in_play else None,
                            "hittype": terminal.get("hittype") if is_ball_in_play else None,
                        }
                    )
                buffer = []
    return rows


def _extract_narrative_errors(
    game_plays: dict[str, Any],
    home_source_team_id: int | None,
    away_source_team_id: int | None,
) -> dict[int, Counter]:
    """Returns {source_team_id: Counter({position: errors})}, read off the
    play-by-play narrative's scorer notation ("... reaches on fielding error.
    E6.").

    Which team was fielding is structural rather than read off a flag: the
    top of an inning is always the away team batting, so the home team is
    fielding — the same rule _extract_lob relies on.

    This is deliberately *not* used as the source of error counts: narrative
    tokens miss errors on stolen-base throws and runner advancement, and
    disagree with the box score's own totals in roughly half of team-games
    (see docs/fielding_metrics_plan.md). It is only ever used to decide
    *which position* an already-counted error belongs to.
    """
    errors: dict[int, Counter] = {}
    if not isinstance(game_plays, dict):
        return errors
    seen_play_ids: set[Any] = set()
    for halves in game_plays.values():
        if not isinstance(halves, dict):
            continue
        for half_name, plays in halves.items():
            if not isinstance(plays, list):
                continue
            fielding_team = home_source_team_id if half_name == "top" else away_source_team_id
            if fielding_team is None:
                continue
            for play in plays:
                play_id = play.get("id")
                if play_id is not None:
                    if play_id in seen_play_ids:
                        continue
                    seen_play_ids.add(play_id)
                for number in _ERROR_TOKEN.findall(play.get("narrative") or ""):
                    errors.setdefault(fielding_team, Counter())[_POSITION_BY_NUMBER[number]] += 1
    return errors


def _extract_fielding_lines(
    by_player: dict[int, list[dict[str, Any]]],
    ps_id_by_player: dict[int, int],
    team_season_id_by_player: dict[int, int],
    narrative_errors: dict[int, Counter],
    game_id: int,
) -> list[dict[str, Any]]:
    """Break each player-game's fielding totals down by position, one row per
    (player, position) — see db/models.py:FieldingGameLine.

    The box score's `pos` is a slash-joined path of the positions a player
    occupied during one stint ("SS/P", "2B/P/P"), while that record's fielding
    counts are a single total, so a multi-position record's errors belong to
    no one position on the record's own evidence. Those are split using the
    game's narrative E<n> tokens for the same fielding team, restricted to the
    positions that record actually names; PO/A/DP go to the first position
    named (where the stint started), which is the one approximation here.
    Errors the narrative can't place are filed under UNKNOWN_POSITION rather
    than guessed at.

    Summing the returned rows per player reproduces that player's box-score
    fielding totals exactly, so the app never shows a per-position breakdown
    that contradicts the team/player totals the site itself publishes.
    """
    # Narrative tokens are consumed as they are used, so two players who each
    # spent part of a game at the same position can't both claim the same
    # error. Single-position records are settled first — their attribution is
    # already exact, and retiring their tokens up front stops an ambiguous
    # record from drawing on an error that demonstrably belongs elsewhere.
    remaining = {team: counter.copy() for team, counter in narrative_errors.items()}
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    ambiguous: list[tuple[int, int, Any, list[str], int]] = []

    def row_for(ps_id: int, team_season_id: int, position: str) -> dict[str, Any]:
        key = (ps_id, position)
        if key not in rows:
            rows[key] = {
                "game_id": game_id,
                "player_season_id": ps_id,
                "team_season_id": team_season_id,
                "position": position,
                "appearances": 0,
                "po": 0,
                "a": 0,
                "e": 0,
                "dp": 0,
            }
        return rows[key]

    for source_player_id, records in by_player.items():
        ps_id = ps_id_by_player.get(source_player_id)
        team_season_id = team_season_id_by_player.get(source_player_id)
        if ps_id is None or team_season_id is None:
            continue
        source_team_id = records[-1].get("teamid")

        for rec in records:
            po = int(rec.get("field_po") or 0)
            assists = int(rec.get("field_a") or 0)
            errors = int(rec.get("field_e") or 0)
            dp = int(rec.get("field_dp") or 0)
            # dict.fromkeys rather than set(): "P/SS/P" is one stint back at
            # the same position, and the *order* is what identifies where it
            # started, so this has to stay order-preserving.
            positions = list(dict.fromkeys(p for p in (rec.get("pos") or "").split("/") if p))
            if not positions and not (po or assists or errors or dp):
                continue

            if len(positions) <= 1:
                position = positions[0] if positions else UNKNOWN_POSITION
                row = row_for(ps_id, team_season_id, position)
                row["appearances"] += 1
                row["po"] += po
                row["a"] += assists
                row["e"] += errors
                row["dp"] += dp
                counter = remaining.get(source_team_id)
                if errors and counter is not None:
                    counter[position] -= min(errors, counter[position])
                continue

            first = row_for(ps_id, team_season_id, positions[0])
            first["po"] += po
            first["a"] += assists
            first["dp"] += dp
            for position in positions:
                row_for(ps_id, team_season_id, position)["appearances"] += 1
            if errors:
                ambiguous.append((ps_id, team_season_id, source_team_id, positions, errors))

    for ps_id, team_season_id, source_team_id, positions, errors in ambiguous:
        counter = remaining.get(source_team_id)
        for position in positions:
            if errors <= 0:
                break
            available = counter[position] if counter is not None else 0
            take = min(errors, available)
            if take:
                counter[position] -= take
                row_for(ps_id, team_season_id, position)["e"] += take
                errors -= take
        if errors:
            row_for(ps_id, team_season_id, UNKNOWN_POSITION)["e"] += errors

    return list(rows.values())


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
    game_plays = (original.get("gamePlays") or {}).get("all") or {}

    game = session.query(Game).filter_by(source_id=game_source_id).one()
    team_season_by_source_id = {
        ts.source_team_id: ts.id
        for ts in session.query(TeamSeason).filter(
            TeamSeason.id.in_([game.home_team_season_id, game.away_team_season_id])
        )
    }

    by_player = _flatten_and_group(box_score)

    # Pass 1: upsert Player/PlayerSeason for everyone who appears, so RISP
    # totals (keyed by player_season_id) can be computed before the single
    # BattingGameLine upsert per player below — folding risp_ab/risp_h into
    # that same upsert call avoids a second upsert on the same row, which
    # would silently zero out the fields the first call didn't set (see
    # db/upsert.py: ON CONFLICT DO UPDATE sets every column from `values`,
    # falling back to each column's default for anything omitted).
    player_season_id_by_player: dict[int, int] = {}
    team_season_id_by_player: dict[int, int] = {}
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
        player_season_id_by_player[source_player_id] = player_season_id
        team_season_id_by_player[source_player_id] = team_season_id

    risp_totals = _extract_risp_totals(game_plays, player_season_id_by_player)
    home_lob, away_lob = _extract_lob(game_plays)
    game.home_lob = home_lob
    game.away_lob = away_lob

    source_team_id_by_team_season = {ts_id: src for src, ts_id in team_season_by_source_id.items()}
    narrative_errors = _extract_narrative_errors(
        game_plays,
        source_team_id_by_team_season.get(game.home_team_season_id),
        source_team_id_by_team_season.get(game.away_team_season_id),
    )
    # Cleared and rebuilt rather than upserted: which (player, position) rows
    # a game produces depends on the attribution rule above, so a re-run after
    # that rule changes would otherwise strand rows it no longer generates
    # (e.g. an "UNK" row left behind once the narrative can place that error).
    session.query(FieldingGameLine).filter_by(game_id=game.id).delete(synchronize_session=False)
    for fielding_row in _extract_fielding_lines(
        by_player,
        player_season_id_by_player,
        team_season_id_by_player,
        narrative_errors,
        game.id,
    ):
        upsert(session, FieldingGameLine, fielding_row, ["game_id", "player_season_id", "position"])

    for pa_row in _extract_plate_appearances(game_plays, player_season_id_by_player, game.id):
        upsert(session, PlateAppearance, pa_row, ["source_play_id"])

    # Pass 2: sum each player's per-record fields and upsert one
    # batting_game_lines / pitching_game_lines row per player.
    for source_player_id, records in by_player.items():
        player_season_id = player_season_id_by_player.get(source_player_id)
        team_season_id = team_season_id_by_player.get(source_player_id)
        if player_season_id is None or team_season_id is None:
            continue
        last = records[-1]

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
            risp = risp_totals.get(player_season_id, {"risp_ab": 0, "risp_h": 0})
            upsert(
                session,
                BattingGameLine,
                {
                    "game_id": game.id,
                    "player_season_id": player_season_id,
                    "team_season_id": team_season_id,
                    "position": last.get("pos"),
                    **batting_totals,
                    **risp,
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
