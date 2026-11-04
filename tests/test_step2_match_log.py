"""Why elements were matched is recorded; elements no longer seen stay out of totals.

SPEC.md 8 (a): every automatic match records its reason (anchors, connections,
move), and the numbers of automatic matches and of items handed to a person are
counted. match_log is a history per import batch and is never overwritten.
moved_mm is a fact of the import, not a value computed from the current state
(SPEC.md 3), so it is stored.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, WALL_TYPES_CSV
from test_step1_review import build_dxf
from test_step2_matching import ROOM_101, SQUARE, read_rows

STEP2 = Path(__file__).resolve().parent.parent / "samples" / "step2"
BASE = STEP2 / "step2.dxf"
MOVED = STEP2 / "step2_moved.dxf"


def import_version(db, dxf):
    from building_db.importer import import_drawing

    return import_drawing(
        db_path=db,
        dxf_path=dxf,
        wall_types_csv=WALL_TYPES_CSV,
        finish_schedule_csv=FINISH_SCHEDULE_CSV,
        default_wall_type=DEFAULT_WALL_TYPE,
    )


def node_ids_by_position(db):
    return {(round(x), round(y)): i for i, x, y in read_rows(db, "SELECT node_id, x, y FROM nodes")}


def log_rows(db, batch_id, table=None):
    where = f"batch_id = {batch_id}" + (f" AND target_table = '{table}'" if table else "")
    rows = read_rows(
        db,
        "SELECT target_table, target_id, classification, rule, anchors, moved_mm, candidates, review "
        f"FROM match_log WHERE {where}",
    )
    return {
        (t, i): {"classification": c, "rule": r, "anchors": json.loads(a) if a else None,
                 "moved_mm": m, "candidates": json.loads(cand) if cand else None, "review": bool(rev)}
        for t, i, c, r, a, m, cand, rev in rows
    }


@pytest.fixture
def moved(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, BASE)
    nodes_before = node_ids_by_position(db)
    result = import_version(db, MOVED)
    return db, nodes_before, result


# --- Reasons ------------------------------------------------------------------


def test_first_import_has_nothing_to_match(tmp_path):
    db = tmp_path / "b.sqlite"
    result = import_version(db, BASE)
    assert read_rows(db, "SELECT COUNT(*) FROM match_log") == [(0,)]
    assert result.match_counts == {"auto": 0, "to_person": 0}


def test_corner_nodes_are_matched_by_position(moved):
    db, before, result = moved
    log = log_rows(db, result.batch_id, "nodes")
    for position in [(0, 0), (6000, 0), (6000, 4000), (0, 4000)]:
        entry = log[("nodes", before[position])]
        assert entry["classification"] == "match"
        assert entry["rule"] == "same_position"
        assert entry["moved_mm"] == 0


def test_t_junction_nodes_are_matched_by_connections(moved):
    db, before, result = moved
    log = log_rows(db, result.batch_id, "nodes")
    for position in [(2300, 0), (2300, 4000)]:
        entry = log[("nodes", before[position])]
        assert entry["classification"] == "moved"
        assert entry["rule"] == "connectivity"
        assert entry["moved_mm"] == pytest.approx(500)
    south = log[("nodes", before[(2300, 0)])]
    assert sorted(south["anchors"]) == sorted([before[(0, 0)], before[(6000, 0)]])


def test_edges_rooms_and_walls_have_reasons_too(moved):
    db, _, result = moved
    log = log_rows(db, result.batch_id)
    rules = {}
    for (table, _), entry in log.items():
        rules.setdefault(table, set()).add(entry["rule"])
    assert rules["edges"] == {"both_end_nodes"}
    assert rules["rooms"] == {"boundary_set"}
    assert rules["walls"] == {"wall_span"}
    assert len([k for k in log if k[0] == "edges"]) == 7
    assert len([k for k in log if k[0] == "rooms"]) == 2
    assert len([k for k in log if k[0] == "walls"]) == 5


def test_moved_wall_records_its_move(moved):
    db, _, result = moved
    walls = log_rows(db, result.batch_id, "walls")
    moves = sorted(round(e["moved_mm"]) for e in walls.values())
    assert moves == [0, 0, 0, 0, 500]
    assert [e["classification"] for e in walls.values()].count("moved") == 1


def test_log_is_a_history_per_batch(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, BASE)
    second = import_version(db, BASE)
    third = import_version(db, BASE)
    batches = {b for (b,) in read_rows(db, "SELECT DISTINCT batch_id FROM match_log")}
    assert batches == {second.batch_id, third.batch_id}
    assert len(log_rows(db, second.batch_id)) == len(log_rows(db, third.batch_id)) == 6 + 7 + 2 + 5


# --- Counts -------------------------------------------------------------------


def test_counts_for_a_clean_move(moved):
    _, _, result = moved
    assert result.match_counts == {"auto": 6 + 7 + 2 + 5, "to_person": 0}


def test_counts_when_a_node_moves_too_far(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, build_dxf(tmp_path / "base.dxf", boundary=SQUARE + [((2300, 0), (2300, 4000))],
                                 walls=SQUARE + [((2300, 0), (2300, 4000))], names=ROOM_101))
    result = import_version(db, build_dxf(tmp_path / "far.dxf", boundary=SQUARE + [((3500, 0), (3500, 4000))],
                                          walls=SQUARE + [((3500, 0), (3500, 4000))], names=ROOM_101))
    assert result.match_counts["to_person"] >= 2  # the two T-junction nodes at least
    assert result.match_counts["auto"] >= 4  # the four corners at least
    log = log_rows(db, result.batch_id)
    handed = [e for e in log.values() if e["review"]]
    assert len(handed) == result.match_counts["to_person"]
    # Everything undecided is handed to a person, with its candidates
    undecided = [e for e in log.values() if e["classification"] == "undecided"]
    assert undecided and all(e["review"] and e["candidates"] for e in undecided)


def test_cli_import_prints_the_counts(tmp_path, capsys):
    from building_db.cli import main

    db = tmp_path / "b.sqlite"
    import_version(db, BASE)
    capsys.readouterr()
    assert main([
        "import", str(MOVED), "--db", str(db),
        "--wall-types", str(WALL_TYPES_CSV),
        "--finish-schedule", str(FINISH_SCHEDULE_CSV),
        "--default-wall-type", DEFAULT_WALL_TYPE,
    ]) == 0
    out = capsys.readouterr().out
    assert "20 matched automatically" in out
    assert "0 for a person" in out


# --- Elements no longer on the drawing ------------------------------------------


@pytest.fixture
def partition_removed(tmp_path):
    """Base: step 2 (5 walls, 2 rooms). New: the partition is gone (4 walls, 1 room)."""
    db = tmp_path / "b.sqlite"
    first = import_version(db, BASE)
    partition = next(
        w for w, g in read_rows(db, "SELECT wall_id, observed_geometry FROM walls")
        if round(json.loads(g)[0][0]) == 2300
    )
    second = import_version(db, build_dxf(tmp_path / "one.dxf", boundary=SQUARE, walls=SQUARE,
                                          names=((3000.0, 2000.0, "101 事務室"),)))
    return db, partition, first, second


def test_every_matched_element_records_the_batch_it_was_last_seen_in(moved):
    db, _, result = moved
    for table in ("nodes", "edges", "rooms", "walls"):
        seen = {b for (b,) in read_rows(db, f"SELECT last_seen_batch FROM {table}")}
        assert seen == {result.batch_id}


def test_removed_wall_stays_in_the_db_with_its_old_batch(partition_removed):
    db, partition, first, second = partition_removed
    rows = read_rows(db, f"SELECT last_seen_batch FROM walls WHERE wall_id = '{partition}'")
    assert rows == [(first.batch_id,)]
    others = read_rows(db, f"SELECT DISTINCT last_seen_batch FROM walls WHERE wall_id != '{partition}'")
    assert others == [(second.batch_id,)]


def test_removed_wall_is_logged_as_missing(partition_removed):
    db, partition, _, second = partition_removed
    entry = log_rows(db, second.batch_id, "walls")[("walls", partition)]
    assert entry["classification"] == "missing_candidate"


def test_removed_wall_is_not_in_the_totals(partition_removed):
    from building_db.calc import wall_lengths_m

    db, partition, _, _ = partition_removed
    conn = sqlite3.connect(db)
    try:
        lengths = wall_lengths_m(conn)
    finally:
        conn.close()
    assert partition not in lengths
    assert sum(lengths.values()) == pytest.approx(20.000)


def test_report_lists_removed_elements_apart(partition_removed, capsys):
    from building_db.cli import main

    db, partition, _, _ = partition_removed
    assert main(["report", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "total 20.000 m (4 walls)" in out
    assert "not on the latest drawing" in out.lower()
    assert partition in out
