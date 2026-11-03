"""Import a drawing and its tables into the SQLite database."""

from pathlib import Path


def import_drawing(
    db_path: Path,
    dxf_path: Path,
    wall_types_csv: Path,
    finish_schedule_csv: Path,
    default_wall_type: str,
):
    """Import grids, rooms, walls and table values; return an ImportResult."""
    raise NotImplementedError
