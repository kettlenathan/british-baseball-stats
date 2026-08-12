"""Record how a final game reached its result

Adds Game.result_type ("played" / "forfeit" / "result_only") so the two
things a game can supply — a result, and a box score — stop being conflated
by `status` alone. See db/models.py for the evidence behind the split.

The existing rows are seeded to "played" rather than left NULL. That is not
cosmetic: every new `result_type == "played"` filter (run environments,
division contexts, probable pitchers) would otherwise match nothing at all
between this migration running and the backfill completing, silently zeroing
out the league averages WAR depends on. "played" is right for the
overwhelming majority of already-final rows; scripts/backfill_divisions.py
then re-classifies the ~200 that are forfeits or result-only, and promotes
the 545 games the old rule dropped entirely.

Revision ID: e8e0f6047d5d
Revises: d81fb0a021de
Create Date: 2026-08-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8e0f6047d5d'
down_revision: Union[str, Sequence[str], None] = 'd81fb0a021de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('games', sa.Column('result_type', sa.String(), nullable=True))
    op.create_index(op.f('ix_games_result_type'), 'games', ['result_type'], unique=False)
    op.execute("UPDATE games SET result_type = 'played' WHERE status = 'final'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_games_result_type'), table_name='games')
    op.drop_column('games', 'result_type')
