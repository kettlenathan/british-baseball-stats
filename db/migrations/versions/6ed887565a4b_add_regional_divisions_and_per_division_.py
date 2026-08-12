"""Add regional divisions and per-division context

Revision ID: 6ed887565a4b
Revises: e8b3c05d1a72
Create Date: 2026-08-12 16:50:45.481659

Purely additive: no existing table's meaning changes and no existing column
is touched, so an app running the previous revision's code against this
schema behaves exactly as before. league_season_context in particular is
left completely alone — division_context sits alongside it rather than
replacing it, because the app shows both scales (see db/models.py).

Backfilling the new columns for already-scraped seasons does not need the
network: scripts/backfill_divisions.py replays the cached schedule and
standings responses in data/raw_cache.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ed887565a4b'
down_revision: Union[str, Sequence[str], None] = 'e8b3c05d1a72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'divisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('league_season_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('source_group_id', sa.Integer(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['league_season_id'], ['league_seasons.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('league_season_id', 'name'),
    )
    op.create_index(op.f('ix_divisions_league_season_id'), 'divisions', ['league_season_id'], unique=False)
    op.create_index(op.f('ix_divisions_source_group_id'), 'divisions', ['source_group_id'], unique=False)

    op.create_table(
        'division_context',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('division_id', sa.Integer(), nullable=False),
        sa.Column('lg_obp', sa.Float(), nullable=True),
        sa.Column('lg_slg', sa.Float(), nullable=True),
        sa.Column('lg_woba', sa.Float(), nullable=True),
        sa.Column('lg_era', sa.Float(), nullable=True),
        sa.Column('lg_fip', sa.Float(), nullable=True),
        sa.Column('fip_constant', sa.Float(), nullable=True),
        sa.Column('runs_per_pa', sa.Float(), nullable=True),
        sa.Column('runs_per_win', sa.Float(), nullable=True),
        sa.Column('replacement_runs_per_pa', sa.Float(), nullable=True),
        sa.Column('replacement_fip_delta', sa.Float(), nullable=True),
        sa.Column('games', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pa', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['division_id'], ['divisions.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_division_context_division_id'), 'division_context', ['division_id'], unique=True)

    # SQLite has no ALTER TABLE ADD CONSTRAINT, and alembic rejects a foreign
    # key declared inline on add_column for the same reason, so the columns
    # carrying one go through batch mode (copy-and-move) instead. Both tables
    # are small enough for the rewrite to be cheap.
    with op.batch_alter_table('team_seasons') as batch:
        batch.add_column(sa.Column('division_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_team_seasons_division', 'divisions', ['division_id'], ['id'])
    op.create_index(op.f('ix_team_seasons_division_id'), 'team_seasons', ['division_id'], unique=False)

    with op.batch_alter_table('games') as batch:
        batch.add_column(sa.Column('division_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('source_group_id', sa.Integer(), nullable=True))
        # server_default is required, not cosmetic: games already holds 5,531
        # rows and a NOT NULL column cannot be added to them without one.
        # Existing rows land on "regular", correct for all but the 140 playoff
        # games the backfill then re-classifies.
        batch.add_column(sa.Column('phase', sa.String(), nullable=False, server_default='regular'))
        batch.create_foreign_key('fk_games_division', 'divisions', ['division_id'], ['id'])
    op.create_index(op.f('ix_games_division_id'), 'games', ['division_id'], unique=False)
    op.create_index(op.f('ix_games_phase'), 'games', ['phase'], unique=False)
    op.create_index(op.f('ix_games_source_group_id'), 'games', ['source_group_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_games_source_group_id'), table_name='games')
    op.drop_index(op.f('ix_games_phase'), table_name='games')
    op.drop_index(op.f('ix_games_division_id'), table_name='games')
    with op.batch_alter_table('games') as batch:
        batch.drop_constraint('fk_games_division', type_='foreignkey')
        batch.drop_column('phase')
        batch.drop_column('source_group_id')
        batch.drop_column('division_id')

    op.drop_index(op.f('ix_team_seasons_division_id'), table_name='team_seasons')
    with op.batch_alter_table('team_seasons') as batch:
        batch.drop_constraint('fk_team_seasons_division', type_='foreignkey')
        batch.drop_column('division_id')

    op.drop_index(op.f('ix_division_context_division_id'), table_name='division_context')
    op.drop_table('division_context')

    op.drop_index(op.f('ix_divisions_source_group_id'), table_name='divisions')
    op.drop_index(op.f('ix_divisions_league_season_id'), table_name='divisions')
    op.drop_table('divisions')
