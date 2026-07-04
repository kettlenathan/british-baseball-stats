from db.models import League, Team
from db.upsert import upsert


def test_upsert_inserts_new_row(session):
    league_id = upsert(session, League, {"code": "nbl", "name": "National Baseball League"}, ["code"])
    session.commit()
    assert session.query(League).count() == 1
    assert session.get(League, league_id).name == "National Baseball League"


def test_upsert_on_existing_row_updates_in_place_without_duplicating(session):
    id1 = upsert(session, League, {"code": "nbl", "name": "Original Name"}, ["code"])
    session.commit()

    id2 = upsert(session, League, {"code": "nbl", "name": "Updated Name"}, ["code"])
    session.commit()

    assert id1 == id2
    assert session.query(League).count() == 1
    assert session.get(League, id1).name == "Updated Name"


def test_upsert_pure_lookup_table_without_extra_columns(session):
    # Team only has (id, name) plus a unique constraint on name — exercises
    # the ON CONFLICT DO NOTHING fallback path when there's nothing to update.
    id1 = upsert(session, Team, {"name": "Croydon Pirates"}, ["name"])
    session.commit()
    id2 = upsert(session, Team, {"name": "Croydon Pirates"}, ["name"])
    session.commit()

    assert id1 == id2
    assert session.query(Team).count() == 1


def test_repeated_upserts_across_many_rows_stay_idempotent(session):
    for _ in range(3):
        upsert(session, League, {"code": "d2", "name": "Division 2"}, ["code"])
        upsert(session, League, {"code": "d3", "name": "Division 3"}, ["code"])
        session.commit()

    assert session.query(League).count() == 2
