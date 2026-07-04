"""Generic idempotent upsert helper keyed on a unique constraint.

Every scraper ingestion function should go through this so re-running any
scrape step is always safe (per the plan's resumability requirement).
"""

from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session


def upsert(session: Session, model: type, values: dict[str, Any], conflict_columns: list[str]) -> Any:
    """Insert `values` as a row of `model`, or update it in place if a row
    already exists matching `conflict_columns` (which must correspond to a
    unique constraint or primary key on the table).

    Returns the primary key id of the resulting row.
    """
    table = model.__table__
    pk_column = inspect(model).primary_key[0].name
    stmt = insert(table).values(**values)
    update_columns = {
        c.name: getattr(stmt.excluded, c.name)
        for c in table.columns
        if c.name not in conflict_columns and c.name != pk_column
    }

    if update_columns:
        stmt = stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_columns)
        stmt = stmt.returning(getattr(table.c, pk_column))
        return session.execute(stmt).scalar_one()

    # Nothing besides the conflict key(s) to update (e.g. a pure lookup
    # table) — ON CONFLICT DO UPDATE requires a non-empty SET clause, so fall
    # back to DO NOTHING and look the existing row's id up separately.
    stmt = stmt.on_conflict_do_nothing(index_elements=conflict_columns)
    session.execute(stmt)
    lookup = select(getattr(table.c, pk_column)).where(
        *[getattr(table.c, col) == values[col] for col in conflict_columns]
    )
    return session.execute(lookup).scalar_one()
