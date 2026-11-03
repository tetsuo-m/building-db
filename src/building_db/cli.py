"""Command line: `python -m building_db import ...` and `... report ...`."""

from __future__ import annotations

import argparse
from pathlib import Path


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
    if args.command == "import":
        return _import(args)
    return _report(args)


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
    if result.review_flags:
        print(f"Review flags ({len(result.review_flags)}):")
        for flag in result.review_flags:
            print(f"  - {flag}")
    else:
        print("Review flags: none")
    return 0


def _report(args) -> int:
    raise NotImplementedError
