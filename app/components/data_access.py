"""Cached DB-query functions returning pandas DataFrames for the Streamlit
pages. Reuses stats/ formulas rather than recomputing them, so the UI layer
never duplicates sabermetric logic — it only displays what stats/ derived."""

import pandas as pd
import streamlit as st
from sqlalchemy import or_, select

from db.engine import get_session
from db.models import (
    BattingSeasonStats,
    BattingWar,
    Game,
    League,
    LeagueSeason,
    LeagueSeasonContext,
    PitchingSeasonStats,
    PitchingWar,
    Player,
    PlayerSeason,
    Season,
    Team,
    TeamSeason,
)
from stats.advanced_stats import era_plus, wrc_plus
from stats.rate_stats import batting_rate_stats, pitching_rate_stats


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
            .order_by(Season.year.desc(), League.code)
        ).all()
        return pd.DataFrame(rows, columns=["league_season_id", "league_code", "league_name", "year", "competition_slug"])
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


def _batting_career_rows(session, names: list[str]) -> list[dict]:
    rows = session.execute(
        select(
            Season.year,
            League.code,
            TeamSeason.display_name,
            Player.full_name,
            BattingSeasonStats,
            BattingWar.war,
        )
        .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
        .join(Player, Player.id == PlayerSeason.player_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .join(LeagueSeason, LeagueSeason.id == TeamSeason.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .join(Season, Season.id == LeagueSeason.season_id)
        .outerjoin(BattingWar, BattingWar.player_season_id == BattingSeasonStats.player_season_id)
        .where(Player.full_name.in_(names))
        .order_by(Player.full_name, Season.year)
    ).all()

    records = []
    for year, league_code, team_name, full_name, stats_row, war in rows:
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
                "war": war,
            }
        )
    return records


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
        )
        .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
        .join(Player, Player.id == PlayerSeason.player_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .join(LeagueSeason, LeagueSeason.id == TeamSeason.league_season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .join(Season, Season.id == LeagueSeason.season_id)
        .outerjoin(PitchingWar, PitchingWar.player_season_id == PitchingSeasonStats.player_season_id)
        .where(Player.full_name.in_(names))
        .order_by(Player.full_name, Season.year)
    ).all()

    records = []
    for year, league_code, team_name, full_name, stats_row, war, player_fip in rows:
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
                "fip": player_fip,
                "war": war,
            }
        )
    return records


@st.cache_data
def player_career(full_name: str) -> pd.DataFrame:
    session = get_session()
    try:
        df = pd.DataFrame(_batting_career_rows(session, [full_name]))
        return df.drop(columns=["player"]) if not df.empty else df
    finally:
        session.close()


@st.cache_data
def player_batting_comparison(names: list[str]) -> pd.DataFrame:
    session = get_session()
    try:
        return pd.DataFrame(_batting_career_rows(session, sorted(names)))
    finally:
        session.close()


@st.cache_data
def player_pitching_comparison(names: list[str]) -> pd.DataFrame:
    session = get_session()
    try:
        return pd.DataFrame(_pitching_career_rows(session, sorted(names)))
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
