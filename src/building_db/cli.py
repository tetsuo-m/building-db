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
    p_import.add_argument("--no-snapshot", action="store_true",
                          help="Do not copy an existing database into <db>.snapshots/ before importing")

    p_report = sub.add_parser("report", help="Show areas, perimeters and wall lengths")
    p_report.add_argument("--db", type=Path, required=True)

    p_snapshot = sub.add_parser("snapshot", help="Copy the database as it is now")
    p_snapshot.add_argument("--db", type=Path, required=True)
    p_snapshot.add_argument("--to", type=Path, required=True, help="Path of the copy; must not exist")

    p_diff = sub.add_parser("diff", help="Compare the latest version of two databases by ID")
    p_diff.add_argument("--base", type=Path, required=True, help="The earlier database (e.g. a snapshot)")
    p_diff.add_argument("--new", type=Path, required=True, help="The later database")

    p_decide = sub.add_parser("decide", help="Record a person's decision on a wall deviation")
    p_decide.add_argument("--db", type=Path, required=True)
    p_decide.add_argument("--target", required=True, help="Wall ID, e.g. A-WL-0005")
    p_decide.add_argument("--choice", required=True, choices=_choices())
    p_decide.add_argument("--reason", help="A word or two on why (optional)")
    p_decide.add_argument("--date", help="Date of the decision, ISO (default: today)")

    args = parser.parse_args(argv)
    commands = {"import": _import, "report": _report, "snapshot": _snapshot, "diff": _diff, "decide": _decide}
    try:
        return commands[args.command](args)
    except SchemaVersionError as error:
        print(error, file=sys.stderr)
        return 1
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        print(f"Nothing was changed: {error}")
        return 1


def _choices() -> tuple[str, ...]:
    from building_db.versions import CHOICES

    return CHOICES


def _import(args) -> int:
    from building_db.importer import import_drawing
    from building_db.versions import take_snapshot

    if args.db.exists() and not args.no_snapshot:
        snapshot = take_snapshot(args.db, _snapshot_path(args.db))
        print(f"Snapshot of the previous version: {snapshot}")

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


def _snapshot_path(db: Path) -> Path:
    """<db>.snapshots/v<latest batch>_<time>.sqlite, never an existing file."""
    import sqlite3
    from datetime import datetime

    conn = sqlite3.connect(db)
    try:
        (batch,) = conn.execute("SELECT MAX(batch_id) FROM import_batches").fetchone()
    except sqlite3.Error:
        batch = None
    finally:
        conn.close()
    folder = db.with_name(db.name + ".snapshots")
    stem = f"v{batch if batch is not None else 0}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = folder / f"{stem}.sqlite"
    n = 1
    while path.exists():
        n += 1
        path = folder / f"{stem}_{n}.sqlite"
    return path


def _snapshot(args) -> int:
    from building_db.versions import take_snapshot

    if args.to.exists():
        print(f"Nothing was changed: {args.to} already exists")
        return 1
    print(f"Snapshot written: {take_snapshot(args.db, args.to)}")
    return 0


def _diff(args) -> int:
    from building_db.versions import diff_versions

    diff = diff_versions(args.base, args.new)
    print(f"Changes ({len(diff.changes)}) — {args.base} -> {args.new}")
    for c in diff.changes:
        print(f"  {c.kind} {c.element_id} {c.field}: {_fmt(c.before, c.field)} -> {_fmt(c.after, c.field)}")
    print("Added: " + (", ".join(f"{a.kind} {a.element_id}" for a in diff.added) or "none"))
    print("Missing: " + (", ".join(f"{m.kind} {m.element_id}" for m in diff.missing) or "none"))
    if diff.missing:
        print("  (missing elements stay in the DB; nothing is deleted)")
    return 0


def _fmt(value, field: str) -> str:
    if isinstance(value, float):
        if field.endswith("_m2"):
            return f"{value:.2f}"
        if field.endswith("_m"):
            return f"{value:.3f}"
        return f"{value:.0f}"
    if isinstance(value, tuple):
        return "(" + ", ".join(_fmt(v, "") for v in value) + ")"
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    return str(value)


def _decide(args) -> int:
    from building_db.versions import record_decision, wall_status

    record = record_decision(args.db, args.target, args.choice, reason=args.reason, decided_on=args.date)
    print(f"Recorded decision {record.decision_id}: {record.choice} on {record.target_id} "
          f"({record.decided_on}, by a {record.basis_kind})")
    if record.reason:
        print(f"  reason: {record.reason}")
    status = wall_status(args.db, args.target)
    print(f"  status now: {status or 'no open item'}")
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

        from building_db.versions import _accepted_exceptions, _drawing_fix_proposals

        proposals = _drawing_fix_proposals(conn)
        if proposals:
            print("Approved positions not on the drawing yet (drawing fix proposals)")
            for p in proposals:
                where = f"x = {p.position_mm:.0f}" if p.position_mm is not None else _fmt(p.adopted_geometry, "")
                print(f"  {p.wall_id}  move the wall center line to {where} "
                      f"(drawn at {_fmt(p.observed_geometry, '')})")

        exceptions = _accepted_exceptions(conn)
        if exceptions:
            print("Approved exceptions (walls that no longer follow their boundary edge)")
            for e in exceptions:
                print(f"  {e.wall_id}  {e.distance_mm:.0f} mm from edge {', '.join(e.edge_ids)}")

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
