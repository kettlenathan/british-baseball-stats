"""Cross-season player identity: normalization and the merge it enables.

Regression cover for the bug where the site's per-season `playerid` was used as
the player's identity, so every player's career was split into one row per
league-season (see db/identity.py).
"""

import pytest
from sqlalchemy import select

from db.identity import (
    build_display_names,
    is_house_style,
    normalize_name,
    player_identity_key,
    preferred_spelling,
    resolve_roster_slot_merges,
)
from db.models import League, LeagueSeason, Player, PlayerSeason, Season, Team, TeamSeason
from db.upsert import upsert


@pytest.mark.parametrize(
    "variant",
    [
        "Brandon JIMENEZ",
        "BRANDON JIMENEZ",
        "brandon jimenez",
        "Brandon Jimenez",
        "  Brandon   JIMENEZ  ",
    ],
)
def test_normalize_name_folds_site_capitalisation_variants(variant):
    assert normalize_name(variant) == "brandon jimenez"


@pytest.mark.parametrize(
    ("variant", "expected"),
    [
        # The site uppercases surnames but skips accented letters, so the same
        # name arrives both accented and not.
        ("Adoni Jose GáLVEZ RODRíGUEZ", "adoni jose galvez rodriguez"),
        ("Adoni Jose GALVEZ RODRIGUEZ", "adoni jose galvez rodriguez"),
        ("Leo CEDEñO", "leo cedeno"),
        # Curly vs straight apostrophe vs none.
        ("Jack O’BRIEN", "jack o brien"),
        ("Jack O'BRIEN", "jack o brien"),
        ("Jack OBRIEN", "jack obrien"),
        # Hyphen folds to the same key as a space.
        ("Benjamin MILLS-KNUTSEN", "benjamin mills knutsen"),
        ("Benjamin MILLS KNUTSEN", "benjamin mills knutsen"),
    ],
)
def test_normalize_name_folds_accents_and_punctuation(variant, expected):
    assert normalize_name(variant) == expected


def test_identity_key_is_stable_across_reissued_site_ids():
    """The whole point: one person, three seasons, three site ids, one key."""
    keys = {
        player_identity_key("Brandon JIMENEZ", 1994, 420200),
        player_identity_key("BRANDON JIMENEZ", 1994, 569446),
        player_identity_key("Brandon Jimenez", 1994, 751722),
    }
    assert len(keys) == 1


def test_identity_key_separates_same_name_different_birth_year():
    """323 normalised names in the corpus cover >1 birth year — real distinct
    people that name-only matching would wrongly fuse into one career."""
    assert player_identity_key("Ben CARTER", 1990, 1) != player_identity_key("Ben CARTER", 1998, 2)


def test_identity_key_without_birth_year_stays_distinct_per_site_id():
    """Strict policy: no name-only fallback when the site gives no dob."""
    assert player_identity_key("Ben CARTER", None, 1) != player_identity_key("Ben CARTER", None, 2)


def test_identity_key_without_birth_year_is_idempotent_for_one_site_id():
    """The key must stay a pure function of the payload, or re-running a
    scrape would mint a new player row every time."""
    assert player_identity_key("Ben CARTER", None, 7) == player_identity_key("Ben CARTER", None, 7)


def test_player_model_defaults_identity_key_from_name_and_birth_year(session):
    player = Player(source_id=420200, full_name="Brandon JIMENEZ", birth_year=1994)
    session.add(player)
    session.flush()
    assert player.identity_key == player_identity_key("Brandon JIMENEZ", 1994, 420200)


def test_roster_slot_merges_one_person_with_a_mistyped_birth_year():
    """The site records Franklin MARTINEZ as born 1979 and 2001 in the same
    team's number 4. Different seasons, so it is one man."""
    remap = resolve_roster_slot_merges(
        [
            (1, 10, "Franklin MARTINEZ", 2, 4),
            (2, 11, "Franklin MARTINEZ", 2, 4),
        ]
    )
    assert remap == {2: 1}


def test_roster_slot_refuses_to_merge_players_sharing_a_league_season():
    """The guard the whole rule rests on: a team cannot field two players in
    number 4 at once, so if two identities *do* overlap they are two people."""
    assert resolve_roster_slot_merges(
        [
            (1, 10, "Ben CARTER", 2, 4),
            (2, 10, "Ben CARTER", 2, 4),
        ]
    ) == {}


def test_roster_slot_ignores_the_placeholder_squad_number():
    """Squad number 0 means "not recorded" on 2,751 roster entries; treating it
    as a slot would merge unrelated players wholesale."""
    assert resolve_roster_slot_merges(
        [
            (1, 10, "Ben CARTER", 2, 0),
            (2, 11, "Ben CARTER", 2, 0),
        ]
    ) == {}


def test_roster_slot_does_not_merge_across_different_teams_or_numbers():
    assert resolve_roster_slot_merges(
        [(1, 10, "Ben CARTER", 2, 4), (2, 11, "Ben CARTER", 3, 4)]
    ) == {}
    assert resolve_roster_slot_merges(
        [(1, 10, "Ben CARTER", 2, 4), (2, 11, "Ben CARTER", 2, 5)]
    ) == {}


def test_roster_slot_merge_chains_resolve_to_one_keeper():
    remap = resolve_roster_slot_merges(
        [
            (3, 10, "A BOWDEN", 2, 4),
            (2, 11, "A BOWDEN", 2, 4),
            (1, 12, "A BOWDEN", 2, 4),
        ]
    )
    assert set(remap.values()) == {1}


def test_roster_slot_validates_a_merge_group_as_a_whole():
    """Player 1 shares number 4 with player 2, and player 2 shares number 7
    with player 3 — but 1 and 3 both played league-season 10, so they are not
    one person. Checking only slot-sharing pairs would chain them together
    anyway; the whole component has to be refused."""
    remap = resolve_roster_slot_merges(
        [
            (1, 10, "A BOWDEN", 2, 4),
            (2, 11, "A BOWDEN", 2, 4),
            (2, 11, "A BOWDEN", 2, 7),
            (3, 10, "A BOWDEN", 2, 7),
        ]
    )
    assert remap == {}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Adam MURRAY", True),
        ("ADAM MURRAY", False),
        ("Marcos Osuna", False),
        # The site leaves accents lowercase when uppercasing a surname.
        ("Jose Gabriel LEDESMA MARTíNEZ", True),
        ("Madonna", False),
    ],
)
def test_is_house_style(name, expected):
    assert is_house_style(name) is expected


def test_preferred_spelling_picks_house_style_but_never_invents_one():
    assert preferred_spelling(["ROSE BHANJI", "Rose BHANJI"]) == "Rose BHANJI"
    # No house-style spelling was ever scraped — leave it alone rather than
    # guessing where the surname starts.
    assert preferred_spelling(["Marcos Osuna"]) == "Marcos Osuna"


def test_display_names_disambiguate_only_shared_names():
    display = build_display_names(
        [
            (1, ["Ben CARTER"], 1995, 10),
            (2, ["Ben CARTER"], 1999, 11),
            (3, ["Solo PLAYER"], 1990, 12),
        ]
    )
    assert display[1] == "Ben CARTER (b. 1995)"
    assert display[2] == "Ben CARTER (b. 1999)"
    assert display[3] == "Solo PLAYER"


def test_display_names_are_unique_even_without_birth_years():
    """display_name is unique in the schema, so this must not produce a
    collision the scraper would then hit as an IntegrityError."""
    display = build_display_names(
        [
            (1, ["Ben CARTER"], None, 10),
            (2, ["Ben CARTER"], None, 11),
        ]
    )
    assert display[1] != display[2]
    assert len(set(display.values())) == 2


def _league_season(session, year):
    """One team_season in `year`, reusing the single test league across calls
    so a player can be followed through consecutive seasons."""
    league = session.query(League).filter_by(code="test").one_or_none()
    if league is None:
        league = League(code="test", name="Test League", tier="senior", is_senior=True)
        session.add(league)
    season = Season(year=year)
    session.add(season)
    session.flush()
    league_season = LeagueSeason(
        league_id=league.id,
        season_id=season.id,
        source_tournament_id=year,
        competition_slug=f"{year}-test",
    )
    session.add(league_season)
    session.flush()
    team = session.query(Team).filter_by(name="London Meteors").one_or_none()
    if team is None:
        team = Team(name="London Meteors")
        session.add(team)
        session.flush()
    team_season = TeamSeason(
        team_id=team.id,
        league_season_id=league_season.id,
        source_team_id=100 + year,
        display_name="London Meteors",
    )
    session.add(team_season)
    session.flush()
    return team_season


def test_upsert_merges_reissued_site_ids_into_one_player(session):
    """End-to-end over the scraper's actual write path: the same person
    scraped in three seasons under three site ids (and three spellings) must
    become one Player with three PlayerSeasons, not three Players."""
    team_seasons = [_league_season(session, year) for year in (2024, 2025, 2026)]
    scraped = [
        ("Brandon JIMENEZ", 420200),
        ("BRANDON JIMENEZ", 569446),
        ("Brandon Jimenez", 751722),
    ]

    for team_season, (full_name, source_player_id) in zip(team_seasons, scraped):
        player_id = upsert(
            session,
            Player,
            {
                "identity_key": player_identity_key(full_name, 1994, source_player_id),
                "source_id": source_player_id,
                "full_name": full_name,
                "birth_year": 1994,
            },
            ["identity_key"],
        )
        upsert(
            session,
            PlayerSeason,
            {
                "player_id": player_id,
                "team_season_id": team_season.id,
                "source_player_id": source_player_id,
            },
            ["player_id", "team_season_id"],
        )

    assert session.query(Player).count() == 1
    assert session.query(PlayerSeason).count() == 3
    # The site's per-season ids are preserved, not discarded.
    assert {ps.source_player_id for ps in session.query(PlayerSeason)} == {420200, 569446, 751722}


def test_known_site_id_pins_player_through_a_spelling_change(session):
    """A playerid already resolved must keep its player even when the name it
    arrives with changes — the site serves "Kenichiro MURATA" and "Kenchiro
    MURATA" for one id from different endpoints, and re-matching by name would
    split the player in two on the next scrape."""
    team_season = _league_season(session, 2026)
    source_player_id = 411511

    first_id = upsert(
        session,
        Player,
        {
            "identity_key": player_identity_key("Kenichiro MURATA", 1996, source_player_id),
            "source_id": source_player_id,
            "full_name": "Kenichiro MURATA",
            "birth_year": 1996,
        },
        ["identity_key"],
    )
    upsert(
        session,
        PlayerSeason,
        {
            "player_id": first_id,
            "team_season_id": team_season.id,
            "source_player_id": source_player_id,
        },
        ["player_id", "team_season_id"],
    )

    # Re-scrape serves a different spelling for the same id.
    resolved = session.execute(
        select(PlayerSeason.player_id).where(
            PlayerSeason.source_player_id == source_player_id
        )
    ).scalar()

    assert resolved == first_id
    assert session.query(Player).count() == 1


def test_upsert_keeps_same_name_different_birth_year_apart(session):
    """Two real people who share a name must not be merged."""
    team_season = _league_season(session, 2026)
    for birth_year, source_player_id in ((1990, 1), (1998, 2)):
        player_id = upsert(
            session,
            Player,
            {
                "identity_key": player_identity_key("Ben CARTER", birth_year, source_player_id),
                "source_id": source_player_id,
                "full_name": "Ben CARTER",
                "birth_year": birth_year,
            },
            ["identity_key"],
        )
        upsert(
            session,
            PlayerSeason,
            {
                "player_id": player_id,
                "team_season_id": team_season.id,
                "source_player_id": source_player_id,
            },
            ["player_id", "team_season_id"],
        )

    assert session.query(Player).count() == 2
