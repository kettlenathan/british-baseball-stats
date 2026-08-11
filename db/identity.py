"""Cross-season player identity.

The site issues a **fresh `playerid` for every competition-instance roster
entry**, so the same human gets a new id each season (and a second id if they
appear in two divisions in one year). This was originally recorded the other
way round -- `scraper/recon/findings.md` concluded `playerid` was "stable
platform-wide" from a single season's payloads, where the reissue is invisible
-- and the consequence was that every player's career was fragmented into one
`Player` row per league-season. Measured on the scraped corpus: **0 of 9,742
player rows appeared in more than one league-season.**

Cross-year identity is therefore resolved by name here, exactly as
`Team` identity already is (see `db/models.py:Team`), with `birth_year` as the
disambiguator -- the site supplies it (as `player.dob`, year only) on 98% of
rows, and it does real work: 323 normalised names in the corpus cover more than
one birth year. Some of those really are different people (there are four
distinct Ben Carters), and merging on name alone would fuse their careers;
others turn out to be one person whose `dob` was mistyped, which is what
`roster_slot_key` below exists to catch. Do not read a `dob` disagreement as
proof of two people -- it is only ever evidence.

The site's own per-season id is not discarded: it moves to
`PlayerSeason.source_player_id`, which is what it actually identifies.

Matching is deliberately **strict** -- name *and* birth year must both match.
When the site gives no `dob` we do not fall back to name-only matching (that
would risk fusing two same-named people on the thinnest evidence); the id is
folded into the key instead, so such a player stays a separate single-season
identity. Folding in the *id* rather than, say, a row count keeps the key a
pure function of the scraped payload, which is what makes re-running any scrape
idempotent.

`dob` alone is not enough, though, and not only because of the placeholders
described at `plausible_birth_year`: the site also records years that are
simply *wrong*. `Franklin MARTINEZ` appears on one team wearing number 4 with a
`dob` of both 1979 and 2001. `roster_slot_key` exists for exactly these -- a
squad number is a slot on one team's roster, and a team cannot field two
players wearing it in the same season, so the same name in the same slot across
*different* seasons is one person however their `dob` was typed. Measured on
the corpus: of 81 such same-name/same-team/same-number groups with conflicting
birth years, **none** ever appeared in the same league-season. The
co-occurrence check in `resolve_roster_slot_merges` is what keeps that true
rather than assumed -- it refuses to merge when two identities really do
overlap, which on the same corpus correctly blocked 9 groups.
"""

import re
import unicodedata

_NON_NAME_CHARS = re.compile(r"[^a-z0-9]+")

# The site's `dob` field carries placeholder junk alongside real years, and the
# two separate cleanly: observed values run 1940-2013 and then jump to 2017,
# 2021 (x38), 2023, 2025-2028 and 2078, with 1 (x61), 7 and 1900 below. Anything
# outside this window is treated as *missing* rather than as a birth year.
# This matters in both directions: a placeholder must not split one person
# across two keys, and — more dangerously — must not merge two people who share
# a name and the same placeholder (before this window, five seasons of
# "Tom SMITH" merged on `dob=1` alone).
MIN_PLAUSIBLE_BIRTH_YEAR = 1930
MAX_PLAUSIBLE_BIRTH_YEAR = 2015


def plausible_birth_year(birth_year: int | None) -> int | None:
    """The birth year if it can be a real one, else None."""
    if birth_year is None:
        return None
    if MIN_PLAUSIBLE_BIRTH_YEAR <= birth_year <= MAX_PLAUSIBLE_BIRTH_YEAR:
        return birth_year
    return None


def normalize_name(full_name: str) -> str:
    """Fold a display name to a match key.

    The site's own capitalisation is inconsistent in every direction --
    ``ADAM MURRAY``/``Adam MURRAY``, ``chris WARD``/``Chris WARD``,
    ``Adolfo YANES``/``Adolfo Yanes`` all denote one person. Its
    surname-uppercasing also skips accented letters, so a name can arrive both
    fully uppercased and with a stray lowercase vowel in it (``RODRIGUEZ`` vs
    ``RODRíGUEZ``, ``CEDENO`` vs ``CEDEÑO``/``CEDEñO``) -- hence accents are
    stripped rather than preserved. Punctuation folds to a space so the curly
    and straight apostrophes in ``O’BRIEN``/``O'BRIEN`` agree with each other
    and with ``OBRIEN``, and ``MILLS-KNUTSEN`` agrees with ``MILLS KNUTSEN``.

    Note this is a *match* key only -- the human-readable `Player.full_name` is
    kept exactly as scraped for display.
    """
    # NFKD splits accented letters into base + combining mark, which the
    # category filter below then drops (a-acute -> a, n-tilde -> n).
    decomposed = unicodedata.normalize("NFKD", full_name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = _NON_NAME_CHARS.sub(" ", stripped.lower())
    return " ".join(folded.split())


def player_identity_key(
    full_name: str,
    birth_year: int | None,
    source_player_id: int | None,
) -> str:
    """Build the value stored in `Player.identity_key` (its uniqueness key).

    Same normalised name + same birth year == same person, across every season
    and division. Without a *usable* birth year the player is kept distinct per
    site id (see the module docstring on why there is no name-only fallback,
    and `plausible_birth_year` on why junk years count as no birth year).
    """
    name = normalize_name(full_name)
    year = plausible_birth_year(birth_year)
    if year:
        return f"{name}|{year}"
    return f"{name}|nodob:{source_player_id}"


# Squad number 0 is the site's "no number recorded" placeholder (2,751 of 9,741
# roster entries), not a real number, so it identifies no slot at all.
_PLACEHOLDER_JERSEY = 0


def roster_slot_key(
    full_name: str,
    team_id: int,
    jersey_number: int | None,
) -> tuple[str, int, int] | None:
    """The roster slot a player-season occupies, or None if there isn't one.

    A slot is one team's squad number. Two player-seasons in the same slot with
    the same name are the same person *provided they never co-occur in a
    league-season* — see the module docstring, and note that the co-occurrence
    test is the caller's job (`resolve_roster_slot_merges`), because this
    function sees one player-season at a time.
    """
    if not jersey_number or jersey_number == _PLACEHOLDER_JERSEY:
        return None
    return (normalize_name(full_name), team_id, jersey_number)


def resolve_roster_slot_merges(
    player_seasons: list[tuple[int, int, str, int, int | None]],
) -> dict[int, int]:
    """Decide which players share a roster slot and should be merged.

    Takes `(player_id, league_season_id, full_name, team_id, jersey_number)`
    per player-season, and returns a ``{absorbed_player_id: keeper_player_id}``
    map. Kept free of any DB access so the rule is testable and so the
    migration and the scraper can share one implementation.

    A group is merged only when no two of its players appear in the same
    league-season. That guard is the whole basis for trusting the rule, so it
    is enforced here rather than assumed from a one-off measurement — if the
    data ever changes shape, the group is left alone instead of fusing two
    real players.
    """
    slots: dict[tuple[str, int, int], set[int]] = {}
    seasons_by_player: dict[int, set[int]] = {}
    for player_id, league_season_id, full_name, team_id, jersey_number in player_seasons:
        seasons_by_player.setdefault(player_id, set()).add(league_season_id)
        slot = roster_slot_key(full_name, team_id, jersey_number)
        if slot is None:
            continue
        slots.setdefault(slot, set()).add(player_id)

    # A player can share one slot with A and another slot with B, which makes
    # A, B and the player one group. Union them into connected components and
    # validate each component *as a whole*: checking only the pairs that share
    # a slot would happily merge A with B and B with C while A and C overlap.
    parent: dict[int, int] = {}

    def find(player_id: int) -> int:
        parent.setdefault(player_id, player_id)
        while parent[player_id] != player_id:
            parent[player_id] = parent[parent[player_id]]
            player_id = parent[player_id]
        return player_id

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    for players in slots.values():
        if len(players) < 2:
            continue
        ordered = sorted(players)
        for other in ordered[1:]:
            union(ordered[0], other)

    components: dict[int, set[int]] = {}
    for player_id in parent:
        components.setdefault(find(player_id), set()).add(player_id)

    remap: dict[int, int] = {}
    for members in components.values():
        if len(members) < 2:
            continue
        ordered = sorted(members)
        overlaps = any(
            seasons_by_player.get(ordered[i], set()) & seasons_by_player.get(ordered[j], set())
            for i in range(len(ordered))
            for j in range(i + 1, len(ordered))
        )
        if overlaps:
            # Two members played the same league-season, so this group is not
            # one person. Refuse the whole component rather than guessing which
            # subset is safe.
            continue
        keeper = ordered[0]
        for player_id in ordered[1:]:
            remap[player_id] = keeper
    return remap


def is_house_style(full_name: str) -> bool:
    """Whether a name is spelled the site's usual ``Firstname SURNAME`` way.

    True of 9,447 of 9,742 scraped spellings, so the odd ``ADAM MURRAY`` or
    ``Marcos Osuna`` reads as a glitch next to everyone else. Note the surname
    test tolerates the site's habit of skipping accented letters when
    uppercasing (``RODRíGUEZ``), which `str.isupper` alone would reject.
    """
    parts = full_name.split()
    if len(parts) < 2:
        return False
    # Judge case on ASCII letters only: the site leaves accented letters
    # lowercase when it uppercases a surname, so `RODRíGUEZ` is house style
    # even though `str.isupper()` disagrees.
    surname = [ch for ch in parts[-1] if ch.isascii() and ch.isalpha()]
    first = [ch for ch in parts[0] if ch.isascii() and ch.isalpha()]
    if not surname:
        return False
    return all(ch.isupper() for ch in surname) and not (
        first and all(ch.isupper() for ch in first)
    )


def preferred_spelling(spellings: list[str]) -> str:
    """Pick which of a player's observed spellings to show.

    Only ever returns something actually scraped — casing is never invented for
    a player the site only ever wrote one way, since ~170 names have no
    house-style spelling anywhere and guessing at where the surname ends would
    mangle the multi-part ones (``Kevin Andy NAVARRO JIMENEZ``).
    """
    ordered = sorted(set(spellings))
    for spelling in ordered:
        if is_house_style(spelling):
            return spelling
    return ordered[0]


def disambiguated_display_name(
    full_name: str,
    birth_year: int | None,
    source_player_id: int | None,
    name_is_shared: bool,
) -> str:
    """The name to show in the UI, unique per player when the name is shared.

    The app resolves a player by the *string* a user picked from a dropdown, so
    two different people sharing a name have their careers silently added
    together — `Ben CARTER` otherwise reads as one man with 741 PA when he is
    four. Appending the birth year is enough to separate them and tells the
    reader why there are two entries.
    """
    if not name_is_shared:
        return full_name
    year = plausible_birth_year(birth_year)
    if year:
        return f"{full_name} (b. {year})"
    # No usable birth year to distinguish them by; the site id at least keeps
    # the entries distinct and traceable back to the source.
    return f"{full_name} (ID {source_player_id})"


def build_display_names(
    players: list[tuple[int, list[str], int | None, int | None]],
) -> dict[int, str]:
    """Map ``player_id -> display_name`` for the whole players table.

    Takes `(player_id, observed_spellings, birth_year, source_player_id)`.
    Whether a name needs disambiguating is a property of the table rather than
    of one row, so this is computed in one pass over all players rather than
    per-row during a scrape. DB-free for the same reason the merge rule is:
    one implementation, shared by the migration and the scraper, testable
    without either.
    """
    chosen = {
        player_id: preferred_spelling(spellings)
        for player_id, spellings, _birth_year, _source_id in players
    }
    counts: dict[str, int] = {}
    for name in chosen.values():
        counts[name] = counts.get(name, 0) + 1

    display: dict[int, str] = {}
    for player_id, _spellings, birth_year, source_id in players:
        name = chosen[player_id]
        display[player_id] = disambiguated_display_name(
            name, birth_year, source_id, name_is_shared=counts[name] > 1
        )

    # This function is the only thing guaranteeing display names are unique
    # (the column deliberately carries no UNIQUE constraint — see
    # db/models.py), so settle any remaining tie here: two players can still
    # land on one string if they share a name *and* a birth year, which
    # identity_key should have merged but a roster-slot merge could recreate.
    seen: dict[str, int] = {}
    for player_id in sorted(display):
        name = display[player_id]
        if name in seen:
            display[player_id] = f"{name} (ID {player_id})"
        seen[display[player_id]] = player_id
    return display


def refresh_display_names(connection) -> int:
    """Recompute every player's `display_name`; returns how many changed.

    Cheap (one pass over a few thousand rows) and idempotent, so it simply runs
    at the end of a scrape rather than trying to patch names incrementally —
    adding one player can change whether an *existing* player's name is shared,
    so there is no purely local update.

    Deliberately uses Core SQL instead of the ORM: `db/models.py` imports this
    module for its column defaults, so importing `Player` here would be a
    circular import.
    """
    # Imported here rather than at module scope to keep this module importable
    # by anything that doesn't need SQLAlchemy at all.
    from sqlalchemy import text

    rows = connection.execute(
        text("SELECT id, full_name, birth_year, source_id, display_name FROM players")
    ).fetchall()
    wanted = build_display_names(
        [(row[0], [row[1]], row[2], row[3]) for row in rows]
    )
    current = {row[0]: row[4] for row in rows}

    changed = 0
    for player_id, name in wanted.items():
        if current.get(player_id) != name:
            connection.execute(
                text("UPDATE players SET display_name = :n WHERE id = :i"),
                {"n": name, "i": player_id},
            )
            changed += 1
    return changed
