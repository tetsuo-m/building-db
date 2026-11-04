"""Command line: `python -m building_db import ...` and `... report ...`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from building_db.db import SchemaVersionError


def main(argv: list[str] | None = None) -> int:
    """Run a command and return the exit code."""
    parser = argparse.ArgumentParser(prog="building_db")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="Import a DXF drawing and its tables")
    p_import.add_argument("dxf", type=Path)
    p_import.add_argument("--db", type=Path, required=True)
    p_import.add_argument("--wall-types", type=Path, required=True)
    p_import.add_argument("--finish-schedule", type=Path, required=True)
    p_import.add_argument("--default-wall-type", required=True,
                          help="Wall type code given to every wall, e.g. WT-3")

    p_report = sub.add_parser("report", help="Show areas, perimeters and wall lengths")
    p_report.add_argument("--db", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            return _import(args)
        return _report(args)
    except SchemaVersionError as error:
        print(error, file=sys.stderr)
        return 1


def _import(args) -> int:
    from building_db.importer import import_drawing

    result = import_drawing(
        db_path=args.db,
        dxf_path=args.dxf,
        wall_types_csv=args.wall_types,
        finish_schedule_csv=args.finish_schedule,
        default_wall_type=args.default_wall_type,
    )
    print(f"Imported {args.dxf} as batch {result.batch_id}")
    for name, count in result.counts.items():
        print(f"  {name}: {count}")
    m = result.match_counts
    print(f"Matching against the previous version: {m['auto']} matched automatically, "
          f"{m['to_person']} for a person to check")
    if result.review_flags:
        print(f"Review flags ({len(result.review_flags)}):")
        for flag in result.review_flags:
            print(f"  - {flag}")
    else:
        print("Review flags: none")
    return 0


def _report(args) -> int:
    from building_db.calc import LATEST_BATCH, room_area_m2, room_perimeter_m, wall_lengths_m
    from building_db.db import open_existing

    if not args.db.exists():
        print(f"Database not found: {args.db}")
        return 1
    conn = open_existing(args.db)
    try:
        print("Rooms (measured on wall centers; calculated, not stored)")
        rooms = conn.execute(
            "SELECT room_id, room_no, name, ceiling_height FROM rooms "
            f"WHERE last_seen_batch = {LATEST_BATCH} ORDER BY room_id"
        ).fetchall()
        for room_id, room_no, name, ceiling in rooms:
            area = room_area_m2(conn, room_id)
            perimeter = room_perimeter_m(conn, room_id)
            print(f"  {room_id}  {room_no or '-'} {name or '-'}  "
                  f"area {area:.2f} m2  perimeter {perimeter:.3f} m  ceiling height {ceiling or '-'} mm")

        print("Walls (adopted wall center length)")
        types = dict(conn.execute(
            "SELECT w.wall_id, t.type_code FROM walls w LEFT JOIN wall_types t ON t.type_id = w.wall_type_id"))
        lengths = wall_lengths_m(conn)
        for wall_id, length in lengths.items():
            print(f"  {wall_id}  {types.get(wall_id) or '-'}  {length:.3f} m")
        print(f"  total {sum(lengths.values()):.3f} m ({len(lengths)} walls)")

        gone = conn.execute(
            "SELECT 'room', room_id, last_seen_batch FROM rooms "
            f"WHERE last_seen_batch IS NOT {LATEST_BATCH} "
            "UNION ALL SELECT 'wall', wall_id, last_seen_batch FROM walls "
            f"WHERE last_seen_batch IS NOT {LATEST_BATCH} ORDER BY 2"
        ).fetchall()
        if gone:
            print("Not on the latest drawing (kept in the DB)")
            for kind, element_id, batch in gone:
                print(f"  {element_id}  {kind}  last seen in batch {batch if batch is not None else '-'}")
    finally:
        conn.close()
    return 0
