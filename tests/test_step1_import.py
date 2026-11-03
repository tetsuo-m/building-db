"""Step 1: importing into SQLite.

Expected values come from the expected-values table in
samples/step1/expected.md. Each test names the row it checks.
"""

import re

from conftest import run_import


def test_grids(imported):
    # Row "通り芯": 4 grids (X1=0, X2=6000, Y1=0, Y2=4000)
    _, conn = imported
    rows = conn.execute("SELECT label, direction, position FROM grids").fetchall()
    assert sorted((label, d, round(p, 3)) for label, d, p in rows) == [
        ("X1", "X", 0.0),
        ("X2", "X", 6000.0),
        ("Y1", "Y", 0.0),
        ("Y2", "Y", 4000.0),
    ]


def test_nodes(imported):
    # Row "節点": 4 nodes at (0,0), (6000,0), (6000,4000), (0,4000)
    _, conn = imported
    rows = conn.execute("SELECT x, y FROM nodes").fetchall()
    assert sorted((round(x, 3), round(y, 3)) for x, y in rows) == [
        (0.0, 0.0),
        (0.0, 4000.0),
        (6000.0, 0.0),
        (6000.0, 4000.0),
    ]


def test_edges_form_one_closed_contour(imported):
    # Row "部屋割り線": 4 lines, one closed contour
    _, conn = imported
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 4
    rows = conn.execute(
        "SELECT room_id, COUNT(*) FROM room_boundaries GROUP BY room_id"
    ).fetchall()
    assert [count for _, count in rows] == [4]


def test_room(imported):
    # Row "部屋": 1 room, room number 101, name "事務室"
    _, conn = imported
    rows = conn.execute("SELECT room_id, room_no, name FROM rooms").fetchall()
    assert len(rows) == 1
    room_id, room_no, name = rows[0]
    assert re.fullmatch(r"A-RM-\d{3}", room_id)
    assert (room_no, name) == ("101", "事務室")


def test_room_name_comes_from_text_inside_contour(imported):
    # Row "室名の文字": the text is inside the contour
    _, conn = imported
    rows = conn.execute(
        "SELECT origin, source FROM field_provenance "
        "WHERE target_table = 'rooms' AND field = 'name'"
    ).fetchall()
    assert len(rows) == 1
    origin, source = rows[0]
    assert origin == "cad_import"
    assert "ROOM_NAME" in source


def test_walls(imported):
    # Row "壁": 4 walls
    _, conn = imported
    rows = conn.execute("SELECT wall_id, follow_edge FROM walls").fetchall()
    assert len(rows) == 4
    for wall_id, follow_edge in rows:
        assert re.fullmatch(r"A-WL-\d{4}", wall_id)
        assert follow_edge == 1
    # each wall follows exactly one boundary edge in step 1
    spans = conn.execute(
        "SELECT wall_id, COUNT(*) FROM wall_edge_spans GROUP BY wall_id"
    ).fetchall()
    assert sorted(count for _, count in spans) == [1, 1, 1, 1]


def test_wall_types(imported):
    # Row "壁の型": all 4 walls are WT-3 (exterior, ALC100, total 100 mm)
    _, conn = imported
    rows = conn.execute(
        "SELECT t.type_code, t.core, t.total_thickness_mm "
        "FROM walls w JOIN wall_types t ON w.wall_type_id = t.type_id"
    ).fetchall()
    assert rows == [("WT-3", "ALC", 100)] * 4


def test_wall_type_is_recorded_as_human_choice(imported):
    # SPEC.md 4: the default wall type is given by a person on the command line
    _, conn = imported
    rows = conn.execute(
        "SELECT origin, source FROM field_provenance "
        "WHERE target_table = 'walls' AND field = 'wall_type_id'"
    ).fetchall()
    assert len(rows) == 4
    for origin, source in rows:
        assert origin == "designer"
        assert "default" in source


def test_ceiling_height(imported):
    # Row "天井高": 2700 mm (from the finish schedule)
    _, conn = imported
    rows = conn.execute("SELECT ceiling_height FROM rooms").fetchall()
    assert rows == [(2700,)]


def test_no_review_flags(imported):
    # Row "要確認": none
    result, _ = imported
    assert result.review_flags == []


def test_reimport_keeps_ids(db_path, dxf_path):
    # Design doc T23: importing the same file twice issues no new IDs
    tables = ["grids", "nodes", "edges", "rooms", "walls"]
    id_columns = ["grid_id", "node_id", "edge_id", "room_id", "wall_id"]

    import sqlite3

    def snapshot():
        conn = sqlite3.connect(db_path)
        try:
            return {
                t: sorted(r[0] for r in conn.execute(f"SELECT {c} FROM {t}"))
                for t, c in zip(tables, id_columns)
            }
        finally:
            conn.close()

    run_import(db_path, dxf_path)
    first = snapshot()
    result = run_import(db_path, dxf_path)
    assert snapshot() == first
    assert result.review_flags == []
