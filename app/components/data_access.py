"""Cached DB-query functions returning pandas DataFrames for the Streamlit
pages. Reuses stats/ formulas rather than recomputing them, so the UI layer
never duplicates sabermetric logic — it only displays what stats/ derived."""

import pandas as pd
import streamlit as st
from sqlalchemy import select

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


@st.cache_data
def player_career(full_name: str) -> pd.DataFrame:
    session = get_session()
    try:
        rows = session.execute(
            select(
                Season.year,
                League.code,
                TeamSeason.display_name,
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
            .where(Player.full_name == full_name)
            .order_by(Season.year)
        ).all()

        records = []
        for year, league_code, team_name, stats_row, war in rows:
            rate = batting_rate_stats(stats_row)
            records.append(
                {
                    "year": year,
                    "league": league_code,
                    "team": team_name,
                    "pa": stats_row.pa,
                    "hr": stats_row.hr,
                    **rate,
                    "war": war,
                }
            )
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def all_player_names() -> list[str]:
    session = get_session()
    try:
        return sorted({row[0] for row in session.execute(select(Player.full_name))})
    finally:
        session.close()
