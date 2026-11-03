"""Step 1: calculated values (not stored in the DB).

Expected values come from the expected-values table in
samples/step1/expected.md.
"""

import pytest

from building_db.calc import room_area_m2, room_perimeter_m, wall_lengths_m
from building_db.cli import main


def only_room_id(conn):
    (room_id,) = conn.execute("SELECT room_id FROM rooms").fetchone()
    return room_id


def test_room_area(imported):
    # Row "面積（壁芯）": 24.00 m2
    _, conn = imported
    assert room_area_m2(conn, only_room_id(conn)) == pytest.approx(24.00)


def test_room_perimeter(imported):
    # Row "周長（壁芯）": 20.000 m
    _, conn = imported
    assert room_perimeter_m(conn, only_room_id(conn)) == pytest.approx(20.000)


def test_wall_lengths(imported):
    # Row "壁": wall center lengths 6.000, 4.000, 6.000, 4.000; total 20.000 m
    _, conn = imported
    lengths = wall_lengths_m(conn)  # {wall_id: length in m}
    assert sorted(lengths.values()) == pytest.approx([4.0, 4.0, 6.0, 6.0])
    assert sum(lengths.values()) == pytest.approx(20.000)


def test_cli_import_then_report(tmp_path, dxf_path, capsys):
    # The commands from SPEC.md 6 print the same values
    from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, WALL_TYPES_CSV

    db = tmp_path / "cli.sqlite"
    assert main([
        "import", str(dxf_path),
        "--db", str(db),
        "--wall-types", str(WALL_TYPES_CSV),
        "--finish-schedule", str(FINISH_SCHEDULE_CSV),
        "--default-wall-type", DEFAULT_WALL_TYPE,
    ]) == 0
    capsys.readouterr()

    assert main(["report", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "24.00" in out
    assert "20.000" in out
