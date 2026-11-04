"""The DB records its schema version; a DB of another version stops the program.

Nothing is migrated automatically. The stop must leave the DB file untouched
and tell the user what to do in plain words.
"""

import hashlib
import sqlite3

import pytest

from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, SAMPLES, WALL_TYPES_CSV, run_import

STEP1_DXF = SAMPLES / "step1.dxf"


def make_old_db(path, version=0):
    """A DB with tables of an older shape, as made before versions were recorded."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE walls (wall_id TEXT PRIMARY KEY, adopted_geometry TEXT NOT NULL)")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    return path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def import_args(db):
    return [
        "import", str(STEP1_DXF), "--db", str(db),
        "--wall-types", str(WALL_TYPES_CSV),
        "--finish-schedule", str(FINISH_SCHEDULE_CSV),
        "--default-wall-type", DEFAULT_WALL_TYPE,
    ]


def test_new_db_records_the_version(db_path):
    from building_db.db import SCHEMA_VERSION

    run_import(db_path, STEP1_DXF)
    assert user_version(db_path) == SCHEMA_VERSION


def test_import_into_a_current_db_still_works(db_path):
    run_import(db_path, STEP1_DXF)
    run_import(db_path, STEP1_DXF)


def test_import_into_an_old_db_stops(tmp_path):
    from building_db.db import SCHEMA_VERSION, SchemaVersionError

    db = make_old_db(tmp_path / "old.sqlite")
    with pytest.raises(SchemaVersionError) as info:
        run_import(db, STEP1_DXF)
    message = str(info.value)
    assert "version 0" in message and f"version {SCHEMA_VERSION}" in message


def test_import_into_an_old_db_changes_nothing(tmp_path):
    from building_db.db import SchemaVersionError

    db = make_old_db(tmp_path / "old.sqlite")
    before = sha256(db)
    with pytest.raises(SchemaVersionError):
        run_import(db, STEP1_DXF)
    assert sha256(db) == before


def test_db_of_a_newer_version_also_stops(tmp_path):
    from building_db.db import SCHEMA_VERSION, SchemaVersionError

    db = make_old_db(tmp_path / "new.sqlite", version=SCHEMA_VERSION + 1)
    with pytest.raises(SchemaVersionError):
        run_import(db, STEP1_DXF)


def test_cli_import_stops_with_a_plain_message(tmp_path, capsys):
    from building_db.cli import main

    db = make_old_db(tmp_path / "old.sqlite")
    before = sha256(db)
    assert main(import_args(db)) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "schema version 0" in output
    assert "Nothing was changed" in output
    assert "new database file" in output
    assert "Traceback" not in output
    assert sha256(db) == before


def test_cli_report_stops_with_a_plain_message(tmp_path, capsys):
    from building_db.cli import main

    db = make_old_db(tmp_path / "old.sqlite")
    before = sha256(db)
    assert main(["report", "--db", str(db)]) == 1
    captured = capsys.readouterr()
    assert "schema version 0" in captured.out + captured.err
    assert sha256(db) == before


def test_open_existing_does_not_create_a_file(tmp_path):
    from building_db.db import open_existing

    db = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        open_existing(db)
    assert not db.exists()


def test_open_existing_stops_on_an_old_db(tmp_path):
    from building_db.db import SchemaVersionError, open_existing

    db = make_old_db(tmp_path / "old.sqlite")
    with pytest.raises(SchemaVersionError):
        open_existing(db)
