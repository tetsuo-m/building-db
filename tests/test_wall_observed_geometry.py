"""Walls keep the observed position apart from the adopted position.

SPEC.md (3. DB, 4. Walls): walls have observed_geometry besides
adopted_geometry. On a first import both are the wall center line on the
drawing. Coordinates are mm from the X1/Y1 grid intersection.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, WALL_TYPES_CSV, run_import

STEP2 = Path(__file__).resolve().parent.parent / "samples" / "step2"

OUTER_WALLS = {
    ((0, 0), (6000, 0)),
    ((6000, 0), (6000, 4000)),
    ((0, 4000), (6000, 4000)),
    ((0, 0), (0, 4000)),
}

# The partition follows the WALL_CENTER line of each file. In
# step2_boundary_only.dxf only the boundary line moved, so it stays at x = 2300.
STEP2_PARTITION = {
    "step2.dxf": ((2300, 0), (2300, 4000)),
    "step2_moved.dxf": ((2800, 0), (2800, 4000)),
    "step2_boundary_only.dxf": ((2300, 0), (2300, 4000)),
}


def geometries(conn, column):
    rows = conn.execute(f"SELECT {column} FROM walls").fetchall()
    result = []
    for (geometry,) in rows:
        (x1, y1), (x2, y2) = json.loads(geometry)
        result.append(tuple(sorted([(round(x1), round(y1)), (round(x2), round(y2))])))
    return sorted(result)


def assert_observed_provenance(conn, source_name, batch_id):
    wall_ids = [r[0] for r in conn.execute("SELECT wall_id FROM walls")]
    assert wall_ids
    for wall_id in wall_ids:
        row = conn.execute(
            "SELECT origin, source, batch_id FROM field_provenance "
            "WHERE target_table = 'walls' AND target_id = ? AND field = 'observed_geometry'",
            (wall_id,),
        ).fetchone()
        assert row is not None
        origin, source, row_batch = row
        assert origin == "cad_import"
        assert source_name in source and "WALL_CENTER" in source
        assert row_batch == batch_id


@pytest.fixture(params=sorted(STEP2_PARTITION))
def step2_imported(request, tmp_path):
    from building_db.importer import import_drawing

    db = tmp_path / "step2.sqlite"
    result = import_drawing(
        db_path=db,
        dxf_path=STEP2 / request.param,
        wall_types_csv=WALL_TYPES_CSV,
        finish_schedule_csv=FINISH_SCHEDULE_CSV,
        default_wall_type=DEFAULT_WALL_TYPE,
    )
    conn = sqlite3.connect(db)
    yield request.param, result, conn
    conn.close()


def test_step1_observed_and_adopted_are_the_wall_center(imported):
    _, conn = imported
    expected = sorted(OUTER_WALLS)
    assert geometries(conn, "observed_geometry") == expected
    assert geometries(conn, "adopted_geometry") == expected


def test_step1_observed_provenance(imported, dxf_path):
    result, conn = imported
    assert_observed_provenance(conn, dxf_path.name, result.batch_id)


def test_step2_observed_and_adopted_are_the_wall_center(step2_imported):
    name, _, conn = step2_imported
    expected = sorted(OUTER_WALLS | {STEP2_PARTITION[name]})
    assert geometries(conn, "observed_geometry") == expected
    assert geometries(conn, "adopted_geometry") == expected


def test_step2_observed_provenance(step2_imported):
    name, result, conn = step2_imported
    assert_observed_provenance(conn, name, result.batch_id)


def test_reimport_refreshes_observed(db_path, dxf_path):
    # The observed position always comes from the latest drawing.
    run_import(db_path, dxf_path)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute("UPDATE walls SET observed_geometry = '[[9, 9], [9, 9]]'")
        result = run_import(db_path, dxf_path)
        assert geometries(conn, "observed_geometry") == sorted(OUTER_WALLS)
        assert_observed_provenance(conn, dxf_path.name, result.batch_id)
    finally:
        conn.close()
