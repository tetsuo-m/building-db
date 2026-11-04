"""Commands for versions: snapshot, diff, decide, and the snapshot taken by import.

CLAUDE.md: import, matching, diff and output go through commands, and the
person confirms through them. The AI never writes the DB directly.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, WALL_TYPES_CSV

STEP2 = Path(__file__).resolve().parent.parent / "samples" / "step2"


def import_args(db, dxf):
    return [
        "import", str(dxf), "--db", str(db),
        "--wall-types", str(WALL_TYPES_CSV),
        "--finish-schedule", str(FINISH_SCHEDULE_CSV),
        "--default-wall-type", DEFAULT_WALL_TYPE,
    ]


def partition_wall_id(db):
    conn = sqlite3.connect(db)
    try:
        for wall_id, geometry in conn.execute("SELECT wall_id, observed_geometry FROM walls"):
            (x1, _), (x2, _) = json.loads(geometry)
            if round(x1) == round(x2) and round(x1) not in (0, 6000):
                return wall_id
    finally:
        conn.close()
    raise AssertionError("no partition wall")


@pytest.fixture
def base(tmp_path, capsys):
    from building_db.cli import main

    db = tmp_path / "building.sqlite"
    assert main(import_args(db, STEP2 / "step2.dxf")) == 0
    capsys.readouterr()
    return db


# --- snapshot -----------------------------------------------------------------


def test_snapshot_command(base, tmp_path, capsys):
    from building_db.cli import main

    snap = tmp_path / "snap.sqlite"
    assert main(["snapshot", "--db", str(base), "--to", str(snap)]) == 0
    assert snap.exists()
    assert str(snap) in capsys.readouterr().out


def test_snapshot_command_refuses_to_overwrite(base, tmp_path, capsys):
    from building_db.cli import main

    snap = tmp_path / "snap.sqlite"
    assert main(["snapshot", "--db", str(base), "--to", str(snap)]) == 0
    assert main(["snapshot", "--db", str(base), "--to", str(snap)]) == 1
    assert "already exists" in capsys.readouterr().out


def test_import_into_an_existing_db_takes_a_snapshot_first(base, capsys):
    from building_db.cli import main

    folder = base.with_name(base.name + ".snapshots")
    assert not folder.exists()
    assert main(import_args(base, STEP2 / "step2_moved.dxf")) == 0
    out = capsys.readouterr().out
    snapshots = sorted(folder.glob("*.sqlite"))
    assert len(snapshots) == 1
    assert str(snapshots[0]) in out
    # the snapshot is the version before this import
    conn = sqlite3.connect(snapshots[0])
    try:
        assert sorted(round(x) for (x,) in conn.execute("SELECT x FROM nodes")) == [0, 0, 2300, 2300, 6000, 6000]
    finally:
        conn.close()


def test_import_into_a_new_db_takes_no_snapshot(tmp_path, capsys):
    from building_db.cli import main

    db = tmp_path / "new.sqlite"
    assert main(import_args(db, STEP2 / "step2.dxf")) == 0
    assert not db.with_name(db.name + ".snapshots").exists()


def test_import_can_skip_the_snapshot(base, capsys):
    from building_db.cli import main

    assert main(import_args(base, STEP2 / "step2_moved.dxf") + ["--no-snapshot"]) == 0
    assert not base.with_name(base.name + ".snapshots").exists()


# --- diff ---------------------------------------------------------------------


def test_diff_command_lists_changes_added_and_missing(base, tmp_path, capsys):
    from building_db.cli import main

    snap = tmp_path / "snap.sqlite"
    assert main(["snapshot", "--db", str(base), "--to", str(snap)]) == 0
    assert main(import_args(base, STEP2 / "step2_moved.dxf")) == 0
    capsys.readouterr()
    assert main(["diff", "--base", str(snap), "--new", str(base)]) == 0
    out = capsys.readouterr().out
    assert "node" in out and "2300" in out and "2800" in out
    assert "room" in out and "9.20" in out and "11.20" in out
    assert "wall" in out
    assert "Added: none" in out and "Missing: none" in out


def test_diff_command_stops_on_a_db_of_another_version(base, tmp_path, capsys):
    from building_db.cli import main

    old = tmp_path / "old.sqlite"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE walls (wall_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    assert main(["diff", "--base", str(old), "--new", str(base)]) == 1
    captured = capsys.readouterr()
    assert "schema version 0" in captured.out + captured.err


# --- decide -------------------------------------------------------------------


@pytest.fixture
def deviating(base, capsys):
    from building_db.cli import main

    assert main(import_args(base, STEP2 / "step2_boundary_only.dxf")) == 0
    capsys.readouterr()
    return base, partition_wall_id(base)


def test_decide_command_records_and_applies(deviating, capsys):
    from building_db.cli import main
    from building_db.versions import decisions_for, wall_status

    db, wall_id = deviating
    assert main(["decide", "--db", str(db), "--target", wall_id, "--choice", "align_to_boundary",
                 "--reason", "Follow the plan", "--date", "2026-11-05"]) == 0
    out = capsys.readouterr().out
    assert wall_id in out and "align_to_boundary" in out
    (record,) = decisions_for(db, wall_id)
    assert (record.choice, record.reason, record.decided_on) == ("align_to_boundary", "Follow the plan", "2026-11-05")
    assert wall_status(db, wall_id) == "approved_not_on_drawing"


def test_decide_command_without_a_reason(deviating, capsys):
    from building_db.cli import main
    from building_db.versions import decisions_for

    db, wall_id = deviating
    assert main(["decide", "--db", str(db), "--target", wall_id, "--choice", "unfollow"]) == 0
    assert decisions_for(db, wall_id)[0].reason is None


def test_decide_command_rejects_a_wall_without_deviation(base, capsys):
    from building_db.cli import main

    wall_id = partition_wall_id(base)
    assert main(["decide", "--db", str(base), "--target", wall_id, "--choice", "unfollow"]) == 1
    captured = capsys.readouterr()
    assert "no deviation" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_decide_command_rejects_an_unknown_choice(deviating, capsys):
    from building_db.cli import main

    db, wall_id = deviating
    with pytest.raises(SystemExit):
        main(["decide", "--db", str(db), "--target", wall_id, "--choice", "snap_it"])


def test_import_output_shows_deviations_and_choices(base, capsys):
    from building_db.cli import main

    assert main(import_args(base, STEP2 / "step2_boundary_only.dxf")) == 0
    out = capsys.readouterr().out
    assert "500 mm" in out
    assert "align_to_boundary" in out and "unfollow" in out


def test_report_shows_approved_position_not_on_drawing(deviating, capsys):
    from building_db.cli import main

    db, wall_id = deviating
    assert main(["decide", "--db", str(db), "--target", wall_id, "--choice", "align_to_boundary"]) == 0
    capsys.readouterr()
    assert main(["report", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "not on the drawing" in out.lower()
    assert wall_id in out
