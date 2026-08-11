"""resolve player identity by normalized name + birth year

Revision ID: d4f1a9b2c7e3
Revises: 9de866620f94
Create Date: 2026-08-11 22:40:00.000000

The site reissues `playerid` per competition-instance roster entry, so keying
`players` on it gave one row per player-season and no player had a career
spanning more than one season (measured: 0 of 9,742 rows appeared in two
league-seasons). This migration re-keys `players` on `identity_key`
(normalized name + birth year, see db/identity.py), merges the fragments, and
moves the site's per-season id to `player_seasons.source_player_id` where it
belongs.

On the corpus this was written against: 9,742 player rows collapse to ~4,347
real people, 2,089 of whom gain a multi-season career.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from db.identity import player_identity_key


# revision identifiers, used by Alembic.
revision: str = 'd4f1a9b2c7e3'
down_revision: Union[str, Sequence[str], None] = '9de866620f94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Fact tables keyed on a player_season: re-pointed when two player_seasons
# merge, since they hold scraped data that cannot be regenerated without a
# rescrape. (table, column) pairs.
_FACT_REFS = [
    ('batting_game_lines', 'player_season_id'),
    ('pitching_game_lines', 'player_season_id'),
    ('fielding_game_lines', 'player_season_id'),
    ('plate_appearances', 'batter_player_season_id'),
    ('plate_appearances', 'pitcher_player_season_id'),
]

# Derived tables: deleted rather than re-pointed, because they are rebuilt
# from fact rows by stats/recompute.py anyway and re-pointing could violate
# their one-row-per-player_season unique constraints.
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

    op.add_column('players', sa.Column('identity_key', sa.String(), nullable=True))
    op.add_column('player_seasons', sa.Column('source_player_id', sa.Integer(), nullable=True))

    # The site id currently on `players` actually identifies the player-season,
    # so push it down to every player_season of that player. Before the merge
    # below this is still 1:1, so no information is lost.
    conn.execute(sa.text(
        "UPDATE player_seasons SET source_player_id = "
        "(SELECT p.source_id FROM players p WHERE p.id = player_seasons.player_id)"
    ))

    # Compute identity keys in Python — the normalization (unicode folding,
    # punctuation, case) is not expressible in SQLite's SQL dialect.
    players = conn.execute(
        sa.text("SELECT id, full_name, birth_year, source_id FROM players")
    ).fetchall()
    canonical: dict[str, int] = {}
    remap: dict[int, int] = {}
    for pid, full_name, birth_year, source_id in players:
        key = player_identity_key(full_name or str(source_id), birth_year, source_id)
        keeper = canonical.setdefault(key, pid)
        if keeper == pid:
            conn.execute(
                sa.text("UPDATE players SET identity_key = :k WHERE id = :i"),
                {"k": key, "i": pid},
            )
        else:
            remap[pid] = keeper

    # Re-point each merged-away player's seasons onto the keeper. This is done
    # one player_season at a time rather than as a bulk UPDATE because the
    # keeper may *already* hold a row for the same team_season — one person who
    # was issued two site ids on a single roster — and a bulk update would trip
    # the (player_id, team_season_id) unique constraint. In that case the two
    # player_seasons are themselves merged instead.
    for dupe_id, keeper_id in remap.items():
        seasons = conn.execute(
            sa.text("SELECT id, team_season_id FROM player_seasons WHERE player_id = :d"),
            {"d": dupe_id},
        ).fetchall()
        for loser, team_season_id in seasons:
            keeper_season = conn.execute(
                sa.text(
                    "SELECT id FROM player_seasons "
                    "WHERE player_id = :p AND team_season_id = :t"
                ),
                {"p": keeper_id, "t": team_season_id},
            ).scalar()
            if keeper_season is None:
                conn.execute(
                    sa.text("UPDATE player_seasons SET player_id = :k WHERE id = :l"),
                    {"k": keeper_id, "l": loser},
                )
                continue
            for table, column in _FACT_REFS:
                conn.execute(
                    sa.text(f"UPDATE {table} SET {column} = :k WHERE {column} = :l"),
                    {"k": keeper_season, "l": loser},
                )
            for table, column in _DERIVED_REFS:
                conn.execute(
                    sa.text(f"DELETE FROM {table} WHERE {column} = :l"), {"l": loser}
                )
            conn.execute(sa.text("DELETE FROM player_seasons WHERE id = :l"), {"l": loser})
        conn.execute(sa.text("DELETE FROM players WHERE id = :d"), {"d": dupe_id})

    # players.source_id was created as a UNIQUE index by the initial schema;
    # it must become non-unique now that a person spans several site ids.
    op.drop_index('ix_players_source_id', table_name='players')
    op.create_index('ix_players_source_id', 'players', ['source_id'], unique=False)
    op.create_index('ix_players_identity_key', 'players', ['identity_key'], unique=True)
    op.create_index(
        'ix_player_seasons_source_player_id', 'player_seasons', ['source_player_id'], unique=False
    )

    with op.batch_alter_table('players') as batch:
        batch.alter_column('identity_key', existing_type=sa.String(), nullable=False)
        batch.alter_column('source_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # Lossy by nature: the merged-away player rows cannot be reconstructed, so
    # this only restores the shape of the schema, not the pre-merge rows.
    op.drop_index('ix_player_seasons_source_player_id', table_name='player_seasons')
    op.drop_index('ix_players_identity_key', table_name='players')
    op.drop_index('ix_players_source_id', table_name='players')
    op.create_index('ix_players_source_id', 'players', ['source_id'], unique=True)
    op.drop_column('player_seasons', 'source_player_id')
    op.drop_column('players', 'identity_key')
