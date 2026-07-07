"""Cached DB-query functions returning pandas DataFrames for the Streamlit
pages. Reuses stats/ formulas rather than recomputing them, so the UI layer
never duplicates sabermetric logic — it only displays what stats/ derived."""

from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased

from db.engine import get_session
from db.models import (
    BatterPitcherMatchup,
    BatterSpraySeasonStats,
    BattingSeasonStats,
    BattingWar,
    Game,
    League,
    LeagueSeason,
    LeagueSeasonContext,
    PitchingSeasonStats,
    PitchingWar,
    PlateAppearance,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from stats.advanced_stats import era_plus, fip, wrc_plus
from stats.advanced_stats import woba as compute_woba
from stats.rate_stats import avg, avg_risp, batting_rate_stats, fielding_pct, fps_pct, outs_to_ip_display, pitching_rate_stats

# Tier order for every league dropdown in the app — top division to
# bottom, not alphabetical (alphabetical would put d2 before nbl). Any
# future/unlisted code sorts after these five rather than erroring.
_LEAGUE_TIER_RANK = case({"nbl": 0, "d2": 1, "d3": 2, "d4": 3, "d5": 4}, value=League.code, else_=99)


@st.cache_data
def list_league_seasons() -> pd.DataFrame:
    session = get_session()
    try:
        rows = session.execute(
            select(
                LeagueSeason.id.label("league_season_id"),
                League.code.label("league_code"),
                League.name.label("league_name"),
                Season.year,
                LeagueSeason.competition_slug,
            )
            .join(League, League.id == LeagueSeason.league_id)
            .join(Season, Season.id == LeagueSeason.season_id)
            .order_by(Season.year.desc(), _LEAGUE_TIER_RANK)
        ).all()
        return pd.DataFrame(rows, columns=["league_season_id", "league_code", "league_name", "year", "competition_slug"])
    finally:
        session.close()


@st.cache_data
def player_league_seasons(full_name: str) -> pd.DataFrame:
    """Every league_season this player has a PlayerSeason row in — lets the
    Player Page offer a season-scoped view (tendency, spray chart, matchups)
    distinct from the always-career-combined batting/pitching tables, without
    the ambiguity of parsing those tables' comma-joined multi-league display
    strings back into a specific league_season_id."""
    session = get_session()
    try:
        rows = session.execute(
            select(LeagueSeason.id.label("league_season_id"), Season.year, League.code.label("league"))
            .join(TeamSeason, TeamSeason.league_season_id == LeagueSeason.id)
            .join(PlayerSeason, PlayerSeason.team_season_id == TeamSeason.id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(Season, Season.id == LeagueSeason.season_id)
            .join(League, League.id == LeagueSeason.league_id)
            .where(Player.full_name == full_name)
            .order_by(Season.year.desc(), _LEAGUE_TIER_RANK)
        ).all()
        return pd.DataFrame(rows, columns=["league_season_id", "year", "league"])
    finally:
        session.close()


def _lg_context(session, league_season_id: int):
    return session.execute(
        select(LeagueSeasonContext).where(LeagueSeasonContext.league_season_id == league_season_id)
    ).scalar_one_or_none()


@st.cache_data
def batting_leaderboard(league_season_id: int, min_pa: int = 0) -> pd.DataFrame:
    session = get_session()
    try:
        ctx = _lg_context(session, league_season_id)
        lg_woba = ctx.lg_woba if ctx else None

        rows = session.execute(
            select(BattingSeasonStats, Player.full_name, TeamSeason.display_name, BattingWar.war, BattingWar.woba)
            .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .outerjoin(BattingWar, BattingWar.player_season_id == BattingSeasonStats.player_season_id)
            .where(TeamSeason.league_season_id == league_season_id, BattingSeasonStats.pa >= min_pa)
        ).all()

        records = []
        for stats_row, full_name, team_name, war, player_woba in rows:
            rate = batting_rate_stats(stats_row)
            records.append(
                {
                    "player": full_name,
                    "team": team_name,
                    "pa": stats_row.pa,
                    "ab": stats_row.ab,
                    "h": stats_row.h,
                    "doubles": stats_row.doubles,
                    "triples": stats_row.triples,
                    "hr": stats_row.hr,
                    "rbi": stats_row.rbi,
                    "bb": stats_row.bb,
                    "so": stats_row.so,
                    "sb": stats_row.sb,
                    **rate,
                    "woba": player_woba,
                    "wrc_plus": wrc_plus(player_woba, lg_woba),
                    "war": war,
                    "po": stats_row.field_po,
                    "a": stats_row.field_a,
                    "e": stats_row.field_e,
                    "dp": stats_row.field_dp,
                    "fpct": fielding_pct(stats_row.field_po, stats_row.field_a, stats_row.field_e),
                    "avg_risp": avg_risp(stats_row.risp_h, stats_row.risp_ab),
                }
            )
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def pitching_leaderboard(league_season_id: int, min_ip: float = 0) -> pd.DataFrame:
    session = get_session()
    try:
        ctx = _lg_context(session, league_season_id)
        lg_era = ctx.lg_era if ctx else None

        rows = session.execute(
            select(PitchingSeasonStats, Player.full_name, TeamSeason.display_name, PitchingWar.war, PitchingWar.fip)
            .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .outerjoin(PitchingWar, PitchingWar.player_season_id == PitchingSeasonStats.player_season_id)
            .where(TeamSeason.league_season_id == league_season_id)
        ).all()

        records = []
        for stats_row, full_name, team_name, war, player_fip in rows:
            rate = pitching_rate_stats(stats_row)
            if rate["ip"] < min_ip:
                continue
            records.append(
                {
                    "player": full_name,
                    "team": team_name,
                    "w": stats_row.wins,
                    "l": stats_row.losses,
                    "sv": stats_row.saves,
                    "so": stats_row.so,
                    "bb": stats_row.bb,
                    "h": stats_row.h,
                    "er": stats_row.er,
                    **rate,
                    "ip": outs_to_ip_display(stats_row.outs_recorded),
                    "fps_pct": fps_pct(stats_row.fps_strikes, stats_row.fps_pa),
                    "fip": player_fip,
                    "era_plus": era_plus(rate["era"], lg_era),
                    "war": war,
                }
            )
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def team_roster(league_season_id: int) -> pd.DataFrame:
    session = get_session()
    try:
        rows = session.execute(
            select(TeamSeason.display_name, Player.full_name, PlayerSeason.position_primary, PlayerSeason.jersey_number)
            .join(PlayerSeason, PlayerSeason.team_season_id == TeamSeason.id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(TeamSeason.league_season_id == league_season_id)
            .order_by(TeamSeason.display_name, Player.full_name)
        ).all()
        return pd.DataFrame(rows, columns=["team", "player", "position", "jersey_number"])
    finally:
        session.close()


@st.cache_data
def standings(league_season_id: int) -> pd.DataFrame:
    """Computed from games (source-of-truth facts), not scraped directly."""
    session = get_session()
    try:
        games = session.execute(
            select(Game).where(Game.league_season_id == league_season_id, Game.status == "final")
        ).scalars().all()
        team_names = {
            ts.id: ts.display_name
            for ts in session.execute(select(TeamSeason).where(TeamSeason.league_season_id == league_season_id)).scalars()
        }
        records = {ts_id: {"team": name, "w": 0, "l": 0, "t": 0} for ts_id, name in team_names.items()}
        for g in games:
            if g.home_score is None or g.away_score is None:
                continue
            if g.home_score > g.away_score:
                records[g.home_team_season_id]["w"] += 1
                records[g.away_team_season_id]["l"] += 1
            elif g.away_score > g.home_score:
                records[g.away_team_season_id]["w"] += 1
                records[g.home_team_season_id]["l"] += 1
            else:
                records[g.home_team_season_id]["t"] += 1
                records[g.away_team_season_id]["t"] += 1
        df = pd.DataFrame(records.values())
        if df.empty:
            return df
        df["pct"] = df["w"] / (df["w"] + df["l"] + df["t"]).replace(0, pd.NA)
        return df.sort_values("pct", ascending=False)
    finally:
        session.close()


_TEAM_BATTING_RAW_FIELDS = [
    "ab", "h", "doubles", "triples", "hr", "bb", "ibb", "hbp", "so", "sf",
    "field_po", "field_a", "field_e", "risp_ab", "risp_h", "pa",
]
_TEAM_PITCHING_RAW_FIELDS = ["outs_recorded", "h", "r", "er", "bb", "ibb", "so", "hr", "hbp", "bf"]


@st.cache_data
def team_season_stats(league_season_id: int) -> pd.DataFrame:
    """One row per team competing in this league_season, with team-wide
    aggregate batting/pitching/fielding/situational stats plus win/loss
    record and runs scored/allowed — the head-to-head view on the Team
    Comparison page. Every player on a team_season shares one league_season,
    so unlike the cross-league player-career combine (_combine_batting_year),
    no blending of league-context inputs (lg_woba, fip_constant, lg_era) is
    needed — there's exactly one for the whole roster."""
    session = get_session()
    try:
        ctx = _lg_context(session, league_season_id)
        lg_woba = ctx.lg_woba if ctx else None
        lg_era = ctx.lg_era if ctx else None
        fip_constant = ctx.fip_constant if ctx else None

        team_seasons = session.execute(
            select(TeamSeason.id, TeamSeason.display_name).where(TeamSeason.league_season_id == league_season_id)
        ).all()
        if not team_seasons:
            return pd.DataFrame()

        games = session.execute(
            select(Game).where(Game.league_season_id == league_season_id, Game.status == "final")
        ).scalars().all()
        game_totals = {ts_id: {"w": 0, "l": 0, "t": 0, "rs": 0, "ra": 0, "lob_sum": 0, "lob_games": 0} for ts_id, _ in team_seasons}
        for g in games:
            if g.home_score is None or g.away_score is None:
                continue
            for ts_id, own, opp, lob in (
                (g.home_team_season_id, g.home_score, g.away_score, g.home_lob),
                (g.away_team_season_id, g.away_score, g.home_score, g.away_lob),
            ):
                if ts_id not in game_totals:
                    continue
                gt = game_totals[ts_id]
                gt["rs"] += own
                gt["ra"] += opp
                if own > opp:
                    gt["w"] += 1
                elif own < opp:
                    gt["l"] += 1
                else:
                    gt["t"] += 1
                if lob is not None:
                    gt["lob_sum"] += lob
                    gt["lob_games"] += 1

        records = []
        for ts_id, team_name in team_seasons:
            bat_cols = [func.sum(getattr(BattingSeasonStats, f)).label(f) for f in _TEAM_BATTING_RAW_FIELDS]
            bat_row = session.execute(
                select(*bat_cols)
                .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
                .where(PlayerSeason.team_season_id == ts_id)
            ).one()
            bat_totals = {f: (getattr(bat_row, f) or 0) for f in _TEAM_BATTING_RAW_FIELDS}
            bat_combined = SimpleNamespace(**bat_totals)
            bat_rate = batting_rate_stats(bat_combined)
            team_woba = compute_woba(bat_combined)
            bat_war = session.execute(
                select(func.sum(BattingWar.war))
                .join(PlayerSeason, PlayerSeason.id == BattingWar.player_season_id)
                .where(PlayerSeason.team_season_id == ts_id)
            ).scalar() or 0.0

            pitch_cols = [func.sum(getattr(PitchingSeasonStats, f)).label(f) for f in _TEAM_PITCHING_RAW_FIELDS]
            pitch_row = session.execute(
                select(*pitch_cols)
                .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
                .where(PlayerSeason.team_season_id == ts_id)
            ).one()
            pitch_totals = {f: (getattr(pitch_row, f) or 0) for f in _TEAM_PITCHING_RAW_FIELDS}
            pitch_combined = SimpleNamespace(**pitch_totals)
            pitch_rate = pitching_rate_stats(pitch_combined)
            team_fip = fip(pitch_combined, fip_constant)
            pitch_war = session.execute(
                select(func.sum(PitchingWar.war))
                .join(PlayerSeason, PlayerSeason.id == PitchingWar.player_season_id)
                .where(PlayerSeason.team_season_id == ts_id)
            ).scalar() or 0.0

            gt = game_totals[ts_id]
            gp = gt["w"] + gt["l"] + gt["t"]

            records.append(
                {
                    "team": team_name,
                    "w": gt["w"],
                    "l": gt["l"],
                    "t": gt["t"],
                    "pct": (gt["w"] / gp) if gp else None,
                    "r_pg": (gt["rs"] / gp) if gp else None,
                    "ra_pg": (gt["ra"] / gp) if gp else None,
                    "lob_pg": (gt["lob_sum"] / gt["lob_games"]) if gt["lob_games"] else None,
                    **bat_rate,
                    "woba": team_woba,
                    "wrc_plus": wrc_plus(team_woba, lg_woba),
                    "fpct": fielding_pct(bat_totals["field_po"], bat_totals["field_a"], bat_totals["field_e"]),
                    "avg_risp": avg_risp(bat_totals["risp_h"], bat_totals["risp_ab"]),
                    "era": pitch_rate["era"],
                    "whip": pitch_rate["whip"],
                    "fip": team_fip,
                    "era_plus": era_plus(pitch_rate["era"], lg_era),
                    "war": bat_war + pitch_war,
                }
            )
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def team_recent_games(league_season_id: int, team_name: str, weeks: int = 3) -> pd.DataFrame:
    """One team's final games from the most recent `weeks` weeks of *that
    team's own schedule* in this league_season — not real wall-clock
    "today", since historical seasons have no games anywhere near today.
    Games are played on weekends (see CLAUDE.md), so this reads as "the
    last 3 weekends"."""
    session = get_session()
    try:
        ts_id = session.execute(
            select(TeamSeason.id).where(
                TeamSeason.league_season_id == league_season_id, TeamSeason.display_name == team_name
            )
        ).scalar_one_or_none()
        if ts_id is None:
            return pd.DataFrame()

        games = session.execute(
            select(Game).where(
                Game.league_season_id == league_season_id,
                Game.status == "final",
                or_(Game.home_team_season_id == ts_id, Game.away_team_season_id == ts_id),
            )
        ).scalars().all()
        dated_games = [g for g in games if g.game_date is not None]
        if not dated_games:
            return pd.DataFrame()

        cutoff = max(g.game_date for g in dated_games) - timedelta(weeks=weeks)
        team_names = {
            ts.id: ts.display_name
            for ts in session.execute(select(TeamSeason).where(TeamSeason.league_season_id == league_season_id)).scalars()
        }

        records = []
        for g in dated_games:
            if g.game_date < cutoff:
                continue
            is_home = g.home_team_season_id == ts_id
            own_score = g.home_score if is_home else g.away_score
            opp_score = g.away_score if is_home else g.home_score
            opponent = team_names.get(g.away_team_season_id if is_home else g.home_team_season_id, "?")
            if own_score is None or opp_score is None:
                result, score = None, "-"
            else:
                result = "W" if own_score > opp_score else ("L" if own_score < opp_score else "T")
                score = f"{own_score}-{opp_score}"
            records.append(
                {
                    "game_date": g.game_date,
                    "opponent": opponent,
                    "home_away": "Home" if is_home else "Away",
                    "score": score,
                    "result": result,
                }
            )
        df = pd.DataFrame(records)
        return df.sort_values("game_date", ascending=False) if not df.empty else df
    finally:
        session.close()


_BATTING_RAW_FIELDS = [
    "ab", "h", "doubles", "triples", "hr", "bb", "ibb", "hbp", "so", "sf",
    "sb", "cs", "gdp", "pa", "field_po", "field_a", "field_e", "field_dp",
    "risp_ab", "risp_h",
]
_BATTING_PUBLIC_COLS = [
    "player", "year", "league", "team", "pa", "hr", "avg", "obp", "slg", "ops",
    "iso", "bb_pct", "k_pct", "woba", "wrc_plus", "war", "po", "a", "e", "dp", "fpct", "avg_risp",
]

_PITCHING_RAW_FIELDS = ["outs_recorded", "h", "r", "er", "bb", "ibb", "so", "hr", "hbp", "bf", "fps_pa", "fps_strikes"]
_PITCHING_PUBLIC_COLS = [
    "player", "year", "league", "team", "w", "l", "sv", "so",
    "ip", "era", "whip", "k9", "bb9", "fip", "era_plus", "fps_pct", "war",
]


def _weighted_avg(values_weights: list[tuple[float | None, float]]) -> float | None:
    pairs = [(v, w) for v, w in values_weights if v is not None and w]
    total_w = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total_w if total_w else None


def _select_public(records: list[dict], public_cols: list[str]) -> list[dict]:
    return [{k: r[k] for k in public_cols if k in r} for r in records]


def _batting_career_rows(session, names: list[str]) -> list[dict]:
    rows = session.execute(
        select(
            Season.year,
            League.code,
            TeamSeason.display_name,
            Player.full_name,
            BattingSeasonStats,
            BattingWar.war,
            BattingWar.woba,
            LeagueSeasonContext.lg_woba,
        )
        .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
        .join(Player, Player.id == PlayerSeason.player_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .join(LeagueSeason, LeagueSeason.id == TeamSeason.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .join(Season, Season.id == LeagueSeason.season_id)
        .outerjoin(BattingWar, BattingWar.player_season_id == BattingSeasonStats.player_season_id)
        .outerjoin(LeagueSeasonContext, LeagueSeasonContext.league_season_id == TeamSeason.league_season_id)
        .where(Player.full_name.in_(names))
        .order_by(Player.full_name, Season.year)
    ).all()

    records = []
    for year, league_code, team_name, full_name, stats_row, war, player_woba, lg_woba in rows:
        rate = batting_rate_stats(stats_row)
        records.append(
            {
                "player": full_name,
                "year": year,
                "league": league_code,
                "team": team_name,
                "pa": stats_row.pa,
                "hr": stats_row.hr,
                **rate,
                "woba": player_woba,
                "wrc_plus": wrc_plus(player_woba, lg_woba),
                "war": war,
                "po": stats_row.field_po,
                "a": stats_row.field_a,
                "e": stats_row.field_e,
                "dp": stats_row.field_dp,
                "fpct": fielding_pct(stats_row.field_po, stats_row.field_a, stats_row.field_e),
                "avg_risp": avg_risp(stats_row.risp_h, stats_row.risp_ab),
                "_raw": {f: getattr(stats_row, f) for f in _BATTING_RAW_FIELDS},
                "_lg_woba": lg_woba,
            }
        )
    return records


def _combine_batting_year(rows: list[dict]) -> dict:
    """Combine a player's same-year, multi-team stints into one row: counting
    stats (including fielding) are summed and rate stats recomputed from the
    sums (exact, since wOBA weights are fixed constants, not league-specific
    — see stats/advanced_stats.py). wRC+ blends each stint's league-average
    wOBA, PA-weighted, since stints can span different leagues. WAR is
    summed — each stint's WAR is already relative to its own league-season."""
    if len(rows) == 1:
        return rows[0]

    totals = {f: sum(r["_raw"][f] for r in rows) for f in _BATTING_RAW_FIELDS}
    combined_row = SimpleNamespace(**totals)
    rate = batting_rate_stats(combined_row)
    player_woba = compute_woba(combined_row)
    lg_woba_blend = _weighted_avg([(r["_lg_woba"], r["_raw"]["pa"]) for r in rows])
    wars = [r["war"] for r in rows if r["war"] is not None]

    return {
        "player": rows[0]["player"],
        "year": rows[0]["year"],
        "league": ", ".join(dict.fromkeys(r["league"] for r in rows)),
        "team": ", ".join(dict.fromkeys(r["team"] for r in rows)),
        "pa": totals["pa"],
        "hr": totals["hr"],
        **rate,
        "woba": player_woba,
        "wrc_plus": wrc_plus(player_woba, lg_woba_blend),
        "war": sum(wars) if wars else None,
        "po": totals["field_po"],
        "a": totals["field_a"],
        "e": totals["field_e"],
        "dp": totals["field_dp"],
        "fpct": fielding_pct(totals["field_po"], totals["field_a"], totals["field_e"]),
        "avg_risp": avg_risp(totals["risp_h"], totals["risp_ab"]),
    }


def _pitching_career_rows(session, names: list[str]) -> list[dict]:
    rows = session.execute(
        select(
            Season.year,
            League.code,
            TeamSeason.display_name,
            Player.full_name,
            PitchingSeasonStats,
            PitchingWar.war,
            PitchingWar.fip,
            LeagueSeasonContext.lg_era,
            LeagueSeasonContext.fip_constant,
        )
        .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
        .join(Player, Player.id == PlayerSeason.player_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .join(LeagueSeason, LeagueSeason.id == TeamSeason.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .join(Season, Season.id == LeagueSeason.season_id)
        .outerjoin(PitchingWar, PitchingWar.player_season_id == PitchingSeasonStats.player_season_id)
        .outerjoin(LeagueSeasonContext, LeagueSeasonContext.league_season_id == TeamSeason.league_season_id)
        .where(Player.full_name.in_(names))
        .order_by(Player.full_name, Season.year)
    ).all()

    records = []
    for year, league_code, team_name, full_name, stats_row, war, player_fip, lg_era, fip_constant in rows:
        rate = pitching_rate_stats(stats_row)
        records.append(
            {
                "player": full_name,
                "year": year,
                "league": league_code,
                "team": team_name,
                "w": stats_row.wins,
                "l": stats_row.losses,
                "sv": stats_row.saves,
                "so": stats_row.so,
                **rate,
                "ip": outs_to_ip_display(stats_row.outs_recorded),
                "fps_pct": fps_pct(stats_row.fps_strikes, stats_row.fps_pa),
                "fip": player_fip,
                "era_plus": era_plus(rate["era"], lg_era),
                "war": war,
                "_raw": {f: getattr(stats_row, f) for f in _PITCHING_RAW_FIELDS},
                "_lg_era": lg_era,
                "_fip_constant": fip_constant,
            }
        )
    return records


def _combine_pitching_year(rows: list[dict]) -> dict:
    """Same approach as _combine_batting_year: sum counting stats and
    recompute rate stats exactly; FIP and ERA+ blend their league-context
    inputs (fip_constant, lg_era) IP-weighted across stints; WAR is summed."""
    if len(rows) == 1:
        return rows[0]

    totals = {f: sum(r["_raw"][f] for r in rows) for f in _PITCHING_RAW_FIELDS}
    combined_row = SimpleNamespace(**totals)
    rate = pitching_rate_stats(combined_row)
    fip_constant_blend = _weighted_avg([(r["_fip_constant"], r["_raw"]["outs_recorded"]) for r in rows])
    lg_era_blend = _weighted_avg([(r["_lg_era"], r["_raw"]["outs_recorded"]) for r in rows])
    player_fip = fip(combined_row, fip_constant_blend)
    wars = [r["war"] for r in rows if r["war"] is not None]

    return {
        "player": rows[0]["player"],
        "year": rows[0]["year"],
        "league": ", ".join(dict.fromkeys(r["league"] for r in rows)),
        "team": ", ".join(dict.fromkeys(r["team"] for r in rows)),
        "w": sum(r["w"] for r in rows),
        "l": sum(r["l"] for r in rows),
        "sv": sum(r["sv"] for r in rows),
        "so": totals["so"],
        **rate,
        "ip": outs_to_ip_display(totals["outs_recorded"]),
        "fps_pct": fps_pct(totals["fps_strikes"], totals["fps_pa"]),
        "fip": player_fip,
        "era_plus": era_plus(rate["era"], lg_era_blend),
        "war": sum(wars) if wars else None,
    }


def _combine_by_year(records: list[dict], combine_fn) -> list[dict]:
    by_year: dict[int, list[dict]] = {}
    for r in records:
        by_year.setdefault(r["year"], []).append(r)
    combined = [combine_fn(v) for v in by_year.values()]
    return sorted(combined, key=lambda r: r["year"])


@st.cache_data
def player_batting_career(full_name: str) -> pd.DataFrame:
    session = get_session()
    try:
        rows = _combine_by_year(_batting_career_rows(session, [full_name]), _combine_batting_year)
        df = pd.DataFrame(_select_public(rows, _BATTING_PUBLIC_COLS))
        return df.drop(columns=["player"]) if not df.empty else df
    finally:
        session.close()


@st.cache_data
def player_pitching_career(full_name: str) -> pd.DataFrame:
    session = get_session()
    try:
        rows = _combine_by_year(_pitching_career_rows(session, [full_name]), _combine_pitching_year)
        df = pd.DataFrame(_select_public(rows, _PITCHING_PUBLIC_COLS))
        return df.drop(columns=["player"]) if not df.empty else df
    finally:
        session.close()


@st.cache_data
def player_batting_comparison(names: list[str]) -> pd.DataFrame:
    session = get_session()
    try:
        rows = _batting_career_rows(session, sorted(names))
        return pd.DataFrame(_select_public(rows, _BATTING_PUBLIC_COLS))
    finally:
        session.close()


@st.cache_data
def player_pitching_comparison(names: list[str]) -> pd.DataFrame:
    session = get_session()
    try:
        rows = _pitching_career_rows(session, sorted(names))
        return pd.DataFrame(_select_public(rows, _PITCHING_PUBLIC_COLS))
    finally:
        session.close()


@st.cache_data
def all_player_names() -> list[str]:
    session = get_session()
    try:
        return sorted({row[0] for row in session.execute(select(Player.full_name))})
    finally:
        session.close()


@st.cache_data
def all_team_names() -> list[str]:
    session = get_session()
    try:
        return sorted({row[0] for row in session.execute(select(Team.name))})
    finally:
        session.close()


@st.cache_data
def team_history(names: list[str]) -> pd.DataFrame:
    """One row per team-per-year, aggregated across every league_season that
    team has played in (unlike standings(), which is scoped to one
    league_season) — W/L/T computed from Game rows the same way
    standings() does, per team_season."""
    session = get_session()
    try:
        ts_rows = session.execute(
            select(TeamSeason.id, Team.name, Season.year, League.code)
            .join(Team, Team.id == TeamSeason.team_id)
            .join(LeagueSeason, LeagueSeason.id == TeamSeason.league_season_id)
            .join(Season, Season.id == LeagueSeason.season_id)
            .join(League, League.id == LeagueSeason.league_id)
            .where(Team.name.in_(sorted(names)))
        ).all()
        if not ts_rows:
            return pd.DataFrame()

        ts_ids = [r.id for r in ts_rows]
        games = session.execute(
            select(Game).where(
                Game.status == "final",
                or_(Game.home_team_season_id.in_(ts_ids), Game.away_team_season_id.in_(ts_ids)),
            )
        ).scalars().all()

        wlt = {r.id: {"w": 0, "l": 0, "t": 0} for r in ts_rows}
        for g in games:
            if g.home_score is None or g.away_score is None:
                continue
            for ts_id, own, opp in (
                (g.home_team_season_id, g.home_score, g.away_score),
                (g.away_team_season_id, g.away_score, g.home_score),
            ):
                if ts_id not in wlt:
                    continue
                key = "w" if own > opp else "l" if own < opp else "t"
                wlt[ts_id][key] += 1

        records = []
        for r in ts_rows:
            w, losses, t = wlt[r.id]["w"], wlt[r.id]["l"], wlt[r.id]["t"]
            gp = w + losses + t
            records.append(
                {
                    "team": r.name,
                    "year": r.year,
                    "league": r.code,
                    "w": w,
                    "l": losses,
                    "t": t,
                    "pct": (w / gp) if gp else None,
                }
            )
        return pd.DataFrame(records).sort_values(["team", "year"])
    finally:
        session.close()


@st.cache_data
def batter_tendency(full_name: str, league_season_id: int | None = None) -> dict | None:
    """Pull/center/oppo tendency for one batter — season-scoped if
    league_season_id is given, career (summed across every league_season the
    player has appeared in) otherwise. Returns None if the player has no
    batted-ball data at all (e.g. a switch hitter, excluded entirely by
    stats/spray.py) or hasn't played in the given scope."""
    session = get_session()
    try:
        query = (
            select(BatterSpraySeasonStats)
            .join(PlayerSeason, PlayerSeason.id == BatterSpraySeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(Player.full_name == full_name)
        )
        if league_season_id is not None:
            query = query.join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id).where(
                TeamSeason.league_season_id == league_season_id
            )
        rows = session.execute(query).scalars().all()
        if not rows:
            return None
        counts = {
            "pull": sum(r.pull_count for r in rows),
            "center": sum(r.center_count for r in rows),
            "oppo": sum(r.oppo_count for r in rows),
        }
        if not any(counts.values()):
            return None
        return {**counts, "tendency_label": max(counts, key=counts.get)}
    finally:
        session.close()


def _pa_outcome(pa: PlateAppearance) -> str:
    if pa.hr:
        return "Home Run"
    if pa.triples:
        return "Triple"
    if pa.doubles:
        return "Double"
    if pa.h:
        return "Single"
    return "Out"


@st.cache_data
def batter_spray_points(
    full_name: str, league_season_id: int | None = None, vs_hand: str | None = None
) -> pd.DataFrame:
    """Raw batted-ball rows (hitpull/hitdistance/hittype/outcome) for one
    batter's balls in play — season-scoped if league_season_id is given,
    career otherwise; optionally filtered to opposing pitchers throwing
    `vs_hand` ("L"/"R"). `hitpull` here is the *raw*, unadjusted field
    direction (not handedness-adjusted) — the spray chart's angle must match
    the physical field regardless of batter handedness; only
    batter_tendency()'s classification uses the adjusted value."""
    session = get_session()
    try:
        query = (
            select(PlateAppearance)
            .join(PlayerSeason, PlayerSeason.id == PlateAppearance.batter_player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(Player.full_name == full_name, PlateAppearance.hitpull.is_not(None))
        )
        if league_season_id is not None:
            query = query.join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id).where(
                TeamSeason.league_season_id == league_season_id
            )
        if vs_hand is not None:
            PitcherSeason = aliased(PlayerSeason)
            PitcherPlayer = aliased(Player)
            query = (
                query.join(PitcherSeason, PitcherSeason.id == PlateAppearance.pitcher_player_season_id)
                .join(PitcherPlayer, PitcherPlayer.id == PitcherSeason.player_id)
                .where(PitcherPlayer.throws == vs_hand)
            )
        rows = session.execute(query).scalars().all()
        return pd.DataFrame(
            [
                {
                    "hitpull": r.hitpull,
                    "hitdistance": r.hitdistance,
                    "hittype": r.hittype,
                    "outcome": _pa_outcome(r),
                }
                for r in rows
            ]
        )
    finally:
        session.close()


@st.cache_data
def pitcher_spray_points(
    full_name: str, league_season_id: int | None = None, vs_hand: str | None = None
) -> pd.DataFrame:
    """Mirror of batter_spray_points for the pitching side: balls in play
    allowed by one pitcher, optionally filtered to opposing batters who
    `bats` `vs_hand` ("L"/"R")."""
    session = get_session()
    try:
        query = (
            select(PlateAppearance)
            .join(PlayerSeason, PlayerSeason.id == PlateAppearance.pitcher_player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(Player.full_name == full_name, PlateAppearance.hitpull.is_not(None))
        )
        if league_season_id is not None:
            query = query.join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id).where(
                TeamSeason.league_season_id == league_season_id
            )
        if vs_hand is not None:
            BatterSeason = aliased(PlayerSeason)
            BatterPlayer = aliased(Player)
            query = (
                query.join(BatterSeason, BatterSeason.id == PlateAppearance.batter_player_season_id)
                .join(BatterPlayer, BatterPlayer.id == BatterSeason.player_id)
                .where(BatterPlayer.bats == vs_hand)
            )
        rows = session.execute(query).scalars().all()
        return pd.DataFrame(
            [
                {
                    "hitpull": r.hitpull,
                    "hitdistance": r.hitdistance,
                    "hittype": r.hittype,
                    "outcome": _pa_outcome(r),
                }
                for r in rows
            ]
        )
    finally:
        session.close()


_MATCHUP_COUNT_FIELDS = ["pa", "ab", "h", "doubles", "triples", "hr", "bb", "so", "hbp"]


def _matchup_rows(session, full_name: str, as_batter: bool, league_season_id: int | None):
    """Bidirectional: as_batter=True views `full_name` as the batter (facing
    various pitchers); as_batter=False views them as the pitcher (facing
    various batters)."""
    self_ps_col = BatterPitcherMatchup.batter_player_season_id if as_batter else BatterPitcherMatchup.pitcher_player_season_id
    opp_ps_col = BatterPitcherMatchup.pitcher_player_season_id if as_batter else BatterPitcherMatchup.batter_player_season_id
    OpponentSeason = aliased(PlayerSeason)
    OpponentPlayer = aliased(Player)

    query = (
        select(BatterPitcherMatchup, OpponentPlayer.full_name, Season.year, League.code)
        .join(PlayerSeason, PlayerSeason.id == self_ps_col)
        .join(Player, Player.id == PlayerSeason.player_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .join(LeagueSeason, LeagueSeason.id == TeamSeason.league_season_id)
        .join(Season, Season.id == LeagueSeason.season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .join(OpponentSeason, OpponentSeason.id == opp_ps_col)
        .join(OpponentPlayer, OpponentPlayer.id == OpponentSeason.player_id)
        .where(Player.full_name == full_name)
    )
    if league_season_id is not None:
        query = query.where(TeamSeason.league_season_id == league_season_id)
    return session.execute(query).all()


@st.cache_data
def batter_pitcher_matchups_season(full_name: str, as_batter: bool, league_season_id: int) -> pd.DataFrame:
    """One row per opponent faced within one league_season. No minimum-PA
    filter — sorted by PA descending so the more meaningful samples surface
    first."""
    session = get_session()
    try:
        records = []
        for matchup, opponent_name, _year, _code in _matchup_rows(session, full_name, as_batter, league_season_id):
            rec = {"opponent": opponent_name, **{f: getattr(matchup, f) for f in _MATCHUP_COUNT_FIELDS}}
            rec["avg"] = avg(matchup.h, matchup.ab)
            records.append(rec)
        df = pd.DataFrame(records)
        return df.sort_values("pa", ascending=False) if not df.empty else df
    finally:
        session.close()


@st.cache_data
def batter_pitcher_matchups_career(full_name: str, as_batter: bool) -> pd.DataFrame:
    """One row per opponent faced across every league_season the player has
    appeared in — counting stats summed by the opponent's identity (Player,
    not player_season, so a rematch in a later season combines correctly)."""
    session = get_session()
    try:
        by_opponent: dict[str, dict[str, int]] = {}
        for matchup, opponent_name, _year, _code in _matchup_rows(session, full_name, as_batter, None):
            entry = by_opponent.setdefault(opponent_name, {f: 0 for f in _MATCHUP_COUNT_FIELDS})
            for f in _MATCHUP_COUNT_FIELDS:
                entry[f] += getattr(matchup, f)
        records = [
            {"opponent": name, **totals, "avg": avg(totals["h"], totals["ab"])}
            for name, totals in by_opponent.items()
        ]
        df = pd.DataFrame(records)
        return df.sort_values("pa", ascending=False) if not df.empty else df
    finally:
        session.close()
