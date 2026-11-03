"""Shared fixtures for step 1 (one room).

Every test runs twice: once with the program-generated DXF and once with
the DXF exported from Jw_cad. Both must give the same results.
"""

import sqlite3
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "step1"
WALL_TYPES_CSV = SAMPLES / "wall_types.csv"
FINISH_SCHEDULE_CSV = SAMPLES / "finish_schedule.csv"
DEFAULT_WALL_TYPE = "WT-3"


@pytest.fixture(params=["step1.dxf", "step1_JW.dxf"])
def dxf_path(request) -> Path:
    return SAMPLES / request.param


def run_import(db_path: Path, dxf_path: Path):
    from building_db.importer import import_drawing

    return import_drawing(
        db_path=db_path,
        dxf_path=dxf_path,
        wall_types_csv=WALL_TYPES_CSV,
        finish_schedule_csv=FINISH_SCHEDULE_CSV,
        default_wall_type=DEFAULT_WALL_TYPE,
    )


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "step1.sqlite"


@pytest.fixture
def imported(db_path, dxf_path):
    """Import the sample once and return (import result, open connection)."""
    result = run_import(db_path, dxf_path)
    conn = sqlite3.connect(db_path)
    yield result, conn
    conn.close()
