"""Snapshots and diffs between versions (SPEC.md 8, plan step 5).

The six API-shape tests live in test_step2_diff.py. These cover what they
leave out: the snapshot is a faithful copy, added and missing elements, and
the change in the edges a wall spans (reported as a diff, not a review item).
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from test_step2_matching import drawing, import_version, read_rows

STEP2 = Path(__file__).resolve().parent.parent / "samples" / "step2"


def changes_of(diff, kind, field):
    return {c.element_id: (c.before, c.after) for c in diff.changes if c.kind == kind and c.field == field}


# --- Snapshot -----------------------------------------------------------------


def test_snapshot_is_a_complete_copy(tmp_path):
    from building_db.versions import take_snapshot

    db = tmp_path / "b.sqlite"
    snap = tmp_path / "snap.sqlite"
    import_version(db, STEP2 / "step2.dxf")
    take_snapshot(db, snap)
    for table in ("import_batches", "nodes", "edges", "rooms", "room_boundaries", "walls",
                  "wall_edge_spans", "field_provenance", "wall_types", "grids"):
        assert read_rows(db, f"SELECT * FROM {table} ORDER BY 1") == read_rows(snap, f"SELECT * FROM {table} ORDER BY 1")


def test_snapshot_keeps_the_schema_version(tmp_path):
    from building_db.db import SCHEMA_VERSION, open_existing
    from building_db.versions import take_snapshot

    db = tmp_path / "b.sqlite"
    snap = tmp_path / "snap.sqlite"
    import_version(db, STEP2 / "step2.dxf")
    take_snapshot(db, snap)
    conn = open_existing(snap)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_snapshot_does_not_change_the_source(tmp_path):
    from building_db.versions import take_snapshot

    db = tmp_path / "b.sqlite"
    import_version(db, STEP2 / "step2.dxf")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    take_snapshot(db, tmp_path / "snap.sqlite")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_snapshot_refuses_to_overwrite(tmp_path):
    from building_db.versions import take_snapshot

    db = tmp_path / "b.sqlite"
    snap = tmp_path / "snap.sqlite"
    import_version(db, STEP2 / "step2.dxf")
    take_snapshot(db, snap)
    with pytest.raises(FileExistsError):
        take_snapshot(db, snap)


# --- Diff: added, missing, and the edges a wall spans ------------------------------


@pytest.fixture
def partition_added(tmp_path):
    """Base: one room. New: a partition at 2300. Returns (base copy, db, base wall IDs)."""
    from building_db.versions import take_snapshot

    db = tmp_path / "b.sqlite"
    base = tmp_path / "base.sqlite"
    import_version(db, drawing(tmp_path, "base"))
    walls = {}
    for wall_id, geometry in read_rows(db, "SELECT wall_id, adopted_geometry FROM walls"):
        walls[geometry] = wall_id
    take_snapshot(db, base)
    import_version(db, drawing(tmp_path, "added", [2300]))
    return base, db, walls


def test_added_elements_are_listed(partition_added):
    from building_db.versions import diff_versions

    base, db, _ = partition_added
    diff = diff_versions(base, db)
    added = {(a.kind, a.element_id) for a in diff.added}
    kinds = {}
    for kind, _ in added:
        kinds[kind] = kinds.get(kind, 0) + 1
    # 2 T-junction nodes, 4 new edges (two halves of south and north, the partition
    # ... counted below), 1 new wall; rooms: the split makes new rooms
    assert kinds["node"] == 2
    assert kinds["wall"] == 1
    assert kinds["edge"] == 5  # south x2, north x2, partition
    assert list(diff.missing) != []  # the old south and north edges are gone


def test_missing_elements_are_listed_and_kept(partition_added):
    from building_db.versions import diff_versions

    base, db, _ = partition_added
    diff = diff_versions(base, db)
    missing_edges = {m.element_id for m in diff.missing if m.kind == "edge"}
    assert len(missing_edges) == 2  # the undivided south and north edges
    still_there = {e for (e,) in read_rows(db, "SELECT edge_id FROM edges")}
    assert missing_edges <= still_there


def test_wall_edge_ids_change_is_a_diff_not_a_review_item(partition_added, tmp_path):
    from building_db.versions import diff_versions

    base, db, walls = partition_added
    south = walls["[[0.0, 0.0], [6000.0, 0.0]]"]
    changes = changes_of(diff_versions(base, db), "wall", "edge_ids")
    before, after = changes[south]
    assert len(before) == 1 and len(after) == 2
    # the outer walls' positions did not change, so no geometry change is reported for them
    assert south not in changes_of(diff_versions(base, db), "wall", "adopted_geometry")


def test_diff_of_identical_versions_is_empty(tmp_path):
    from building_db.versions import diff_versions, take_snapshot

    db = tmp_path / "b.sqlite"
    base = tmp_path / "base.sqlite"
    dxf = drawing(tmp_path, "base", [2300])
    import_version(db, dxf)
    take_snapshot(db, base)
    import_version(db, dxf)
    diff = diff_versions(base, db)
    assert list(diff.changes) == [] and list(diff.added) == [] and list(diff.missing) == []


def test_diff_refuses_databases_of_another_schema_version(tmp_path):
    from building_db.db import SchemaVersionError
    from building_db.versions import diff_versions

    db = tmp_path / "b.sqlite"
    import_version(db, STEP2 / "step2.dxf")
    old = tmp_path / "old.sqlite"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE walls (wall_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaVersionError):
        diff_versions(old, db)
