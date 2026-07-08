"""SQLAlchemy ORM models for the British Baseball Stats Explorer.

Layering (see stats/ package for the derivation logic that populates the
"derived" tables): scraper writes only to the dimension and fact tables;
everything under "Derived / materialized stats" is recomputed from fact rows
by the stats/ package and is safe to drop and rebuild at any time.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------


class League(Base):
    """A competition identity that persists across years, e.g. 'nbl'.

    Corresponds to a competition_code in the site's URL scheme
    (/en/events/{year}-{code}/...). See scraper/recon/findings.md.
    """

    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    tier: Mapped[str | None] = mapped_column(String, nullable=True)
    is_senior: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    league_seasons: Mapped[list["LeagueSeason"]] = relationship(back_populates="league")


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    league_seasons: Mapped[list["LeagueSeason"]] = relationship(back_populates="season")


class LeagueSeason(Base):
    """One league's specific instance in one year (a WBSC 'tournament')."""

    __tablename__ = "league_seasons"
    __table_args__ = (UniqueConstraint("league_id", "season_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), index=True)
    source_tournament_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    competition_slug: Mapped[str] = mapped_column(String)  # e.g. "2026-nbl"
    start_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    end_date: Mapped[dt.date | None] = mapped_column(nullable=True)

    league: Mapped["League"] = relationship(back_populates="league_seasons")
    season: Mapped["Season"] = relationship(back_populates="league_seasons")
    team_seasons: Mapped[list["TeamSeason"]] = relationship(back_populates="league_season")
    games: Mapped[list["Game"]] = relationship(back_populates="league_season")


class Team(Base):
    """Persistent team identity across years.

    The site's own team id (teamid) is scoped per competition-instance and
    its cross-year stability is unconfirmed (see findings.md) — cross-year
    identity is resolved by name matching in the upsert layer, not by a
    site-provided id, hence no source_id here.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)

    team_seasons: Mapped[list["TeamSeason"]] = relationship(back_populates="team")


class TeamSeason(Base):
    """A team's participation in one league_season — this is what the site's
    teamid actually identifies."""

    __tablename__ = "team_seasons"
    __table_args__ = (UniqueConstraint("league_season_id", "source_team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    league_season_id: Mapped[int] = mapped_column(ForeignKey("league_seasons.id"), index=True)
    source_team_id: Mapped[int] = mapped_column(Integer, index=True)
    display_name: Mapped[str] = mapped_column(String)
    short_code: Mapped[str | None] = mapped_column(String, nullable=True)

    team: Mapped["Team"] = relationship(back_populates="team_seasons")
    league_season: Mapped["LeagueSeason"] = relationship(back_populates="team_seasons")
    player_seasons: Mapped[list["PlayerSeason"]] = relationship(back_populates="team_season")


class Player(Base):
    """Persistent player identity — the site's playerid is stable platform-wide
    (confirmed in recon), so it's used directly as source_id."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str] = mapped_column(String, index=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bats: Mapped[str | None] = mapped_column(String, nullable=True)
    throws: Mapped[str | None] = mapped_column(String, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String, nullable=True)

    player_seasons: Mapped[list["PlayerSeason"]] = relationship(back_populates="player")


class PlayerSeason(Base):
    """A player's affiliation with one team_season (handles mid-season moves:
    a player who switches teams gets a separate PlayerSeason row per team)."""

    __tablename__ = "player_seasons"
    __table_args__ = (UniqueConstraint("player_id", "team_season_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_season_id: Mapped[int] = mapped_column(ForeignKey("team_seasons.id"), index=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_primary: Mapped[str | None] = mapped_column(String, nullable=True)

    player: Mapped["Player"] = relationship(back_populates="player_seasons")
    team_season: Mapped["TeamSeason"] = relationship(back_populates="player_seasons")


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    league_season_id: Mapped[int] = mapped_column(ForeignKey("league_seasons.id"), index=True)
    game_date: Mapped[dt.date | None] = mapped_column(index=True, nullable=True)
    home_team_season_id: Mapped[int] = mapped_column(ForeignKey("team_seasons.id"))
    away_team_season_id: Mapped[int] = mapped_column(ForeignKey("team_seasons.id"))
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String)  # scheduled / final / postponed / cancelled
    venue: Mapped[str | None] = mapped_column(String, nullable=True)

    # Runners left on base, derived from the box-score payload's `gamePlays`
    # play-by-play feed (see scraper/recon/risp_lob_plan.md) — nullable since
    # older games scraped before this field existed won't have it until
    # re-processed, and some "final" games have no play-by-play at all.
    home_lob: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_lob: Mapped[int | None] = mapped_column(Integer, nullable=True)

    scraped_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    league_season: Mapped["LeagueSeason"] = relationship(back_populates="games")


class BattingGameLine(Base):
    __tablename__ = "batting_game_lines"
    __table_args__ = (UniqueConstraint("game_id", "player_season_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    player_season_id: Mapped[int] = mapped_column(ForeignKey("player_seasons.id"), index=True)
    team_season_id: Mapped[int] = mapped_column(ForeignKey("team_seasons.id"), index=True)

    pa: Mapped[int] = mapped_column(Integer, default=0)
    ab: Mapped[int] = mapped_column(Integer, default=0)
    r: Mapped[int] = mapped_column(Integer, default=0)
    h: Mapped[int] = mapped_column(Integer, default=0)
    doubles: Mapped[int] = mapped_column(Integer, default=0)
    triples: Mapped[int] = mapped_column(Integer, default=0)
    hr: Mapped[int] = mapped_column(Integer, default=0)
    rbi: Mapped[int] = mapped_column(Integer, default=0)
    bb: Mapped[int] = mapped_column(Integer, default=0)
    ibb: Mapped[int] = mapped_column(Integer, default=0)
    hbp: Mapped[int] = mapped_column(Integer, default=0)
    so: Mapped[int] = mapped_column(Integer, default=0)
    sf: Mapped[int] = mapped_column(Integer, default=0)
    sh: Mapped[int] = mapped_column(Integer, default=0)
    sb: Mapped[int] = mapped_column(Integer, default=0)
    cs: Mapped[int] = mapped_column(Integer, default=0)
    gdp: Mapped[int] = mapped_column(Integer, default=0)

    # Fielding — captured for display purposes only; not used in WAR (see
    # stats/war.py docstring for why the defensive component was dropped).
    field_po: Mapped[int] = mapped_column(Integer, default=0)
    field_a: Mapped[int] = mapped_column(Integer, default=0)
    field_e: Mapped[int] = mapped_column(Integer, default=0)
    field_dp: Mapped[int] = mapped_column(Integer, default=0)
    field_sba: Mapped[int] = mapped_column(Integer, default=0)
    field_csb: Mapped[int] = mapped_column(Integer, default=0)
    field_pb: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[str | None] = mapped_column(String, nullable=True)

    # Situational splits derived from `gamePlays` (see
    # scraper/recon/risp_lob_plan.md) — at-bats/hits with a runner on 2nd or
    # 3rd at the time of the plate appearance.
    risp_ab: Mapped[int] = mapped_column(Integer, default=0)
    risp_h: Mapped[int] = mapped_column(Integer, default=0)


class PitchingGameLine(Base):
    __tablename__ = "pitching_game_lines"
    __table_args__ = (UniqueConstraint("game_id", "player_season_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    player_season_id: Mapped[int] = mapped_column(ForeignKey("player_seasons.id"), index=True)
    team_season_id: Mapped[int] = mapped_column(ForeignKey("team_seasons.id"), index=True)

    # Stored as outs recorded (not float IP) to avoid the classic
    # "1.1 + 1.1 = 2.2 IP" bug — divide by 3 for display/formula purposes.
    outs_recorded: Mapped[int] = mapped_column(Integer, default=0)
    h: Mapped[int] = mapped_column(Integer, default=0)
    r: Mapped[int] = mapped_column(Integer, default=0)
    er: Mapped[int] = mapped_column(Integer, default=0)
    bb: Mapped[int] = mapped_column(Integer, default=0)
    ibb: Mapped[int] = mapped_column(Integer, default=0)
    so: Mapped[int] = mapped_column(Integer, default=0)
    hr: Mapped[int] = mapped_column(Integer, default=0)
    hbp: Mapped[int] = mapped_column(Integer, default=0)
    bf: Mapped[int] = mapped_column(Integer, default=0)
    win: Mapped[bool] = mapped_column(Boolean, default=False)
    loss: Mapped[bool] = mapped_column(Boolean, default=False)
    save: Mapped[bool] = mapped_column(Boolean, default=False)


class PlateAppearance(Base):
    """One row per completed plate appearance, parsed from the box score's
    `gamePlays` play-by-play feed alongside RISP/LOB (see
    scraper/scrape_boxscores.py, scraper/recon/risp_lob_plan.md). Feeds
    batter pull/spray tendency, batter-vs-pitcher matchups, and pitcher
    first-pitch-strike% (stats/spray.py, stats/matchups.py) — a single fact
    table rather than three, since all three derive from the same per-PA
    data."""

    __tablename__ = "plate_appearances"
    __table_args__ = (
        Index("ix_plate_appearances_batter_pitcher", "batter_player_season_id", "pitcher_player_season_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_play_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    inning: Mapped[int] = mapped_column(Integer)
    half: Mapped[str] = mapped_column(String)  # "top" / "bottom"

    batter_player_season_id: Mapped[int] = mapped_column(ForeignKey("player_seasons.id"), index=True)
    # Nullable: pitcherid's resolvability against player_season_id_by_player
    # is less thoroughly confirmed than batterid's (see scrape_boxscores.py).
    pitcher_player_season_id: Mapped[int | None] = mapped_column(
        ForeignKey("player_seasons.id"), index=True, nullable=True
    )

    ab: Mapped[int] = mapped_column(Integer, default=0)
    h: Mapped[int] = mapped_column(Integer, default=0)
    doubles: Mapped[int] = mapped_column(Integer, default=0)
    triples: Mapped[int] = mapped_column(Integer, default=0)
    hr: Mapped[int] = mapped_column(Integer, default=0)
    bb: Mapped[int] = mapped_column(Integer, default=0)
    ibb: Mapped[int] = mapped_column(Integer, default=0)
    hbp: Mapped[int] = mapped_column(Integer, default=0)
    so: Mapped[int] = mapped_column(Integer, default=0)
    sf: Mapped[int] = mapped_column(Integer, default=0)
    rbi: Mapped[int] = mapped_column(Integer, default=0)

    # Derived by diffing the balls/strikes count between the first pitch and
    # the next record in the same PA — this league's called/swing/foul/inplay
    # flags are confirmed always-zero (dead fields), so they can't be read
    # directly. None when undeterminable (e.g. no play-by-play for the game).
    first_pitch_strike: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Batted-ball proxies, populated only when this PA ended in a ball in
    # play. This league's scorers never populate true hitx/hity/exitvelo
    # coordinates (see CLAUDE.md's "no batted-ball tracking data" note) —
    # hitpull (raw, absolute field direction: negative = left/third-base
    # side, positive = right/first-base side — NOT handedness-adjusted) +
    # hitdistance + hittype are the closest available approximation.
    hitpull: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hitdistance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hittype: Mapped[int | None] = mapped_column(Integer, nullable=True)


# --------------------------------------------------------------------------
# Derived / materialized stats — rebuilt by stats/, never scraped directly
# --------------------------------------------------------------------------


class BattingSeasonStats(Base):
    __tablename__ = "batting_season_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(
        ForeignKey("player_seasons.id"), unique=True, index=True
    )
    pa: Mapped[int] = mapped_column(Integer, default=0)
    ab: Mapped[int] = mapped_column(Integer, default=0)
    r: Mapped[int] = mapped_column(Integer, default=0)
    h: Mapped[int] = mapped_column(Integer, default=0)
    doubles: Mapped[int] = mapped_column(Integer, default=0)
    triples: Mapped[int] = mapped_column(Integer, default=0)
    hr: Mapped[int] = mapped_column(Integer, default=0)
    rbi: Mapped[int] = mapped_column(Integer, default=0)
    bb: Mapped[int] = mapped_column(Integer, default=0)
    ibb: Mapped[int] = mapped_column(Integer, default=0)
    hbp: Mapped[int] = mapped_column(Integer, default=0)
    so: Mapped[int] = mapped_column(Integer, default=0)
    sf: Mapped[int] = mapped_column(Integer, default=0)
    sh: Mapped[int] = mapped_column(Integer, default=0)
    sb: Mapped[int] = mapped_column(Integer, default=0)
    cs: Mapped[int] = mapped_column(Integer, default=0)
    gdp: Mapped[int] = mapped_column(Integer, default=0)

    # Fielding — same "display only, not used in WAR" scope as the
    # batting_game_lines fields these are summed from (see BattingGameLine).
    field_po: Mapped[int] = mapped_column(Integer, default=0)
    field_a: Mapped[int] = mapped_column(Integer, default=0)
    field_e: Mapped[int] = mapped_column(Integer, default=0)
    field_dp: Mapped[int] = mapped_column(Integer, default=0)

    # Situational splits — see BattingGameLine.risp_ab/risp_h.
    risp_ab: Mapped[int] = mapped_column(Integer, default=0)
    risp_h: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class PitchingSeasonStats(Base):
    __tablename__ = "pitching_season_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(
        ForeignKey("player_seasons.id"), unique=True, index=True
    )
    outs_recorded: Mapped[int] = mapped_column(Integer, default=0)
    h: Mapped[int] = mapped_column(Integer, default=0)
    r: Mapped[int] = mapped_column(Integer, default=0)
    er: Mapped[int] = mapped_column(Integer, default=0)
    bb: Mapped[int] = mapped_column(Integer, default=0)
    ibb: Mapped[int] = mapped_column(Integer, default=0)
    so: Mapped[int] = mapped_column(Integer, default=0)
    hr: Mapped[int] = mapped_column(Integer, default=0)
    hbp: Mapped[int] = mapped_column(Integer, default=0)
    bf: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)

    # First-pitch-strike%, derived from PlateAppearance.first_pitch_strike —
    # see stats/aggregation.py.
    fps_pa: Mapped[int] = mapped_column(Integer, default=0)
    fps_strikes: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class LeagueSeasonContext(Base):
    """Self-calibrated league-average inputs WAR depends on for one
    league_season — see stats/league_context.py."""

    __tablename__ = "league_season_context"

    id: Mapped[int] = mapped_column(primary_key=True)
    league_season_id: Mapped[int] = mapped_column(
        ForeignKey("league_seasons.id"), unique=True, index=True
    )
    lg_obp: Mapped[float | None] = mapped_column(Float, nullable=True)
    lg_slg: Mapped[float | None] = mapped_column(Float, nullable=True)
    lg_woba: Mapped[float | None] = mapped_column(Float, nullable=True)
    lg_era: Mapped[float | None] = mapped_column(Float, nullable=True)
    lg_fip: Mapped[float | None] = mapped_column(Float, nullable=True)
    fip_constant: Mapped[float | None] = mapped_column(Float, nullable=True)
    runs_per_pa: Mapped[float | None] = mapped_column(Float, nullable=True)
    runs_per_win: Mapped[float | None] = mapped_column(Float, nullable=True)
    replacement_runs_per_pa: Mapped[float | None] = mapped_column(Float, nullable=True)
    replacement_fip_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BattingWar(Base):
    __tablename__ = "batting_war"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(
        ForeignKey("player_seasons.id"), unique=True, index=True
    )
    woba: Mapped[float | None] = mapped_column(Float, nullable=True)
    wraa: Mapped[float | None] = mapped_column(Float, nullable=True)
    war: Mapped[float | None] = mapped_column(Float, nullable=True)
    formula_version: Mapped[str] = mapped_column(String)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class PitchingWar(Base):
    __tablename__ = "pitching_war"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(
        ForeignKey("player_seasons.id"), unique=True, index=True
    )
    fip: Mapped[float | None] = mapped_column(Float, nullable=True)
    war: Mapped[float | None] = mapped_column(Float, nullable=True)
    formula_version: Mapped[str] = mapped_column(String)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BattingTrueTalent(Base):
    """Empirical-Bayes shrinkage of season wOBA toward the league-season
    mean, weighted by PA against a stabilization point self-calibrated from
    this league-season's own player-to-player variance (see
    stats/shrinkage.py) — falls back to a published stabilization-point
    constant when the league-season's own data can't support the estimate
    (k_self_calibrated distinguishes which path was used)."""

    __tablename__ = "batting_true_talent"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(
        ForeignKey("player_seasons.id"), unique=True, index=True
    )
    pa: Mapped[int] = mapped_column(Integer, default=0)
    observed_woba: Mapped[float | None] = mapped_column(Float, nullable=True)
    shrunk_woba: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability: Mapped[float | None] = mapped_column(Float, nullable=True)
    stabilization_pa: Mapped[float | None] = mapped_column(Float, nullable=True)
    k_self_calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class PitchingTrueTalent(Base):
    """Pitching-side counterpart to BattingTrueTalent, shrinking FIP toward
    the league-season mean weighted by IP — see stats/shrinkage.py."""

    __tablename__ = "pitching_true_talent"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(
        ForeignKey("player_seasons.id"), unique=True, index=True
    )
    ip: Mapped[float] = mapped_column(Float, default=0.0)
    observed_fip: Mapped[float | None] = mapped_column(Float, nullable=True)
    shrunk_fip: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability: Mapped[float | None] = mapped_column(Float, nullable=True)
    stabilization_ip: Mapped[float | None] = mapped_column(Float, nullable=True)
    k_self_calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BatterSpraySeasonStats(Base):
    """Season-level pull/center/oppo tendency for one batter, bucketed
    against fixed thirds of the true 90-degree fair-territory fan (see
    stats/spray.py). Switch hitters (Player.bats == "S") are excluded — no
    per-PA batting-side data exists to know which side they actually hit
    from, so no row is written for them; career tendency is summed across
    these rows at read time (app/components/data_access.py), not stored
    separately."""

    __tablename__ = "batter_spray_season_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(
        ForeignKey("player_seasons.id"), unique=True, index=True
    )
    pull_count: Mapped[int] = mapped_column(Integer, default=0)
    center_count: Mapped[int] = mapped_column(Integer, default=0)
    oppo_count: Mapped[int] = mapped_column(Integer, default=0)
    tendency_label: Mapped[str | None] = mapped_column(String, nullable=True)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BatterPitcherMatchup(Base):
    """Aggregated plate-appearance results for one batter/pitcher pair within
    one league_season — see stats/matchups.py. No minimum-PA filter is
    applied here (rows with a single PA are stored same as any other); career
    totals are summed across these rows at read time
    (app/components/data_access.py), not stored separately."""

    __tablename__ = "batter_pitcher_matchups"
    __table_args__ = (UniqueConstraint("batter_player_season_id", "pitcher_player_season_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batter_player_season_id: Mapped[int] = mapped_column(ForeignKey("player_seasons.id"), index=True)
    pitcher_player_season_id: Mapped[int] = mapped_column(ForeignKey("player_seasons.id"), index=True)
    pa: Mapped[int] = mapped_column(Integer, default=0)
    ab: Mapped[int] = mapped_column(Integer, default=0)
    h: Mapped[int] = mapped_column(Integer, default=0)
    doubles: Mapped[int] = mapped_column(Integer, default=0)
    triples: Mapped[int] = mapped_column(Integer, default=0)
    hr: Mapped[int] = mapped_column(Integer, default=0)
    bb: Mapped[int] = mapped_column(Integer, default=0)
    so: Mapped[int] = mapped_column(Integer, default=0)
    hbp: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


# --------------------------------------------------------------------------
# Scraper bookkeeping
# --------------------------------------------------------------------------


class ScrapeLog(Base):
    __tablename__ = "scrape_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String, index=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    cache_path: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)  # ok / error / empty
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
