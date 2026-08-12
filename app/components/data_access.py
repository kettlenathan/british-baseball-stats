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
    BattingTrueTalent,
    BattingWar,
    Division,
    DivisionContext,
    FieldingSeasonStats,
    Game,
    League,
    LeagueSeason,
    LeagueSeasonContext,
    PitchingSeasonStats,
    PitchingTrueTalent,
    PitchingWar,
    PlateAppearance,
    Player,
    PlayerSeason,
    ScrapeLog,
    Season,
    Team,
    TeamSeason,
    TeamStrength,
)
from stats.advanced_stats import era_plus, fip, wrc_plus
from stats.advanced_stats import woba as compute_woba
from stats.archetypes import fit_archetypes, k_diagnostics
from stats.league_context import _league_batting_totals
from stats.lineup import (
    MIN_PA_FOR_JUDGEMENT,
    build_profile,
    conservative_woba,
    league_component_rates,
    optimize_lineup,
    platoon_adjusted_woba,
    select_starters,
    slot_rationales,
)
from stats.probable_pitchers import probable_starters, staff_usage
from stats.rate_stats import (
    avg,
    avg_risp,
    batting_rate_stats,
    caught_stealing_pct,
    fielding_pct,
    fps_pct,
    hit_type_mix,
    outs_to_ip_display,
    pitching_rate_stats,
)

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
            .where(Player.display_name == full_name)
            .order_by(Season.year.desc(), _LEAGUE_TIER_RANK)
        ).all()
        return pd.DataFrame(rows, columns=["league_season_id", "year", "league"])
    finally:
        session.close()


def _lg_context(session, league_season_id: int):
    return session.execute(
        select(LeagueSeasonContext).where(LeagueSeasonContext.league_season_id == league_season_id)
    ).scalar_one_or_none()


def _division_contexts(session, league_season_id: int) -> dict[int, DivisionContext]:
    """Every division context in this league_season, keyed by division id.

    Fetched in one query and handed around rather than looked up per player:
    a leaderboard row needs its own division's baseline, and per-row lookups
    would issue one query per player.
    """
    return {
        ctx.division_id: ctx
        for ctx in session.execute(
            select(DivisionContext)
            .join(Division, Division.id == DivisionContext.division_id)
            .where(Division.league_season_id == league_season_id)
        ).scalars()
    }


def _division_names(session, league_season_id: int) -> dict[int, str]:
    return {
        row.id: row.name
        for row in session.execute(
            select(Division).where(Division.league_season_id == league_season_id)
        ).scalars()
    }


@st.cache_data
def list_divisions(league_season_id: int) -> pd.DataFrame:
    """The divisions in one league_season, in the site's own published order.

    Returns an empty frame for a league_season with none recorded — 2021's
    NBL genuinely had one undivided table, and callers use "is this empty"
    to decide whether to offer a division control at all rather than
    showing a pointless single-option dropdown.
    """
    session = get_session()
    try:
        rows = session.execute(
            select(
                Division.id.label("division_id"),
                Division.name,
                Division.sort_order,
                func.count(func.distinct(TeamSeason.id)).label("teams"),
            )
            .outerjoin(TeamSeason, TeamSeason.division_id == Division.id)
            .where(Division.league_season_id == league_season_id)
            .group_by(Division.id)
            .order_by(Division.sort_order)
        ).all()
        return pd.DataFrame(rows, columns=["division_id", "division", "sort_order", "teams"])
    finally:
        session.close()


@st.cache_data
def division_environments(league_season_id: int) -> pd.DataFrame:
    """Each division's run environment beside the league-wide one.

    This is the evidence for why the app carries two baselines at all: in
    2026 Division 4 these range from 11.34 runs per team per game down to
    7.52. Presented as a table rather than folded into a single "adjusted"
    number on purpose — the difference between divisions mixes run
    environment with genuine quality, and nothing here can separate the two.
    """
    session = get_session()
    try:
        rows = session.execute(
            select(
                Division.name,
                DivisionContext.games,
                DivisionContext.pa,
                DivisionContext.lg_woba,
                DivisionContext.lg_era,
                DivisionContext.runs_per_pa,
            )
            .join(DivisionContext, DivisionContext.division_id == Division.id)
            .where(Division.league_season_id == league_season_id)
            .order_by(Division.sort_order)
        ).all()
        df = pd.DataFrame(
            rows, columns=["division", "games", "pa", "lg_woba", "lg_era", "runs_per_pa"]
        )
        if df.empty:
            return df

        # Runs per team per game is the readable form of this, and it is what
        # the scoping numbers above quote — derive it from the games actually
        # counted rather than storing a second, redundant column.
        runs = session.execute(
            select(
                Division.name,
                func.sum(Game.home_score + Game.away_score),
                func.count(Game.id),
            )
            .join(Game, Game.division_id == Division.id)
            .where(
                Division.league_season_id == league_season_id,
                Game.status == "final",
                Game.phase == "regular",
                # Forfeits are awarded 7-0 without play; counting them would
                # invent runs this division never scored.
                Game.result_type == "played",
            )
            .group_by(Division.id)
        ).all()
        per_game = {
            name: (total or 0) / (2 * count) if count else None for name, total, count in runs
        }
        df["r_per_team_game"] = df["division"].map(per_game)
        return df[["division", "games", "r_per_team_game", "lg_woba", "lg_era", "pa"]]
    finally:
        session.close()


@st.cache_data
def batting_leaderboard(league_season_id: int, min_pa: int = 0) -> pd.DataFrame:
    """One row per batter, carrying wRC+ against *both* baselines.

    `wrc_plus` compares the hitter to the whole competition; `wrc_plus_div`
    compares them to the division they actually played in. Neither is the
    "true" figure — a hitter in a weak division is flattered by the first and
    penalised by the second, and only showing both makes that visible. The
    division column is included so a page can filter or group without a
    second query.
    """
    session = get_session()
    try:
        ctx = _lg_context(session, league_season_id)
        lg_woba = ctx.lg_woba if ctx else None
        div_contexts = _division_contexts(session, league_season_id)
        div_names = _division_names(session, league_season_id)

        rows = session.execute(
            select(
                BattingSeasonStats,
                Player.display_name,
                TeamSeason.display_name,
                BattingWar.war,
                BattingWar.woba,
                TeamSeason.division_id,
            )
            .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .outerjoin(BattingWar, BattingWar.player_season_id == BattingSeasonStats.player_season_id)
            .where(TeamSeason.league_season_id == league_season_id, BattingSeasonStats.pa >= min_pa)
        ).all()

        records = []
        for stats_row, full_name, team_name, war, player_woba, division_id in rows:
            rate = batting_rate_stats(stats_row)
            div_ctx = div_contexts.get(division_id)
            records.append(
                {
                    "player": full_name,
                    "team": team_name,
                    "division": div_names.get(division_id),
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
                    "wrc_plus_div": wrc_plus(player_woba, div_ctx.lg_woba) if div_ctx else None,
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
        div_contexts = _division_contexts(session, league_season_id)
        div_names = _division_names(session, league_season_id)

        rows = session.execute(
            select(
                PitchingSeasonStats,
                Player.display_name,
                TeamSeason.display_name,
                PitchingWar.war,
                PitchingWar.fip,
                TeamSeason.division_id,
            )
            .join(PlayerSeason, PlayerSeason.id == PitchingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .outerjoin(PitchingWar, PitchingWar.player_season_id == PitchingSeasonStats.player_season_id)
            .where(TeamSeason.league_season_id == league_season_id)
        ).all()

        records = []
        for stats_row, full_name, team_name, war, player_fip, division_id in rows:
            rate = pitching_rate_stats(stats_row)
            if rate["ip"] < min_ip:
                continue
            div_ctx = div_contexts.get(division_id)
            records.append(
                {
                    "player": full_name,
                    "team": team_name,
                    "division": div_names.get(division_id),
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
                    "era_plus_div": era_plus(rate["era"], div_ctx.lg_era) if div_ctx else None,
                    "war": war,
                }
            )
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def batting_true_talent(league_season_id: int, min_pa: int = 0) -> pd.DataFrame:
    """Empirical-Bayes shrunk wOBA per batter for one league_season — see
    stats/shrinkage.py."""
    session = get_session()
    try:
        rows = session.execute(
            select(BattingTrueTalent, Player.display_name, TeamSeason.display_name)
            .join(PlayerSeason, PlayerSeason.id == BattingTrueTalent.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id, BattingTrueTalent.pa >= min_pa)
        ).all()

        records = [
            {
                "player": full_name,
                "team": team_name,
                "pa": row.pa,
                "observed_woba": row.observed_woba,
                "shrunk_woba": row.shrunk_woba,
                "reliability": row.reliability,
                "stabilization_pa": row.stabilization_pa,
                "k_self_calibrated": row.k_self_calibrated,
            }
            for row, full_name, team_name in rows
        ]
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def pitching_true_talent(league_season_id: int, min_ip: float = 0.0) -> pd.DataFrame:
    """Empirical-Bayes shrunk FIP per pitcher for one league_season — see
    stats/shrinkage.py."""
    session = get_session()
    try:
        rows = session.execute(
            select(PitchingTrueTalent, Player.display_name, TeamSeason.display_name)
            .join(PlayerSeason, PlayerSeason.id == PitchingTrueTalent.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id, PitchingTrueTalent.ip >= min_ip)
        ).all()

        records = [
            {
                "player": full_name,
                "team": team_name,
                "ip": row.ip,
                "observed_fip": row.observed_fip,
                "shrunk_fip": row.shrunk_fip,
                "reliability": row.reliability,
                "stabilization_ip": row.stabilization_ip,
                "k_self_calibrated": row.k_self_calibrated,
            }
            for row, full_name, team_name in rows
        ]
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def batter_archetype_inputs(league_season_id: int, min_pa: int = 20) -> pd.DataFrame:
    """Feature vector for batter-archetype clustering (stats/archetypes.py):
    spray tendency (pull/center/oppo %), rate stats (ISO/BB%/K%), and
    extra-base-hit mix (1B/2B/3B/HR%), for every batter in one league_season
    with at least min_pa PA and spray data (excludes switch hitters, same as
    batter_tendency, since there's no per-PA batting-side record to bucket
    them by)."""
    session = get_session()
    try:
        rows = session.execute(
            select(BattingSeasonStats, BatterSpraySeasonStats, Player.display_name, TeamSeason.display_name)
            .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .join(
                BatterSpraySeasonStats,
                BatterSpraySeasonStats.player_season_id == BattingSeasonStats.player_season_id,
            )
            .where(TeamSeason.league_season_id == league_season_id, BattingSeasonStats.pa >= min_pa)
        ).all()

        records = []
        for stats_row, spray_row, full_name, team_name in rows:
            mix = hit_type_mix(stats_row)
            spray_total = spray_row.pull_count + spray_row.center_count + spray_row.oppo_count
            rate = batting_rate_stats(stats_row)
            if mix is None or not spray_total or None in (rate["iso"], rate["bb_pct"], rate["k_pct"]):
                continue
            records.append(
                {
                    "player": full_name,
                    "team": team_name,
                    "pa": stats_row.pa,
                    "pull_pct": spray_row.pull_count / spray_total,
                    "center_pct": spray_row.center_count / spray_total,
                    "oppo_pct": spray_row.oppo_count / spray_total,
                    "iso": rate["iso"],
                    "bb_pct": rate["bb_pct"],
                    "k_pct": rate["k_pct"],
                    **mix,
                }
            )
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def batter_archetypes(league_season_id: int, min_pa: int = 20, k: int | None = None) -> pd.DataFrame:
    """Fits batter archetypes (stats/archetypes.py) over
    batter_archetype_inputs' feature vector — the one place sklearn gets
    invoked from the app layer."""
    df = batter_archetype_inputs(league_season_id, min_pa=min_pa)
    if df.empty:
        return df
    return fit_archetypes(df, k=k)


@st.cache_data
def batter_archetype_k_diagnostics(league_season_id: int, min_pa: int = 20) -> pd.DataFrame:
    """Silhouette/inertia diagnostics across candidate k values, for a "how
    was k chosen" display — see stats/archetypes.py."""
    df = batter_archetype_inputs(league_season_id, min_pa=min_pa)
    if df.empty:
        return df
    return k_diagnostics(df)


@st.cache_data
def team_roster(league_season_id: int) -> pd.DataFrame:
    session = get_session()
    try:
        rows = session.execute(
            select(TeamSeason.display_name, Player.display_name, PlayerSeason.position_primary, PlayerSeason.jersey_number)
            .join(PlayerSeason, PlayerSeason.team_season_id == TeamSeason.id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(TeamSeason.league_season_id == league_season_id)
            .order_by(TeamSeason.display_name, Player.display_name)
        ).all()
        return pd.DataFrame(rows, columns=["team", "player", "position", "jersey_number"])
    finally:
        session.close()


@st.cache_data
def standings(league_season_id: int, regular_season_only: bool = True) -> pd.DataFrame:
    """Computed from games (source-of-truth facts), not scraped directly.

    Carries a `division` column so the page can present the table the way
    the site itself does — one block per division. That grouping is not
    cosmetic: teams in different divisions play disjoint schedules, so
    ranking them in a single list by win percentage compares records built
    against opposition that never overlapped.

    Playoff games are excluded by default, since a league table is a record
    of the regular season and including them would credit a deep playoff run
    as extra league wins.
    """
    session = get_session()
    try:
        query = select(Game).where(
            Game.league_season_id == league_season_id, Game.status == "final"
        )
        if regular_season_only:
            query = query.where(Game.phase == "regular")
        games = session.execute(query).scalars().all()

        divisions = {
            d.id: (d.name, d.sort_order)
            for d in session.execute(
                select(Division).where(Division.league_season_id == league_season_id)
            ).scalars()
        }
        team_seasons = session.execute(
            select(TeamSeason).where(TeamSeason.league_season_id == league_season_id)
        ).scalars().all()
        records = {
            ts.id: {
                "team": ts.display_name,
                "division": divisions.get(ts.division_id, (None, None))[0],
                # Sorted on, then dropped: division blocks must appear in the
                # site's published order, not ordered by whichever division
                # happens to contain the best record. Teams with no division
                # sort last.
                "_division_sort": divisions.get(ts.division_id, (None, 10**6))[1],
                "w": 0,
                "l": 0,
                "t": 0,
            }
            for ts in team_seasons
        }
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

        # Rating and schedule difficulty, where they've been computed. Joined
        # on rather than recomputed: stats/team_strength.py owns the model,
        # this layer only displays it.
        strength = {
            row.team_season_id: row
            for row in session.execute(
                select(TeamStrength)
                .join(TeamSeason, TeamSeason.id == TeamStrength.team_season_id)
                .where(TeamSeason.league_season_id == league_season_id)
            ).scalars()
        }
        if strength and regular_season_only:
            by_team = {
                ts.display_name: strength.get(ts.id)
                for ts in team_seasons
                if ts.id in strength
            }
            df["rating"] = df["team"].map(
                lambda name: by_team[name].rating if by_team.get(name) else None
            )
            df["sos"] = df["team"].map(
                lambda name: by_team[name].sos if by_team.get(name) else None
            )

        return (
            df.sort_values(["_division_sort", "pct"], ascending=[True, False])
            .drop(columns=["_division_sort"])
            .reset_index(drop=True)
        )
    finally:
        session.close()


@st.cache_data
def season_progress(league_season_id: int) -> dict:
    """How far through its published schedule a league-season is.

    The site publishes the whole season's fixtures up front, so "how much is
    left" is known rather than inferred. Ratings and records are only ever a
    snapshot of games played, and mid-season that distinction is the
    difference between "leading the division" and "won the division" —
    2026's leagues are around 85-90% complete as this is written.
    """
    session = get_session()
    try:
        played, remaining = session.execute(
            select(
                func.sum(case((Game.status == "final", 1), else_=0)),
                func.sum(case((Game.status.in_(("scheduled", "postponed")), 1), else_=0)),
            ).where(
                Game.league_season_id == league_season_id,
                Game.phase == "regular",
            )
        ).one()
        played, remaining = played or 0, remaining or 0
        total = played + remaining
        return {
            "played": played,
            "remaining": remaining,
            "total": total,
            "pct_complete": (played / total) if total else None,
            "complete": remaining == 0,
        }
    finally:
        session.close()


@st.cache_data
def team_division(league_season_id: int, team_name: str) -> dict | None:
    """Which division a team played in, and how that division scored.

    Returns None when the league-season records no divisions, or the team
    isn't in one. `rank`/`of` place the team within its own division rather
    than the whole league, which is the only ranking its schedule supports.
    """
    session = get_session()
    try:
        row = session.execute(
            select(Division.name, Division.id, DivisionContext)
            .join(TeamSeason, TeamSeason.division_id == Division.id)
            .outerjoin(DivisionContext, DivisionContext.division_id == Division.id)
            .where(
                TeamSeason.league_season_id == league_season_id,
                TeamSeason.display_name == team_name,
            )
        ).first()
        if row is None:
            return None
        name, _division_id, ctx = row

        table = standings(league_season_id)
        in_division = table[table["division"] == name].reset_index(drop=True)
        position = in_division.index[in_division["team"] == team_name]

        strength = session.execute(
            select(TeamStrength)
            .join(TeamSeason, TeamSeason.id == TeamStrength.team_season_id)
            .where(
                TeamSeason.league_season_id == league_season_id,
                TeamSeason.display_name == team_name,
            )
        ).scalar_one_or_none()

        return {
            "division": name,
            "rank": int(position[0]) + 1 if len(position) else None,
            "of": len(in_division),
            "lg_woba": ctx.lg_woba if ctx else None,
            "lg_era": ctx.lg_era if ctx else None,
            "games": ctx.games if ctx else 0,
            "rating": strength.rating if strength else None,
            "rating_se": strength.rating_se if strength else None,
            "sos": strength.sos if strength else None,
            "expected_win_pct": strength.expected_win_pct if strength else None,
            "games_remaining": strength.games_remaining if strength else 0,
            "sos_remaining": strength.sos_remaining if strength else None,
        }
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
        game_totals = {
            ts_id: {"w": 0, "l": 0, "t": 0, "rs": 0, "ra": 0, "run_games": 0, "lob_sum": 0, "lob_games": 0}
            for ts_id, _ in team_seasons
        }
        for g in games:
            if g.home_score is None or g.away_score is None:
                continue
            # A forfeit is a real win or loss but not real runs, so the
            # record counts it and the run totals don't — otherwise every
            # forfeit would add a phantom 7-0 to runs scored and allowed.
            counts_runs = g.result_type == "played"
            for ts_id, own, opp, lob in (
                (g.home_team_season_id, g.home_score, g.away_score, g.home_lob),
                (g.away_team_season_id, g.away_score, g.home_score, g.away_lob),
            ):
                if ts_id not in game_totals:
                    continue
                gt = game_totals[ts_id]
                if counts_runs:
                    gt["rs"] += own
                    gt["ra"] += opp
                    gt["run_games"] += 1
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
                    # Per *played* game, not per game in the record: the
                    # numerator excludes forfeits, so the denominator must
                    # too or a team with forfeits shows a diluted rate.
                    "r_pg": (gt["rs"] / gt["run_games"]) if gt["run_games"] else None,
                    "ra_pg": (gt["ra"] / gt["run_games"]) if gt["run_games"] else None,
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


# Defensive positions in scorecard order (1-9), then the non-fielding lineup
# slots, then the residue the error-attribution rule couldn't place (see
# scraper/scrape_boxscores.py:_extract_fielding_lines). Sorting by this rather
# than alphabetically is what makes a fielding table read like a scorecard.
_POSITION_ORDER = ["P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "PH", "PR", "UNK"]
_POSITION_RANK = {position: rank for rank, position in enumerate(_POSITION_ORDER)}

# Positions where nobody is actually fielding, so a fielding row for them is
# noise rather than information — dropped from every by-position view. "UNK"
# is deliberately *not* in here: hiding unattributed errors would make the
# per-position numbers silently fail to add up to the team's real total.
_NON_FIELDING_POSITIONS = {"DH", "PH", "PR"}


def _position_sort(df: pd.DataFrame) -> pd.DataFrame:
    """Stable so callers can pre-sort within a position (e.g. by errors) and
    keep that order inside each position group."""
    return (
        df.assign(_rank=df["position"].map(lambda p: _POSITION_RANK.get(p, 99)))
        .sort_values("_rank", kind="stable")
        .drop(columns="_rank")
    )


def _fielding_frame(rows: list[dict]) -> pd.DataFrame:
    """Shared shaping for every by-position fielding view: drop the
    non-fielding lineup slots, add fielding %, and order like a scorecard.

    A position with games but no putouts/assists/errors is kept — an outfielder
    who never had a ball hit to them still played there, and dropping the row
    would misrepresent where the team fielded people.
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[~df["position"].isin(_NON_FIELDING_POSITIONS)]
    if df.empty:
        return df
    df["fpct"] = [fielding_pct(r.po, r.a, r.e) for r in df.itertuples()]
    return _position_sort(df).reset_index(drop=True)


@st.cache_data
def team_fielding_by_position(league_season_id: int, team_name: str) -> pd.DataFrame:
    """One row per position for a team: games, PO/A/E/DP and fielding %.

    Summed at read time from the players' FieldingSeasonStats rows, the same
    way team batting/pitching totals are — there's no team-level fielding
    table. `g` is the number of player-games at the position (two players
    splitting a game at 3B counts twice), which is what makes a per-position
    error rate comparable across positions.
    """
    session = get_session()
    try:
        ts_id = _team_season_id(session, league_season_id, team_name)
        if ts_id is None:
            return pd.DataFrame()
        rows = session.execute(
            select(
                FieldingSeasonStats.position,
                func.sum(FieldingSeasonStats.games).label("g"),
                func.sum(FieldingSeasonStats.po).label("po"),
                func.sum(FieldingSeasonStats.a).label("a"),
                func.sum(FieldingSeasonStats.e).label("e"),
                func.sum(FieldingSeasonStats.dp).label("dp"),
            )
            .join(PlayerSeason, PlayerSeason.id == FieldingSeasonStats.player_season_id)
            .where(PlayerSeason.team_season_id == ts_id)
            .group_by(FieldingSeasonStats.position)
        ).all()
        return _fielding_frame(
            [
                {"position": r.position, "g": r.g or 0, "po": r.po or 0, "a": r.a or 0, "e": r.e or 0, "dp": r.dp or 0}
                for r in rows
            ]
        )
    finally:
        session.close()


@st.cache_data
def team_position_error_players(league_season_id: int, team_name: str) -> pd.DataFrame:
    """Who made a team's errors, one row per (position, player) — the
    follow-up question to team_fielding_by_position's totals. Only players
    with at least one error at that position appear."""
    session = get_session()
    try:
        ts_id = _team_season_id(session, league_season_id, team_name)
        if ts_id is None:
            return pd.DataFrame()
        rows = session.execute(
            select(
                FieldingSeasonStats.position,
                Player.display_name,
                FieldingSeasonStats.games,
                FieldingSeasonStats.po,
                FieldingSeasonStats.a,
                FieldingSeasonStats.e,
            )
            .join(PlayerSeason, PlayerSeason.id == FieldingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(PlayerSeason.team_season_id == ts_id, FieldingSeasonStats.e > 0)
        ).all()
        df = pd.DataFrame(
            [
                {"position": r.position, "player": r.display_name, "g": r.games, "po": r.po, "a": r.a, "e": r.e}
                for r in rows
                if r.position not in _NON_FIELDING_POSITIONS
            ]
        )
        if df.empty:
            return df
        df["fpct"] = [fielding_pct(r.po, r.a, r.e) for r in df.itertuples()]
        # Scorecard position order, and within a position the biggest
        # contributor first — mergesort keeps that inner order stable.
        return _position_sort(df.sort_values("e", ascending=False, kind="stable")).reset_index(drop=True)
    finally:
        session.close()


@st.cache_data
def player_fielding_by_position(full_name: str, league_season_id: int | None = None) -> pd.DataFrame:
    """One row per position a player has fielded, ordered by errors so "which
    position do they make the most errors at" is the first thing read.
    `league_season_id=None` sums their whole career, matching how the Player
    Page's other by-scope sections behave."""
    session = get_session()
    try:
        query = (
            select(
                FieldingSeasonStats.position,
                func.sum(FieldingSeasonStats.games).label("g"),
                func.sum(FieldingSeasonStats.po).label("po"),
                func.sum(FieldingSeasonStats.a).label("a"),
                func.sum(FieldingSeasonStats.e).label("e"),
                func.sum(FieldingSeasonStats.dp).label("dp"),
            )
            .join(PlayerSeason, PlayerSeason.id == FieldingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(Player.display_name == full_name)
            .group_by(FieldingSeasonStats.position)
        )
        if league_season_id is not None:
            query = query.join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id).where(
                TeamSeason.league_season_id == league_season_id
            )
        rows = session.execute(query).all()
        df = _fielding_frame(
            [
                {"position": r.position, "g": r.g or 0, "po": r.po or 0, "a": r.a or 0, "e": r.e or 0, "dp": r.dp or 0}
                for r in rows
            ]
        )
        if df.empty:
            return df
        return df.sort_values(["e", "g"], ascending=False).reset_index(drop=True)
    finally:
        session.close()


_CATCHER_THROWING_COLS = ["sb_against", "cs", "sb_att", "cs_pct", "pb"]


def _catcher_frame(rows: list[dict]) -> pd.DataFrame:
    """Shared shaping for the catcher throwing views: attempts and CS% from
    the raw allowed/caught counts, biggest workload first."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["sb_att"] = df["sb_against"] + df["cs"]
    df["cs_pct"] = [caught_stealing_pct(r.cs, r.sb_against) for r in df.itertuples()]
    df = df[df["sb_att"] > 0]
    if df.empty:
        return df
    return df.sort_values("sb_att", ascending=False).reset_index(drop=True)


@st.cache_data
def team_catcher_throwing(league_season_id: int, team_name: str) -> pd.DataFrame:
    """One row per catcher: stolen bases allowed, runners caught, attempts and
    CS%.

    Only the C rows are counted. This league's scorers charge part of a team's
    steals allowed to the *pitcher* (see FieldingGameLine.sba), so a catcher's
    totals here are deliberately their own share and will read lower than the
    team's total steals allowed.
    """
    session = get_session()
    try:
        ts_id = _team_season_id(session, league_season_id, team_name)
        if ts_id is None:
            return pd.DataFrame()
        rows = session.execute(
            select(
                Player.display_name,
                func.sum(FieldingSeasonStats.games).label("g"),
                func.sum(FieldingSeasonStats.sba).label("sb_against"),
                func.sum(FieldingSeasonStats.csb).label("cs"),
                func.sum(FieldingSeasonStats.pb).label("pb"),
            )
            .join(PlayerSeason, PlayerSeason.id == FieldingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(PlayerSeason.team_season_id == ts_id, FieldingSeasonStats.position == "C")
            .group_by(Player.display_name)
        ).all()
        return _catcher_frame(
            [
                {
                    "player": r.display_name, "g": r.g or 0, "sb_against": r.sb_against or 0,
                    "cs": r.cs or 0, "pb": r.pb or 0,
                }
                for r in rows
            ]
        )
    finally:
        session.close()


@st.cache_data
def player_catcher_throwing(full_name: str, league_season_id: int | None = None) -> pd.DataFrame:
    """One catcher's throwing line, per season or summed across their career
    when `league_season_id` is None — same scoping as the Player Page's other
    sections."""
    session = get_session()
    try:
        query = (
            select(
                func.sum(FieldingSeasonStats.games).label("g"),
                func.sum(FieldingSeasonStats.sba).label("sb_against"),
                func.sum(FieldingSeasonStats.csb).label("cs"),
                func.sum(FieldingSeasonStats.pb).label("pb"),
            )
            .join(PlayerSeason, PlayerSeason.id == FieldingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .where(Player.display_name == full_name, FieldingSeasonStats.position == "C")
        )
        if league_season_id is not None:
            query = query.join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id).where(
                TeamSeason.league_season_id == league_season_id
            )
        row = session.execute(query).one()
        return _catcher_frame(
            [{"player": full_name, "g": row.g or 0, "sb_against": row.sb_against or 0,
              "cs": row.cs or 0, "pb": row.pb or 0}]
        )
    finally:
        session.close()


@st.cache_data
def league_catcher_throwing(league_season_id: int) -> dict | None:
    """League-wide catcher CS% for one league_season — the yardstick a single
    catcher's rate is read against, since throwing runners out is rare enough
    here that a raw count says little on its own."""
    session = get_session()
    try:
        row = session.execute(
            select(
                func.sum(FieldingSeasonStats.sba),
                func.sum(FieldingSeasonStats.csb),
            )
            .join(PlayerSeason, PlayerSeason.id == FieldingSeasonStats.player_season_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id, FieldingSeasonStats.position == "C")
        ).one()
        sb_against, cs = row[0] or 0, row[1] or 0
        if sb_against + cs == 0:
            return None
        return {
            "sb_against": sb_against,
            "cs": cs,
            "sb_att": sb_against + cs,
            "cs_pct": caught_stealing_pct(cs, sb_against),
        }
    finally:
        session.close()


@st.cache_data
def league_fielding_by_position(league_season_id: int) -> pd.DataFrame:
    """League-wide errors per position, plus the per-team average — the
    yardstick for whether a team's 9 errors at shortstop is unusual. Without
    it a raw error count is unreadable: shortstops and third basemen make far
    more errors than corner outfielders everywhere, so the comparison that
    matters is against the same position, not against the team's other ones.
    """
    session = get_session()
    try:
        team_count = session.execute(
            select(func.count(TeamSeason.id)).where(TeamSeason.league_season_id == league_season_id)
        ).scalar() or 0
        rows = session.execute(
            select(
                FieldingSeasonStats.position,
                func.sum(FieldingSeasonStats.games).label("g"),
                func.sum(FieldingSeasonStats.po).label("po"),
                func.sum(FieldingSeasonStats.a).label("a"),
                func.sum(FieldingSeasonStats.e).label("e"),
                func.sum(FieldingSeasonStats.dp).label("dp"),
            )
            .join(PlayerSeason, PlayerSeason.id == FieldingSeasonStats.player_season_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .where(TeamSeason.league_season_id == league_season_id)
            .group_by(FieldingSeasonStats.position)
        ).all()
        df = _fielding_frame(
            [
                {"position": r.position, "g": r.g or 0, "po": r.po or 0, "a": r.a or 0, "e": r.e or 0, "dp": r.dp or 0}
                for r in rows
            ]
        )
        if df.empty or not team_count:
            return df
        df["e_per_team"] = df["e"] / team_count
        return df
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
            Player.display_name,
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
        .where(Player.display_name.in_(names))
        .order_by(Player.display_name, Season.year)
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
            Player.display_name,
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
        .where(Player.display_name.in_(names))
        .order_by(Player.display_name, Season.year)
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
        return sorted({row[0] for row in session.execute(select(Player.display_name))})
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
            .where(Player.display_name == full_name)
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
            .where(Player.display_name == full_name, PlateAppearance.hitpull.is_not(None))
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
            .where(Player.display_name == full_name, PlateAppearance.hitpull.is_not(None))
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
        select(BatterPitcherMatchup, OpponentPlayer.display_name, Season.year, League.code)
        .join(PlayerSeason, PlayerSeason.id == self_ps_col)
        .join(Player, Player.id == PlayerSeason.player_id)
        .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
        .join(LeagueSeason, LeagueSeason.id == TeamSeason.league_season_id)
        .join(Season, Season.id == LeagueSeason.season_id)
        .join(League, League.id == LeagueSeason.league_id)
        .join(OpponentSeason, OpponentSeason.id == opp_ps_col)
        .join(OpponentPlayer, OpponentPlayer.id == OpponentSeason.player_id)
        .where(Player.display_name == full_name)
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


# --------------------------------------------------------------------------
# Scouting report (app/pages/8_Scouting_Report.py)
# --------------------------------------------------------------------------


def _team_season_id(session, league_season_id: int, team_name: str) -> int | None:
    return session.execute(
        select(TeamSeason.id).where(
            TeamSeason.league_season_id == league_season_id, TeamSeason.display_name == team_name
        )
    ).scalar_one_or_none()


@st.cache_data
def next_fixtures(league_season_id: int, team_name: str) -> pd.DataFrame:
    """This team's not-yet-played games, soonest first — the schedule scrape
    stores the whole season's fixtures up front (status "scheduled"), so
    this is how the Scouting Report page defaults to the next opponent.
    Empty for completed historical seasons."""
    session = get_session()
    try:
        ts_id = _team_season_id(session, league_season_id, team_name)
        if ts_id is None:
            return pd.DataFrame()
        home_ts, away_ts = aliased(TeamSeason), aliased(TeamSeason)
        rows = session.execute(
            select(
                Game.game_date,
                home_ts.display_name.label("home"),
                away_ts.display_name.label("away"),
                Game.venue,
            )
            .join(home_ts, home_ts.id == Game.home_team_season_id)
            .join(away_ts, away_ts.id == Game.away_team_season_id)
            .where(
                Game.league_season_id == league_season_id,
                Game.status == "scheduled",
                or_(Game.home_team_season_id == ts_id, Game.away_team_season_id == ts_id),
            )
            .order_by(Game.game_date)
        ).all()
        return pd.DataFrame(
            [
                {
                    "game_date": r.game_date,
                    "opponent": r.away if r.home == team_name else r.home,
                    "home_away": "Home" if r.home == team_name else "Away",
                    "venue": r.venue,
                }
                for r in rows
            ]
        )
    finally:
        session.close()


_SCOUTING_HITTER_COUNT_FIELDS = ["pa", "ab", "h", "doubles", "triples", "hr", "bb", "ibb", "hbp", "so", "sf", "sb"]


@st.cache_data
def scouting_hitters(league_season_id: int, team_name: str) -> pd.DataFrame:
    """One team's hitters for the scouting report, best first — raw counting
    stats plus rate stats, wOBA/wRC+, the shrinkage layer's true-talent wOBA
    (the ranking key: observed wOBA on 15 PA is mostly noise), and the spray
    tendency label. Raw counts are included because the lineup optimizer
    builds its event profiles from them."""
    session = get_session()
    try:
        ctx = _lg_context(session, league_season_id)
        lg_woba = ctx.lg_woba if ctx else None
        rows = session.execute(
            select(BattingSeasonStats, Player.display_name, Player.bats, BattingTrueTalent, BatterSpraySeasonStats)
            .join(PlayerSeason, PlayerSeason.id == BattingSeasonStats.player_season_id)
            .join(Player, Player.id == PlayerSeason.player_id)
            .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
            .outerjoin(BattingTrueTalent, BattingTrueTalent.player_season_id == PlayerSeason.id)
            .outerjoin(BatterSpraySeasonStats, BatterSpraySeasonStats.player_season_id == PlayerSeason.id)
            .where(TeamSeason.league_season_id == league_season_id, TeamSeason.display_name == team_name)
        ).all()
        records = []
        for stats_row, full_name, bats, talent, spray in rows:
            observed = compute_woba(stats_row)
            records.append(
                {
                    "player": full_name,
                    "bats": bats,
                    **{f: getattr(stats_row, f) for f in _SCOUTING_HITTER_COUNT_FIELDS},
                    **batting_rate_stats(stats_row),
                    "woba": observed,
                    "shrunk_woba": talent.shrunk_woba if talent else None,
                    "wrc_plus": wrc_plus(observed, lg_woba),
                    "tendency": spray.tendency_label if spray else None,
                }
            )
        df = pd.DataFrame(records)
        if df.empty:
            return df
        return df.sort_values(["shrunk_woba", "woba", "pa"], ascending=False, na_position="last").reset_index(
            drop=True
        )
    finally:
        session.close()


@st.cache_data
def scouting_pitching_staff(league_season_id: int, team_name: str) -> pd.DataFrame:
    """One team's pitching staff ranked by probable-starter likelihood
    (stats/probable_pitchers.py), with season rates, first-pitch-strike%,
    and true-talent FIP attached for display. Relievers are included (score
    0, at the bottom); `evidence` is filled for the top probable starters
    only."""
    session = get_session()
    try:
        ts_id = _team_season_id(session, league_season_id, team_name)
        if ts_id is None:
            return pd.DataFrame()
        usage = staff_usage(session, ts_id)
        if not usage:
            return pd.DataFrame()
        ctx = _lg_context(session, league_season_id)
        fip_constant = ctx.fip_constant if ctx else None
        evidence = {
            row["player_season_id"]: row["evidence"] for row in probable_starters(session, ts_id, top_n=3)
        }
        records = []
        for row in usage:
            ps_id = row["player_season_id"]
            detail = session.execute(
                select(Player.display_name, Player.throws, PitchingSeasonStats, PitchingTrueTalent)
                .select_from(PlayerSeason)
                .join(Player, Player.id == PlayerSeason.player_id)
                .outerjoin(PitchingSeasonStats, PitchingSeasonStats.player_season_id == PlayerSeason.id)
                .outerjoin(PitchingTrueTalent, PitchingTrueTalent.player_season_id == PlayerSeason.id)
                .where(PlayerSeason.id == ps_id)
            ).first()
            if detail is None:
                continue
            full_name, throws, season, talent = detail
            rates = pitching_rate_stats(season) if season else {"ip": None, "era": None, "whip": None, "k9": None, "bb9": None}
            records.append(
                {
                    "player": full_name,
                    "throws": throws,
                    "g": row["g"],
                    "gs": row["gs"],
                    "ip": outs_to_ip_display(row["outs"]),
                    "team_ip_share": row["team_ip_share"],
                    "sv": row["saves"],
                    **{k: rates[k] for k in ("era", "whip", "k9", "bb9")},
                    "fip": fip(season, fip_constant) if season else None,
                    "shrunk_fip": talent.shrunk_fip if talent else None,
                    "fps_pct": fps_pct(season.fps_strikes, season.fps_pa) if season else None,
                    "so": season.so if season else 0,
                    "bb": season.bb if season else 0,
                    "bf": season.bf if season else 0,
                    "last_date": row["last_date"],
                    "score_share": row["score_share"],
                    "confidence": row["confidence"] if row["gs"] > 0 else None,
                    "evidence": evidence.get(ps_id),
                }
            )
        return pd.DataFrame(records)
    finally:
        session.close()


@st.cache_data
def roster_vs_pitcher(league_season_id: int, team_name: str, pitcher_name: str) -> pd.DataFrame:
    """Career batter-vs-pitcher history for every batter on `team_name`'s
    current roster against one opposing pitcher, summed across every season
    they've met (BatterPitcherMatchup rows carry no minimum sample size —
    the UI/PDF must caveat small PA counts, same as the Player Page)."""
    session = get_session()
    try:
        roster_player_ids = set(
            session.execute(
                select(PlayerSeason.player_id)
                .join(TeamSeason, TeamSeason.id == PlayerSeason.team_season_id)
                .where(TeamSeason.league_season_id == league_season_id, TeamSeason.display_name == team_name)
            )
            .scalars()
            .all()
        )
        if not roster_player_ids:
            return pd.DataFrame()
        BatterSeason, BatterPlayer = aliased(PlayerSeason), aliased(Player)
        PitcherSeason, PitcherPlayer = aliased(PlayerSeason), aliased(Player)
        rows = session.execute(
            select(BatterPitcherMatchup, BatterPlayer.display_name)
            .join(BatterSeason, BatterSeason.id == BatterPitcherMatchup.batter_player_season_id)
            .join(BatterPlayer, BatterPlayer.id == BatterSeason.player_id)
            .join(PitcherSeason, PitcherSeason.id == BatterPitcherMatchup.pitcher_player_season_id)
            .join(PitcherPlayer, PitcherPlayer.id == PitcherSeason.player_id)
            .where(PitcherPlayer.display_name == pitcher_name, BatterPlayer.id.in_(roster_player_ids))
        ).all()
        by_batter: dict[str, dict[str, int]] = {}
        for matchup, batter_name in rows:
            entry = by_batter.setdefault(batter_name, {f: 0 for f in _MATCHUP_COUNT_FIELDS})
            for f in _MATCHUP_COUNT_FIELDS:
                entry[f] += getattr(matchup, f)
        df = pd.DataFrame(
            [
                {"player": name, **totals, "avg": avg(totals["h"], totals["ab"])}
                for name, totals in by_batter.items()
            ]
        )
        return df.sort_values("pa", ascending=False).reset_index(drop=True) if not df.empty else df
    finally:
        session.close()


def _vs_hand_woba_rows(session, player_names: list[str], throws: str, as_batter: bool) -> pd.DataFrame:
    """Career PA totals and observed wOBA for each named player against
    opponents of one handedness, from raw PlateAppearance rows. as_batter
    selects which side of the PA the named players are on."""
    own_season, own_player = aliased(PlayerSeason), aliased(Player)
    opp_season, opp_player = aliased(PlayerSeason), aliased(Player)
    own_col = PlateAppearance.batter_player_season_id if as_batter else PlateAppearance.pitcher_player_season_id
    opp_col = PlateAppearance.pitcher_player_season_id if as_batter else PlateAppearance.batter_player_season_id
    opp_hand = opp_player.throws if as_batter else opp_player.bats
    component_fields = ["ab", "h", "doubles", "triples", "hr", "bb", "ibb", "hbp", "so", "sf"]
    rows = session.execute(
        select(
            own_player.display_name,
            func.count().label("pa"),
            *[func.sum(getattr(PlateAppearance, f)).label(f) for f in component_fields],
        )
        .join(own_season, own_season.id == own_col)
        .join(own_player, own_player.id == own_season.player_id)
        .join(opp_season, opp_season.id == opp_col)
        .join(opp_player, opp_player.id == opp_season.player_id)
        .where(own_player.display_name.in_(player_names), opp_hand == throws)
        .group_by(own_player.display_name)
    ).all()
    records = []
    for row in rows:
        components = SimpleNamespace(**{f: (getattr(row, f) or 0) for f in component_fields})
        records.append(
            {
                "player": row.display_name,
                "pa": row.pa,
                "woba": compute_woba(components),
                "avg": avg(components.h, components.ab),
            }
        )
    return pd.DataFrame(records)


@st.cache_data
def batters_vs_hand(player_names: list[str], throws: str) -> pd.DataFrame:
    """Career vs-LHP/vs-RHP observed wOBA per named batter — the platoon
    input the lineup optimizer shrinks toward each batter's overall talent
    (stats/lineup.py: platoon_adjusted_woba)."""
    session = get_session()
    try:
        return _vs_hand_woba_rows(session, player_names, throws, as_batter=True)
    finally:
        session.close()


@st.cache_data
def pitcher_vs_hands(pitcher_name: str) -> pd.DataFrame:
    """Career opponent wOBA allowed by one pitcher, split by batter
    handedness — one row per hand faced ("L"/"R"), for the scouting PDF's
    pitcher blocks."""
    session = get_session()
    try:
        frames = []
        for hand in ("L", "R"):
            df = _vs_hand_woba_rows(session, [pitcher_name], hand, as_batter=False)
            if not df.empty:
                df["vs_hand"] = hand
                frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    finally:
        session.close()


@st.cache_data
def league_batting_component_totals(league_season_id: int) -> dict:
    """League-wide batting counting totals for stats/lineup.py's
    league_component_rates — reuses league_context's aggregation so the
    lineup model and the published league context always agree."""
    session = get_session()
    try:
        return _league_batting_totals(session, league_season_id)
    finally:
        session.close()


@st.cache_data
def lineup_recommendation(
    league_season_id: int, team_name: str, player_names: list[str], vs_throws: str | None
) -> dict:
    """Assemble lineup-optimizer inputs for the available batters and run the
    optimization (stats/lineup.py). The 9 best bats (platoon-adjusted) start;
    everyone else is benched and ranked as a pinch-hit option vs LHP and vs
    RHP. Returns:

    - "result": LineupResult for the starting nine (rationale phrased in
      box-score stats via stats/lineup.py:slot_rationales, not model
      internals),
    - "lineup": DataFrame, one row per slot with the season stats that
      justify it (PA/AVG/OBP/SLG/ISO/BB%/K%/SB),
    - "bench": DataFrame with each bench bat's season line, observed vs-hand
      splits, and a `role` naming the best bat off the bench vs LHP/RHP,
    - "profiles": the model-internals audit table (what the optimizer
      actually believed), kept for the "under the hood" expander."""
    hitters = scouting_hitters(league_season_id, team_name)
    league_rates = league_component_rates(league_batting_component_totals(league_season_id))
    ctx_woba = None
    session = get_session()
    try:
        ctx = _lg_context(session, league_season_id)
        ctx_woba = ctx.lg_woba if ctx else None
    finally:
        session.close()

    # Vs-hand history for BOTH hands: the profile target uses the hand we're
    # optimizing against, but the bench roles need each player rated vs LHP
    # and vs RHP regardless of who starts.
    vs_split = {
        hand: {r["player"]: r for _, r in batters_vs_hand(player_names, hand).iterrows()}
        for hand in ("L", "R")
    }
    by_player = {r["player"]: r for _, r in hitters.iterrows()} if not hitters.empty else {}

    def overall_talent(name: str) -> float | None:
        row = by_player.get(name)
        if row is None:
            return None
        overall = row["shrunk_woba"] if pd.notna(row["shrunk_woba"]) else row["woba"]
        return overall if pd.notna(overall) else ctx_woba

    def adjusted_target(name: str, hand: str | None) -> float | None:
        # No season data at all: anchor to the same league-context wOBA the
        # known hitters' shrinkage uses, so best-9 selection compares
        # everyone on one scale (build_profile's own None-fallback is the
        # component-implied league wOBA, which can sit on a different scale
        # from the stored context value).
        target = overall_talent(name) if by_player.get(name) is not None else ctx_woba
        if target is None or hand not in ("L", "R"):
            return target
        split = vs_split[hand].get(name)
        vs_pa = int(split["pa"]) if split is not None else 0
        vs_woba = split["woba"] if split is not None and pd.notna(split["woba"]) else None
        return platoon_adjusted_woba(target, vs_pa, vs_woba)

    profiles = []
    audit_rows = []
    for name in player_names:
        row = by_player.get(name)
        counts = (
            {f: int(row[f]) for f in ("pa", "h", "doubles", "triples", "hr", "bb", "hbp")}
            if row is not None
            else {"pa": 0}
        )
        profile = build_profile(name, counts, league_rates, adjusted_target(name, vs_throws))
        profiles.append(profile)
        split = vs_split[vs_throws].get(name) if vs_throws in ("L", "R") else None
        audit_rows.append(
            {
                "player": name,
                "pa": counts.get("pa", 0),
                "shrunk_woba": overall_talent(name),
                "vs_hand_pa": int(split["pa"]) if split is not None else 0,
                "vs_hand_woba": split["woba"] if split is not None else None,
                "target_woba": profile.implied_woba,
                "onbase": profile.p_onbase,
                "power": profile.power,
            }
        )

    def season_pa(name: str) -> int:
        row = by_player.get(name)
        return int(row["pa"]) if row is not None else 0

    def conservative(name: str, hand: str | None) -> float:
        # Sample-aware ranking score: the adjusted estimate minus an
        # uncertainty penalty, so "7 PA of nothing ≈ league average" can't
        # outrank a real track record (see stats/lineup.py).
        estimate = adjusted_target(name, hand)
        score = conservative_woba(estimate if estimate is not None else ctx_woba, season_pa(name))
        return score if score is not None else 0.0

    selection_scores = [conservative(name, vs_throws) for name in player_names]
    starters, bench_profiles = select_starters(profiles, scores=selection_scores)
    result = optimize_lineup(starters)

    _STAT_KEYS = ("pa", "avg", "obp", "slg", "iso", "bb_pct", "k_pct", "sb")

    def season_stats(name: str) -> dict:
        row = by_player.get(name)
        if row is None:
            return {}
        return {k: (row[k] if pd.notna(row[k]) else None) for k in _STAT_KEYS if k in row}

    stats_by_name = {name: season_stats(name) for name in player_names}
    result.rationale = slot_rationales(result.order, stats_by_name)

    lineup_df = pd.DataFrame(
        [{"slot": slot, "player": name, **season_stats(name)} for slot, name in enumerate(result.order, start=1)]
    )

    bench_rows = []
    bench_names = [p.name for p in bench_profiles]
    # Only bench bats with a real sample are eligible to be *named* the best
    # option — an 0-for-7 player whose shrunk estimate sits near league
    # average must not be recommended over hitters with actual track
    # records. Ranking among the eligible is still the conservative
    # (sample-penalized) vs-hand estimate.
    eligible = [n for n in bench_names if season_pa(n) >= MIN_PA_FOR_JUDGEMENT]
    best_vs = {hand: max(eligible, key=lambda n: conservative(n, hand), default=None) for hand in ("L", "R")}
    for name in bench_names:
        roles = [f"vs {hand}HP" for hand in ("L", "R") if best_vs[hand] == name]
        if roles:
            role = "First bat off the bench " + " & ".join(roles)
        elif season_pa(name) < MIN_PA_FOR_JUDGEMENT:
            role = f"Too few PA to judge ({season_pa(name)})"
        else:
            role = ""
        split_l, split_r = vs_split["L"].get(name), vs_split["R"].get(name)
        bench_rows.append(
            {
                "player": name,
                "role": role,
                **season_stats(name),
                "avg_vs_lhp": split_l["avg"] if split_l is not None else None,
                "pa_vs_lhp": int(split_l["pa"]) if split_l is not None else 0,
                "avg_vs_rhp": split_r["avg"] if split_r is not None else None,
                "pa_vs_rhp": int(split_r["pa"]) if split_r is not None else 0,
            }
        )
    bench_df = pd.DataFrame(bench_rows)
    if not bench_df.empty:
        bench_df = bench_df.sort_values(
            "player", key=lambda s: s.map(lambda n: -conservative(n, vs_throws)), kind="stable"
        ).reset_index(drop=True)

    return {"result": result, "lineup": lineup_df, "bench": bench_df, "profiles": pd.DataFrame(audit_rows)}


@st.cache_data
def coverage_summary() -> dict:
    """Headline counts of what's in the database, for the Feedback & Support page.

    Read live rather than written into the copy so the page can't quietly go
    stale as more seasons are scraped.
    """
    session = get_session()
    try:
        first_year, last_year = session.execute(
            select(func.min(Season.year), func.max(Season.year))
        ).one()
        return {
            "first_year": first_year,
            "last_year": last_year,
            "divisions": session.execute(select(func.count(League.id))).scalar() or 0,
            # Games with a scoresheet behind them, which is what "how much
            # data is here" means to a reader — forfeits are real results
            # but carry no statistics to explore.
            "games": session.execute(
                select(func.count(Game.id)).where(
                    Game.status == "final", Game.result_type == "played"
                )
            ).scalar()
            or 0,
            "players": session.execute(select(func.count(Player.id))).scalar() or 0,
        }
    finally:
        session.close()


@st.cache_data
def data_freshness() -> str | None:
    """Timestamp of the most recent scrape, for the PDF header."""
    session = get_session()
    try:
        latest = session.execute(select(func.max(ScrapeLog.fetched_at))).scalar()
        return str(latest) if latest else None
    finally:
        session.close()
