"""Scrape one competition-year's standings page for its regional divisions.

This is the authoritative source for *which* division a team played in, and
for the division's published name. It covers every scraped league-season
including 2021, whereas the schedule payload's `wbsc_tournament_group_id`
(the only other signal) is absent for all of 2021 and partial for 2024.

The standings page is plain server-rendered HTML rather than an Inertia
`data-page` blob — one `<div class="box-container">` per division, holding an
`<h3>` with the division's name and a table of team links whose hrefs carry
the site's own `teamid`. Membership is therefore matched by *id*, not by
name: the two pages spell the same club differently ("Leeds Locos" on
standings vs "Leeds Locos 1" in the schedule, "Kent Buccaneers (AA)" vs
"Kent Buccaneers"), so name matching would drop teams silently.

Measured coverage across the 25 scraped league-seasons: 426 of 436
team_seasons resolve to a division. The residue is teams that appear in
fixtures but never in a published standings table; they keep a NULL
division_id rather than being guessed at.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from config import BASE_URL
from db.models import Division, Game, TeamSeason
from db.upsert import upsert
from scraper.discovery import resolve_fetch_code
from scraper.http_client import fetch_html

_TEAM_HREF_RE = re.compile(r"/teams/(\d+)")


@dataclass
class DivisionBlock:
    """One division as published on the standings page."""

    name: str
    source_team_ids: list[int] = field(default_factory=list)
    sort_order: int = 0


class _StandingsParser(HTMLParser):
    """Pulls (division name, team ids) out of the standings markup.

    A real parser rather than string splitting for the same reason
    http_client.extract_inertia_page uses one: the page is machine-generated
    HTML whose attribute values contain quotes and angle brackets, and it
    carries team links outside the standings tables too (breadcrumbs, the
    event nav). Team ids are only collected while inside a <table> within a
    box-container, which excludes those.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[DivisionBlock] = []
        self._current: DivisionBlock | None = None
        self._in_heading = False
        self._table_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()

        if tag == "div" and "box-container" in classes:
            # Divisions are siblings, so a new box-container closes the
            # previous one. Nested divs inside a block are ignored entirely —
            # we never need to know when a block ends, only when the next
            # begins.
            self._current = DivisionBlock(name="", sort_order=len(self.blocks))
            self.blocks.append(self._current)
            self._table_depth = 0
            return

        if self._current is None:
            return

        if tag == "h3" and not self._current.name:
            self._in_heading = True
        elif tag == "table":
            self._table_depth += 1
        elif tag == "a" and self._table_depth > 0:
            match = _TEAM_HREF_RE.search(attr.get("href") or "")
            if match:
                team_id = int(match.group(1))
                if team_id not in self._current.source_team_ids:
                    self._current.source_team_ids.append(team_id)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._in_heading = False
        elif tag == "table" and self._table_depth > 0:
            self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_heading and self._current is not None:
            self._current.name = (self._current.name + data).strip()


def parse_standings_divisions(html: str) -> list[DivisionBlock]:
    """Extract the divisions from a standings page. Pure, so it can be
    tested against fixture HTML without touching the network or the DB.

    Blocks with no team links are dropped: the page renders empty
    box-containers for layout in some seasons.
    """
    parser = _StandingsParser()
    parser.feed(html)
    blocks = [b for b in parser.blocks if b.source_team_ids]
    # Re-number after filtering so sort_order stays contiguous and matches
    # the order the site actually displays.
    for i, block in enumerate(blocks):
        block.sort_order = i
        if not block.name:
            # The site has always supplied a heading in the scraped corpus;
            # this keeps a nameless block addressable rather than colliding
            # with another on the (league_season_id, name) unique key.
            block.name = f"Division {i + 1}"
    return blocks


def scrape_standings(
    league_code: str,
    year: int,
    session: Session,
    *,
    league_season_id: int,
    force_refresh: bool = False,
    is_current_season: bool = True,
) -> int:
    """Scrape one competition-year's divisions and assign team membership.

    Returns the number of divisions written. Must run *after*
    scrape_schedule for the same competition-year, since it attaches
    divisions to the TeamSeason rows that scrape creates.
    """
    fetch_code = resolve_fetch_code(league_code, year)
    slug = f"{year}-{fetch_code}"
    url = f"{BASE_URL}/en/events/{slug}/standings"
    html = fetch_html(
        url,
        "standings",
        session=session,
        source_id=slug,
        force_refresh=force_refresh,
        is_current_season=is_current_season,
    )
    blocks = parse_standings_divisions(html)
    return apply_divisions(session, league_season_id, blocks)


def apply_divisions(
    session: Session, league_season_id: int, blocks: list[DivisionBlock]
) -> int:
    """Write `blocks` as this league_season's divisions and attach teams.

    Split out from the fetch so the offline backfill
    (scripts/backfill_divisions.py) can replay cached responses through the
    identical code path.

    Membership is cleared before being reassigned, and divisions that no
    longer appear on the page are deleted, so a re-run after the site edits
    its groupings converges rather than accumulating stale rows — the same
    reasoning as FieldingGameLine's delete-then-insert, and for the same
    reason: which rows a run produces depends on a rule that may change.
    """
    if not blocks:
        return 0

    team_season_by_source = {
        source_id: ts_id
        for source_id, ts_id in session.execute(
            select(TeamSeason.source_team_id, TeamSeason.id).where(
                TeamSeason.league_season_id == league_season_id
            )
        )
    }

    # Detach everything first: a team that moved between divisions must not
    # keep its old one, and a division about to be deleted must have no
    # referents left.
    _clear_division_links(session, league_season_id)

    written_ids: list[int] = []
    for block in blocks:
        division_id = upsert(
            session,
            Division,
            {
                "league_season_id": league_season_id,
                "name": block.name,
                "sort_order": block.sort_order,
                # Filled in separately from game data — the standings page
                # carries no group id.
                "source_group_id": None,
            },
            ["league_season_id", "name"],
        )
        written_ids.append(division_id)

        team_season_ids = [
            team_season_by_source[src]
            for src in block.source_team_ids
            if src in team_season_by_source
        ]
        if team_season_ids:
            session.execute(
                update(TeamSeason)
                .where(TeamSeason.id.in_(team_season_ids))
                .values(division_id=division_id)
            )

    _delete_stale_divisions(session, league_season_id, keep=written_ids)
    link_division_group_ids(session, league_season_id)
    resolve_game_divisions(session, league_season_id)
    session.commit()
    return len(written_ids)


def _clear_division_links(session: Session, league_season_id: int) -> None:
    session.execute(
        update(TeamSeason)
        .where(TeamSeason.league_season_id == league_season_id)
        .values(division_id=None)
    )
    session.execute(
        update(Game)
        .where(Game.league_season_id == league_season_id)
        .values(division_id=None)
    )


def _delete_stale_divisions(session: Session, league_season_id: int, keep: list[int]) -> None:
    stale = session.execute(
        select(Division.id).where(
            Division.league_season_id == league_season_id,
            Division.id.notin_(keep),
        )
    ).scalars().all()
    for division_id in stale:
        session.execute(Division.__table__.delete().where(Division.id == division_id))


def link_division_group_ids(session: Session, league_season_id: int) -> int:
    """Record each division's `wbsc_tournament_group_id`, where the schedule
    payload supplied one.

    Derived rather than scraped: the standings page has no group id and the
    schedule has no division name, so the two are joined through the teams
    they share. A division's group id is the one carried by the regular-season
    games between its own members — taken as the most common such value rather
    than the first, so a single mistagged game can't rename the division's
    group.

    Returns the number of divisions that got one.
    """
    rows = session.execute(
        select(
            TeamSeason.division_id,
            Game.source_group_id,
            func.count(Game.id),
        )
        .join(Game, Game.home_team_season_id == TeamSeason.id)
        .where(
            TeamSeason.league_season_id == league_season_id,
            TeamSeason.division_id.isnot(None),
            Game.source_group_id.isnot(None),
            Game.phase == "regular",
        )
        .group_by(TeamSeason.division_id, Game.source_group_id)
    ).all()

    best: dict[int, tuple[int, int]] = {}
    for division_id, group_id, count in rows:
        if division_id not in best or count > best[division_id][1]:
            best[division_id] = (group_id, count)

    for division_id, (group_id, _) in best.items():
        session.execute(
            update(Division).where(Division.id == division_id).values(source_group_id=group_id)
        )
    return len(best)


def resolve_game_divisions(session: Session, league_season_id: int) -> int:
    """Set Game.division_id for games played entirely within one division.

    A game belongs to a division only when *both* teams do. Playoffs between
    division winners genuinely belong to no single division and keep NULL
    here rather than being attributed to the home side — which would
    otherwise quietly inflate one division's game count and, through
    DivisionContext, its run environment.

    Returns the number of games attributed.
    """
    home = TeamSeason.__table__.alias("home_ts")
    away = TeamSeason.__table__.alias("away_ts")

    rows = session.execute(
        select(Game.id, home.c.division_id)
        .join(home, home.c.id == Game.home_team_season_id)
        .join(away, away.c.id == Game.away_team_season_id)
        .where(
            Game.league_season_id == league_season_id,
            home.c.division_id.isnot(None),
            home.c.division_id == away.c.division_id,
        )
    ).all()

    by_division: dict[int, list[int]] = {}
    for game_id, division_id in rows:
        by_division.setdefault(division_id, []).append(game_id)

    for division_id, game_ids in by_division.items():
        session.execute(
            update(Game).where(Game.id.in_(game_ids)).values(division_id=division_id)
        )
    return len(rows)
