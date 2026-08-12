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

from db.identity import player_identity_key


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


class Division(Base):
    """A regional grouping within one league_season, e.g. 2026 Division 3's
    "North" / "Central" / "South" / "SWWBL".

    Scoped to a single league_season on purpose, unlike Team and Player: the
    site provides no year-spanning division identity and the names cannot
    supply one. The same regional grouping is published as "AA - Central"
    (2021), "South A" (2022), "South" (2023), "A" (2024, 2025) and "North"
    (2026), and names are reused for *different* regions in different years,
    so matching divisions across seasons by name would silently join
    unrelated groupings. Division counts churn as well — the NBL ran as a
    single division 2021-2025 and splits North/South for the first time in
    2026 — so consumers must handle a league-season with one division, or
    with none recorded at all.

    Membership comes from the site's `/standings` page rather than the
    schedule payload (scraper/scrape_standings.py): standings covers every
    scraped league-season including 2021, whereas the schedule's
    `wbsc_tournament_group_id` is absent for all of 2021 and partial for
    2024. The group id is still recorded here when known, because it is the
    only thing that tags an individual *game* with the division it counted
    toward (Game.source_group_id).
    """

    __tablename__ = "divisions"
    __table_args__ = (UniqueConstraint("league_season_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_season_id: Mapped[int] = mapped_column(ForeignKey("league_seasons.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    # The site's `wbsc_tournament_group_id`, when the schedule payload
    # supplied one. Nullable because 2021 has no group tagging at all and
    # 2024's is partial; a division is still fully usable without it, it just
    # can't attribute individual games by group.
    source_group_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # Display order as published on the standings page, so the app can show
    # divisions in the site's own order rather than alphabetically.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    league_season: Mapped["LeagueSeason"] = relationship()


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
    # Which regional division this team played in, for league_seasons that
    # have any. Nullable: a single-division league_season records no
    # divisions at all, and ~10 team_seasons across the corpus appear in
    # fixtures without ever appearing in a published standings table.
    division_id: Mapped[int | None] = mapped_column(
        ForeignKey("divisions.id"), index=True, nullable=True
    )

    team: Mapped["Team"] = relationship(back_populates="team_seasons")
    division: Mapped["Division | None"] = relationship()
    league_season: Mapped["LeagueSeason"] = relationship(back_populates="team_seasons")
    player_seasons: Mapped[list["PlayerSeason"]] = relationship(back_populates="team_season")


class Player(Base):
    """Persistent player identity, resolved by normalized name + birth year.

    The site's `playerid` is **not** a stable person id — it is reissued for
    every competition-instance roster entry, so it identifies a player-season,
    not a player, and lives on `PlayerSeason.source_player_id`. Cross-season
    identity is therefore name-matched here, the same way `Team` identity is;
    `db/identity.py` owns the matching rule and documents why it is strict.
    """

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The uniqueness key: normalized name + birth year (see db/identity.py).
    # Defaulted from the other columns so callers that build a Player without
    # precomputing it — tests, ad-hoc scripts — still get a correct key.
    identity_key: Mapped[str] = mapped_column(
        String,
        unique=True,
        index=True,
        default=lambda ctx: player_identity_key(
            ctx.get_current_parameters()["full_name"],
            ctx.get_current_parameters().get("birth_year"),
            ctx.get_current_parameters().get("source_id"),
        ),
    )
    # Most recent site id this player was seen under, kept for traceability
    # only — deliberately not unique, since the site issues a new one every
    # season. The full per-season set lives on PlayerSeason.source_player_id.
    source_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    full_name: Mapped[str] = mapped_column(String, index=True)
    # What the app shows and resolves a player by. Equal to full_name except
    # where two different people share a name, when it carries a disambiguator
    # — the app looks players up by this string, so a shared one silently adds
    # two careers together. Maintained by db/identity.py:refresh_display_names,
    # not per-row, because whether a name is shared is a fact about the whole
    # table. Defaults to full_name so a Player built without it is still valid.
    #
    # Deliberately *not* a UNIQUE column even though the values are unique by
    # construction: two players sharing a name are inserted one at a time
    # during a scrape and only separated by the refresh pass at the end of the
    # run, so a constraint here would abort the scrape rather than be satisfied
    # a moment later. `build_display_names` owns the guarantee instead.
    display_name: Mapped[str] = mapped_column(
        String,
        index=True,
        default=lambda ctx: ctx.get_current_parameters()["full_name"],
    )
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
    # The site's own `playerid`. It is scoped to this one competition-instance
    # roster entry rather than to the person, which is why it lives here and
    # not on Player — see db/identity.py.
    source_player_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
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
    # How a final game reached its result. NULL unless status == "final".
    #
    #   "played"      — a full game with a scored box score (99.9% carry an
    #                   inning-by-inning line score and hit totals).
    #   "forfeit"     — awarded without play, 7-0 or 0-7, no line score.
    #   "result_only" — genuinely contested and the result recorded, but no
    #                   scoresheet was ever entered: a real score like 14-8
    #                   with no innings, no line score and no hits.
    #
    # The distinction exists because the two things a game can supply come
    # apart. A forfeit is a real *result* — the federation's own standings
    # count it — but contains no baseball, so it must never reach a batting
    # line, a run environment or a park-neutral rate stat. Filtering only on
    # status would force a choice between losing 545 real results from the
    # standings and inventing 7-0 offence in the league averages.
    #
    # Counting these was verified against the site's own published standings
    # rather than assumed: including them takes exact agreement on team
    # records from 121 of 436 team-seasons to 231, and reproduces the 2026
    # Division 3 Central table exactly (Milton Keynes 20-0, Oxford 12-10,
    # Cambridge 10-10), which the played-games-only count gets wrong for four
    # of its five teams.
    # Defaulted from status so a Game built without it — tests, ad-hoc
    # scripts — still describes an ordinary played game rather than falling
    # out of every "played" filter. The scraper always sets it explicitly.
    result_type: Mapped[str | None] = mapped_column(
        String,
        index=True,
        nullable=True,
        default=lambda ctx: (
            "played" if ctx.get_current_parameters().get("status") == "final" else None
        ),
    )
    venue: Mapped[str | None] = mapped_column(String, nullable=True)

    # Which division this game counted toward. Non-null only when both teams
    # belong to the same division — a playoff between division winners
    # genuinely belongs to no single division, and those rows keep NULL here
    # rather than being arbitrarily attributed to one side. Resolved from
    # team membership by scraper/scrape_standings.py:resolve_game_divisions
    # after divisions are known, not written by the schedule scrape itself.
    division_id: Mapped[int | None] = mapped_column(
        ForeignKey("divisions.id"), index=True, nullable=True
    )
    # The raw `wbsc_tournament_group_id` from the schedule payload, kept as
    # the scraped fact behind division_id. Nullable for the seasons the site
    # never tagged (all of 2021, part of 2024).
    source_group_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    # "regular" or "playoff". Derived in scraper/scrape_schedule.py rather
    # than read from a single field, because no single field is reliable:
    # playoff games usually sit on a different wbsc_tournament_round_id from
    # the season's main one, but 2024 files 327 plainly-regular-season games
    # on off-rounds too, so the game's own label breaks the tie in both
    # directions. See _game_phase for the exact rule.
    phase: Mapped[str] = mapped_column(String, default="regular", index=True)

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


class FieldingGameLine(Base):
    """One row per (game, player, position played) — the per-position
    breakdown of the fielding totals BattingGameLine stores for the same
    player-game: summing these across positions reproduces that row's
    field_po/field_a/field_e/field_dp exactly, for every player who has such a
    row.

    It is a superset of them, not a mirror. BattingGameLine is only written
    when a player actually batted (pa or ab > 0), so a defensive substitute
    who never came to the plate has no batting row at all — 5,088 player-games
    across the scraped corpus, carrying 414 errors that were previously stored
    nowhere. Those get fielding rows here regardless, which is why the two
    tables' league-wide fielding totals differ by exactly that residue.

    This table exists because the box score's own `pos` is a *slash-joined
    path* of the positions occupied during one stint ("SS/P", "2B/P/P"), while
    the fielding counts on that record are a single total — so the raw payload
    attributes 19% of errors to no single position. See
    scraper/scrape_boxscores.py:_extract_fielding_lines for the attribution
    rule (narrative E<n> tokens split a multi-position record's errors;
    PO/A/DP go to the first position named) and docs/fielding_metrics_plan.md
    for the measurements behind it.

    Populated only by scraper/; `position` is "UNK" for the residual errors
    that neither source could place.
    """

    __tablename__ = "fielding_game_lines"
    __table_args__ = (UniqueConstraint("game_id", "player_season_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    player_season_id: Mapped[int] = mapped_column(ForeignKey("player_seasons.id"), index=True)
    team_season_id: Mapped[int] = mapped_column(ForeignKey("team_seasons.id"), index=True)

    position: Mapped[str] = mapped_column(String, index=True)
    appearances: Mapped[int] = mapped_column(Integer, default=0)
    po: Mapped[int] = mapped_column(Integer, default=0)
    a: Mapped[int] = mapped_column(Integer, default=0)
    e: Mapped[int] = mapped_column(Integer, default=0)
    dp: Mapped[int] = mapped_column(Integer, default=0)

    # Battery stats, only meaningful at C (and sba at P, whom this league's
    # scorers charge with a share of the steals allowed). `sba` is stolen
    # bases *allowed* and excludes runners thrown out, so a catcher's attempts
    # against are sba + csb — verified against the opposing team's own SB/CS
    # totals, see docs/fielding_metrics_plan.md.
    sba: Mapped[int] = mapped_column(Integer, default=0)
    csb: Mapped[int] = mapped_column(Integer, default=0)
    pb: Mapped[int] = mapped_column(Integer, default=0)


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


class FieldingSeasonStats(Base):
    """One row per (player_season, position) — FieldingGameLine summed over a
    season. Team-level "errors by position" is summed from these at read time
    in app/components/data_access.py, the same way team batting/pitching
    totals are, rather than materialized as a third table."""

    __tablename__ = "fielding_season_stats"
    __table_args__ = (UniqueConstraint("player_season_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_season_id: Mapped[int] = mapped_column(ForeignKey("player_seasons.id"), index=True)
    position: Mapped[str] = mapped_column(String, index=True)

    games: Mapped[int] = mapped_column(Integer, default=0)
    appearances: Mapped[int] = mapped_column(Integer, default=0)
    po: Mapped[int] = mapped_column(Integer, default=0)
    a: Mapped[int] = mapped_column(Integer, default=0)
    e: Mapped[int] = mapped_column(Integer, default=0)
    dp: Mapped[int] = mapped_column(Integer, default=0)

    # See FieldingGameLine — attempts against a catcher are sba + csb.
    sba: Mapped[int] = mapped_column(Integer, default=0)
    csb: Mapped[int] = mapped_column(Integer, default=0)
    pb: Mapped[int] = mapped_column(Integer, default=0)
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


class DivisionContext(Base):
    """The same self-calibrated inputs as LeagueSeasonContext, computed over
    one division's own games instead of the whole league-season.

    A separate table rather than a nullable `division_id` on
    LeagueSeasonContext, for two reasons. Practically, SQLite treats NULLs as
    distinct in a unique index, so a `(league_season_id, division_id)` key
    could never upsert the league-wide row — it would insert a duplicate on
    every recompute. Conceptually, the two are genuinely different questions
    and the app shows both: LeagueSeasonContext answers "how does this player
    compare to the league" and stays exactly as it always was, while this
    answers "how does this player compare to the opponents they actually
    faced". Neither supersedes the other.

    That distinction matters because divisions inside one league are not the
    same run environment — 2026 Division 4 ranges from 11.34 runs per team
    per game (North, league wOBA .513) to 7.52 (London, .415). Measuring
    everyone against the pooled league mean misstates both ends. But
    measuring everyone against their *own* division's mean instead would
    erase the difference entirely, since a weak division's hitters would be
    judged against weak pitching and come out looking average. Only keeping
    both scales lets the app say which of the two effects it is showing.
    """

    __tablename__ = "division_context"

    id: Mapped[int] = mapped_column(primary_key=True)
    division_id: Mapped[int] = mapped_column(
        ForeignKey("divisions.id"), unique=True, index=True
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
    # How much data this division's own calibration rests on, so the app can
    # refuse to show a division-relative figure built on a handful of games
    # (2026 Division 4 Central plays 36; a rain-shortened group could play
    # far fewer). Callers check these rather than re-deriving them.
    games: Mapped[int] = mapped_column(Integer, default=0)
    pa: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class TeamStrength(Base):
    """Bradley-Terry rating for one team's season, fitted on the games it
    actually played — see stats/team_strength.py.

    Exists because a bare win-loss record is not comparable even between two
    teams in the *same* division: the schedules are unbalanced. In 2026's
    Division 3 Central, Milton Keynes played the bottom team six times and
    second-placed Cambridge only twice, so their 18-0 and Cambridge's 10-4
    were built against materially different opposition.

    **`rating` is comparable only within one division.** Divisions play no
    regular-season games against each other at all, so nothing in the data
    fixes their relative level, and each division's ratings are centred on
    its own mean by construction. Comparing a rating across divisions would
    silently assert the two divisions are equally strong — which is the open
    question, not an answer. Cross-division offsets are a separate exercise
    resting on players who appear in more than one division.
    """

    __tablename__ = "team_strength"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_season_id: Mapped[int] = mapped_column(
        ForeignKey("team_seasons.id"), unique=True, index=True
    )
    # Log-odds strength, centred on this team's own division. 0 is a
    # division-average team; +1 means beating that average team about 73% of
    # the time at a neutral site.
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_se: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Schedule difficulty: how much stronger the opponents actually faced
    # were than the ones a perfectly balanced schedule would have given.
    # Positive is a harder draw than the division's other teams average.
    # Measured against that balanced baseline rather than as a bare mean
    # opponent rating, because a team never plays itself — so under a raw
    # mean the best team in any division automatically appears to have had
    # the easiest schedule, which would corrupt the exact comparison this
    # exists to support. Zero for a balanced round robin, by construction.
    sos: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Win probability against an average opponent from this team's division
    # at a neutral site — `rating` on a scale people can read.
    expected_win_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    games: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    ties: Mapped[int] = mapped_column(Integer, default=0)

    # Fixtures still to play, from the published schedule. Mid-season these
    # are what stop a rating being read as a finished verdict: a team 18-0
    # through 18 of 24 games has not won the division yet, and the app says
    # so rather than presenting the same number it will present in October.
    games_remaining: Mapped[int] = mapped_column(Integer, default=0)
    # Schedule difficulty of those remaining fixtures, on the same scale as
    # `sos`. This is the half of strength-of-schedule that only exists while
    # a season is live, and it can diverge sharply from the games already
    # played — one team may have had the contenders early and the other may
    # have them all to come.
    sos_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Fit diagnostics. Both describe the whole league-season rather than this
    # one team, and are denormalised onto each row so a row explains itself —
    # the same reasoning as BattingTrueTalent.stabilization_pa.
    home_advantage: Mapped[float | None] = mapped_column(Float, nullable=True)
    ridge_lambda: Mapped[float | None] = mapped_column(Float, nullable=True)
    lambda_self_calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
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
