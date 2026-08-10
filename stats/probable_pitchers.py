"""Opponent pitching-staff usage and probable-starter inference for the
scouting report (app/pages/8_Scouting_Report.py).

The site publishes no rotation or probable-pitcher information, so "who are
we likely to face" has to be inferred from what the staff has actually done.
Two building blocks:

- **Starter identification.** The starter of a game is the pitcher of the
  team's first defensive plate appearance in the `PlateAppearance` feed
  (ordered by inning, then `source_play_id` — play ids increase through a
  game). Games with no play-by-play at all fall back to "the team's
  `PitchingGameLine` with the most outs recorded", flagged lower-confidence
  via `starter_source`.

- **Likelihood ranking.** Each identified start is weighted by an
  exponential recency decay (half-life `RECENCY_HALF_LIFE_DAYS`) measured
  from the team's *own most recent final game* — not wall-clock today, so
  historical seasons rank sensibly (same convention as
  `data_access.team_recent_games`). A pitcher's score is the sum of their
  start weights; ranking by score naturally favors "started most, most
  recently". BBF weekends are usually doubleheaders, so the caller should
  present a top-2/top-3, never a single hard prediction — the qualitative
  `confidence` on each row exists to reinforce that framing.

Like stats/archetypes.py this is computed at read time, not materialized:
it's cheap (one team's season), and the ranking depends on "as of when"
in a way a materialized row wouldn't capture.
"""

import datetime as dt
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Game, PitchingGameLine, PlateAppearance

RECENCY_HALF_LIFE_DAYS = 14.0

# Score-share thresholds for the qualitative confidence label. A staff ace
# who starts every week converges to a share near 1/(starts per weekend);
# 0.35 therefore means "clearly the most-used current starter", while
# anything under 0.15 is an occasional starter at best.
CONFIDENCE_HIGH_SHARE = 0.35
CONFIDENCE_MEDIUM_SHARE = 0.15
MIN_STARTS_FOR_HIGH = 3


def _team_final_games(session: Session, team_season_id: int) -> list[Game]:
    return list(
        session.execute(
            select(Game)
            .where(
                Game.status == "final",
                (Game.home_team_season_id == team_season_id) | (Game.away_team_season_id == team_season_id),
            )
            .order_by(Game.game_date)
        )
        .scalars()
        .all()
    )


def identify_starter(session: Session, game: Game, team_season_id: int) -> tuple[int | None, str]:
    """(pitcher player_season_id, source) for one team's starter in one game.

    source is "play_by_play" when read from the first defensive PA,
    "max_outs" for the no-play-by-play fallback, "unknown" when the game has
    no pitching data at all for this team.
    """
    defensive_half = "top" if game.home_team_season_id == team_season_id else "bottom"
    first_pa_pitcher = session.execute(
        select(PlateAppearance.pitcher_player_season_id)
        .where(
            PlateAppearance.game_id == game.id,
            PlateAppearance.half == defensive_half,
            PlateAppearance.pitcher_player_season_id.is_not(None),
        )
        .order_by(PlateAppearance.inning, PlateAppearance.source_play_id)
        .limit(1)
    ).scalar_one_or_none()
    if first_pa_pitcher is not None:
        return first_pa_pitcher, "play_by_play"

    max_outs_pitcher = session.execute(
        select(PitchingGameLine.player_season_id)
        .where(
            PitchingGameLine.game_id == game.id,
            PitchingGameLine.team_season_id == team_season_id,
        )
        .order_by(PitchingGameLine.outs_recorded.desc(), PitchingGameLine.id)
        .limit(1)
    ).scalar_one_or_none()
    if max_outs_pitcher is not None:
        return max_outs_pitcher, "max_outs"
    return None, "unknown"


def _recency_weight(game_date: dt.date | None, reference_date: dt.date) -> float:
    if game_date is None:
        return 0.0
    days_ago = max((reference_date - game_date).days, 0)
    return 0.5 ** (days_ago / RECENCY_HALF_LIFE_DAYS)


def staff_usage(session: Session, team_season_id: int) -> list[dict]:
    """One dict per pitcher who has thrown for this team_season, ranked by
    probable-starter score (descending), relievers included (score 0 sorts
    them to the bottom by innings). Keys:

    player_season_id, g, gs, gs_play_by_play (starts identified from real
    play-by-play rather than the max-outs fallback), outs, team_ip_share,
    saves, first_date, last_date, start_dates, score, score_share,
    confidence ("High"/"Medium"/"Low"), recent_outs_by_date (per-game outs
    for the team's last 4 distinct game dates, for the usage grid).
    """
    games = _team_final_games(session, team_season_id)
    lines = list(
        session.execute(
            select(PitchingGameLine, Game.game_date)
            .join(Game, Game.id == PitchingGameLine.game_id)
            .where(PitchingGameLine.team_season_id == team_season_id, Game.status == "final")
        ).all()
    )
    if not lines:
        return []

    dates = [d for _, d in lines if d is not None]
    reference_date = max(dates) if dates else dt.date.today()
    recent_dates = sorted({d for d in dates}, reverse=True)[:4]

    per_pitcher: dict[int, dict] = defaultdict(
        lambda: {
            "g": 0,
            "gs": 0,
            "gs_play_by_play": 0,
            "outs": 0,
            "saves": 0,
            "first_date": None,
            "last_date": None,
            "start_dates": [],
            "score": 0.0,
            "recent_outs_by_date": {},
        }
    )
    for line, game_date in lines:
        row = per_pitcher[line.player_season_id]
        row["g"] += 1
        row["outs"] += line.outs_recorded
        row["saves"] += 1 if line.save else 0
        if game_date is not None:
            row["first_date"] = min(row["first_date"] or game_date, game_date)
            row["last_date"] = max(row["last_date"] or game_date, game_date)
            if game_date in recent_dates:
                row["recent_outs_by_date"][game_date] = (
                    row["recent_outs_by_date"].get(game_date, 0) + line.outs_recorded
                )

    for game in games:
        starter, source = identify_starter(session, game, team_season_id)
        if starter is None or starter not in per_pitcher:
            continue
        row = per_pitcher[starter]
        row["gs"] += 1
        if source == "play_by_play":
            row["gs_play_by_play"] += 1
        if game.game_date is not None:
            row["start_dates"].append(game.game_date)
        row["score"] += _recency_weight(game.game_date, reference_date)

    team_outs = sum(row["outs"] for row in per_pitcher.values())
    total_score = sum(row["score"] for row in per_pitcher.values())
    result = []
    for player_season_id, row in per_pitcher.items():
        share = (row["score"] / total_score) if total_score else 0.0
        if share >= CONFIDENCE_HIGH_SHARE and row["gs"] >= MIN_STARTS_FOR_HIGH:
            confidence = "High"
        elif share >= CONFIDENCE_MEDIUM_SHARE:
            confidence = "Medium"
        else:
            confidence = "Low"
        result.append(
            {
                "player_season_id": player_season_id,
                **row,
                "start_dates": sorted(row["start_dates"]),
                "team_ip_share": (row["outs"] / team_outs) if team_outs else 0.0,
                "score_share": share,
                "confidence": confidence,
            }
        )
    result.sort(key=lambda r: (-r["score"], -r["outs"], r["player_season_id"]))
    return result


def probable_starters(session: Session, team_season_id: int, top_n: int = 3) -> list[dict]:
    """The staff_usage rows most likely to start the next game, with a
    human-readable `evidence` string added. Only pitchers with at least one
    identified start qualify."""
    usage = [row for row in staff_usage(session, team_season_id) if row["gs"] > 0]
    top = usage[:top_n]
    for row in top:
        latest = max(row["start_dates"]) if row["start_dates"] else None
        recency = f", most recently {latest.strftime('%d %b')}" if latest else ""
        fallback_note = "" if row["gs_play_by_play"] == row["gs"] else " (some starts inferred from innings totals)"
        row["evidence"] = f"{row['gs']} start{'s' if row['gs'] != 1 else ''} this season{recency}{fallback_note}"
    return top
