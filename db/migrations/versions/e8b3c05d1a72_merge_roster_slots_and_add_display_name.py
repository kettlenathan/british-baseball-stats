"""merge players sharing a roster slot, and add a disambiguated display_name

Revision ID: e8b3c05d1a72
Revises: d4f1a9b2c7e3
Create Date: 2026-08-11 23:20:00.000000

Two follow-ups to the identity re-keying in d4f1a9b2c7e3:

1. The site's `dob` is not merely placeholder-prone, it is sometimes *wrong*
   ("Franklin MARTINEZ" is recorded as born both 1979 and 2001 while wearing
   number 4 for one team), so keying on name+birth year alone still split some
   players. Players sharing a roster slot — same name, same team, same squad
   number — across seasons they never both appear in are merged here.

2. `Player.display_name` is added so the app can tell apart two different
   people who *do* share a name. It resolves players by name string, so before
   this "Ben CARTER" read as one man with 741 PA when he is four men.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from db.identity import (
    build_display_names,
    preferred_spelling,
    resolve_roster_slot_merges,
)


# revision identifiers, used by Alembic.
revision: str = 'e8b3c05d1a72'
down_revision: Union[str, Sequence[str], None] = 'd4f1a9b2c7e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FACT_REFS = [
    ('batting_game_lines', 'player_season_id'),
    ('pitching_game_lines', 'player_season_id'),
    ('fielding_game_lines', 'player_season_id'),
    ('plate_appearances', 'batter_player_season_id'),
    ('plate_appearances', 'pitcher_player_season_id'),
]

_DERIVED_REFS = [
    ('batting_season_stats', 'player_season_id'),
    ('pitching_season_stats', 'player_season_id'),
    ('fielding_season_stats', 'player_season_id'),
    ('batting_war', 'player_season_id'),
    ('pitching_war', 'player_season_id'),
    ('batting_true_talent', 'player_season_id'),
    ('pitching_true_talent', 'player_season_id'),
    ('batter_spray_season_stats', 'player_season_id'),
    ('batter_pitcher_matchups', 'batter_player_season_id'),
    ('batter_pitcher_matchups', 'pitcher_player_season_id'),
]


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    op.add_column('players', sa.Column('display_name', sa.String(), nullable=True))

    roster = conn.execute(sa.text(
        "SELECT ps.player_id, ts.league_season_id, p.full_name, ts.team_id, ps.jersey_number "
        "FROM player_seasons ps "
        "JOIN players p ON p.id = ps.player_id "
        "JOIN team_seasons ts ON ts.id = ps.team_season_id"
    )).fetchall()
    remap = resolve_roster_slot_merges([tuple(row) for row in roster])

    # Re-assert the property the whole rule rests on, rather than trusting the
    # one-off measurement that motivated it: no two players being merged may
    # ever have appeared in the same league-season. If the data changes shape,
    # this must fail the migration rather than silently fuse two real players.
    seasons_by_player: dict[int, set[int]] = {}
    for player_id, league_season_id, *_ in roster:
        seasons_by_player.setdefault(player_id, set()).add(league_season_id)
    for absorbed, keeper in remap.items():
        overlap = seasons_by_player[absorbed] & seasons_by_player[keeper]
        if overlap:
            raise RuntimeError(
                f"refusing to merge player {absorbed} into {keeper}: both appear "
                f"in league_season(s) {sorted(overlap)}"
            )

    # Keep the best spelling the site ever used for each merged player, so a
    # career doesn't end up labelled with a stray "ADAM MURRAY" just because
    # that row happened to have the lowest id.
    spellings: dict[int, list[str]] = {}
    for absorbed, keeper in remap.items():
        spellings.setdefault(keeper, []).append(
            conn.execute(
                sa.text("SELECT full_name FROM players WHERE id = :i"), {"i": absorbed}
            ).scalar()
        )

    for absorbed, keeper in remap.items():
        rows = conn.execute(
            sa.text("SELECT id, team_season_id FROM player_seasons WHERE player_id = :a"),
            {"a": absorbed},
        ).fetchall()
        for loser, team_season_id in rows:
            existing = conn.execute(
                sa.text(
                    "SELECT id FROM player_seasons "
                    "WHERE player_id = :p AND team_season_id = :t"
                ),
                {"p": keeper, "t": team_season_id},
            ).scalar()
            if existing is None:
                conn.execute(
                    sa.text("UPDATE player_seasons SET player_id = :k WHERE id = :l"),
                    {"k": keeper, "l": loser},
                )
                continue
            for table, column in _FACT_REFS:
                conn.execute(
                    sa.text(f"UPDATE {table} SET {column} = :k WHERE {column} = :l"),
                    {"k": existing, "l": loser},
                )
            for table, column in _DERIVED_REFS:
                conn.execute(
                    sa.text(f"DELETE FROM {table} WHERE {column} = :l"), {"l": loser}
                )
            conn.execute(sa.text("DELETE FROM player_seasons WHERE id = :l"), {"l": loser})
        conn.execute(sa.text("DELETE FROM players WHERE id = :a"), {"a": absorbed})

    for keeper, observed in spellings.items():
        current = conn.execute(
            sa.text("SELECT full_name FROM players WHERE id = :i"), {"i": keeper}
        ).scalar()
        if current is None:
            continue  # keeper was itself absorbed by a longer chain
        best = preferred_spelling([current, *[s for s in observed if s]])
        if best != current:
            conn.execute(
                sa.text("UPDATE players SET full_name = :n WHERE id = :i"),
                {"n": best, "i": keeper},
            )

    players = conn.execute(
        sa.text("SELECT id, full_name, birth_year, source_id FROM players")
    ).fetchall()
    for player_id, name in build_display_names(
        [(row[0], [row[1]], row[2], row[3]) for row in players]
    ).items():
        conn.execute(
            sa.text("UPDATE players SET display_name = :n WHERE id = :i"),
            {"n": name, "i": player_id},
        )

    # Not unique — see the column comment in db/models.py: a scrape inserts two
    # same-named players one at a time and only separates them in the refresh
    # pass at the end of the run.
    op.create_index('ix_players_display_name', 'players', ['display_name'], unique=False)
    with op.batch_alter_table('players') as batch:
        batch.alter_column('display_name', existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Lossy: the merged-away player rows cannot be reconstructed.
    op.drop_index('ix_players_display_name', table_name='players')
    op.drop_column('players', 'display_name')
