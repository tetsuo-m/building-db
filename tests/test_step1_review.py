"""Step 1: review flags for broken drawings.

Each test builds a one-room drawing with ezdxf in a temporary folder, breaks
one thing, and checks that
- the import still finishes and reports the matching review flag,
- the flag is stored with the import batch, and
- the import does not silently fix the problem.

The finish schedule is the step 1 sample (room 101 事務室, ceiling 2700 mm).
"""

import json
import sqlite3

import ezdxf
import pytest

from conftest import run_import

W, H = 6000.0, 4000.0  # room size on wall centers, mm
GOOD_NAME = "101 事務室"


def build_dxf(path, *, boundary=None, walls=None, names=((3000.0, 2000.0, GOOD_NAME),)):
    """Write a one-room R12 DXF like samples/step1/step1.dxf."""
    corners = [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)]
    square = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    boundary = square if boundary is None else boundary
    walls = square if walls is None else walls

    doc = ezdxf.new("R12")
    doc.header["$DWGCODEPAGE"] = "ANSI_932"  # Shift_JIS, as in the samples
    for layer in ("GRID", "ROOM_BOUNDARY", "ROOM_NAME", "WALL_CENTER"):
        doc.layers.add(layer)
    msp = doc.modelspace()

    for label, x in (("X1", 0.0), ("X2", W)):
        msp.add_line((x, -1500), (x, H + 1500), dxfattribs={"layer": "GRID"})
        msp.add_circle((x, H + 1900), 400, dxfattribs={"layer": "GRID"})
        msp.add_text(label, dxfattribs={"layer": "GRID", "insert": (x, H + 1900), "height": 300})
    for label, y in (("Y1", 0.0), ("Y2", H)):
        msp.add_line((-1500, y), (W + 1500, y), dxfattribs={"layer": "GRID"})
        msp.add_circle((-1900, y), 400, dxfattribs={"layer": "GRID"})
        msp.add_text(label, dxfattribs={"layer": "GRID", "insert": (-1900, y), "height": 300})

    for a, b in boundary:
        msp.add_line(a, b, dxfattribs={"layer": "ROOM_BOUNDARY"})
    for a, b in walls:
        msp.add_line(a, b, dxfattribs={"layer": "WALL_CENTER"})
    for x, y, text in names:
        msp.add_text(text, dxfattribs={"layer": "ROOM_NAME", "insert": (x, y), "height": 350})

    doc.saveas(path)
    return path


def import_broken(tmp_path, **kwargs):
    """Import a built drawing; return (review flags, open connection)."""
    dxf = build_dxf(tmp_path / "broken.dxf", **kwargs)
    db = tmp_path / "broken.sqlite"
    result = run_import(db, dxf)
    conn = sqlite3.connect(db)
    stored = json.loads(conn.execute("SELECT review_flags FROM import_batches").fetchone()[0])
    assert stored == result.review_flags  # flags are kept with the batch
    return result.review_flags, conn


def has_flag(flags, *parts):
    return any(all(p in f for p in parts) for f in flags)


@pytest.fixture
def conn_closer():
    conns = []
    yield conns.append
    for c in conns:
        c.close()


def test_builder_makes_a_clean_drawing(tmp_path, conn_closer):
    # Control: the unbroken drawing gives no review flags
    flags, conn = import_broken(tmp_path)
    conn_closer(conn)
    assert flags == []
    assert conn.execute("SELECT room_no, name FROM rooms").fetchall() == [("101", "事務室")]


def test_open_contour(tmp_path, conn_closer):
    # The top boundary stops 1000 mm short of the corner: the outline is open
    boundary = [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (1000, H)), ((0, H), (0, 0))]
    flags, conn = import_broken(tmp_path, boundary=boundary)
    conn_closer(conn)
    assert has_flag(flags, "Open contour")
    # not fixed: no room is made from the open outline, the gap is not closed
    assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 4
    # the room name is left without a room and reported
    assert has_flag(flags, GOOD_NAME, "not inside any contour")


def test_room_without_name(tmp_path, conn_closer):
    flags, conn = import_broken(tmp_path, names=())
    conn_closer(conn)
    assert has_flag(flags, "has no room name text")
    # not fixed: the room exists but no number or name is invented
    assert conn.execute("SELECT room_no, name FROM rooms").fetchall() == [(None, None)]


def test_room_with_two_names(tmp_path, conn_closer):
    names = ((2000.0, 2000.0, GOOD_NAME), (4000.0, 2000.0, "102 会議室"))
    flags, conn = import_broken(tmp_path, names=names)
    conn_closer(conn)
    assert has_flag(flags, "has 2 room name texts")
    # not fixed: neither name is picked
    assert conn.execute("SELECT room_no, name FROM rooms").fetchall() == [(None, None)]


def test_room_name_outside_contour(tmp_path, conn_closer):
    flags, conn = import_broken(tmp_path, names=((8000.0, 2000.0, GOOD_NAME),))
    conn_closer(conn)
    assert has_flag(flags, GOOD_NAME, "not inside any contour")
    assert has_flag(flags, "has no room name text")
    # not fixed: the outside text is not moved into the nearest room
    assert conn.execute("SELECT room_no, name FROM rooms").fetchall() == [(None, None)]


def test_room_not_in_finish_schedule(tmp_path, conn_closer):
    flags, conn = import_broken(tmp_path, names=((3000.0, 2000.0, "102 事務室"),))
    conn_closer(conn)
    assert has_flag(flags, "Room 102 is not in finish_schedule.csv")
    # not fixed: no ceiling height is borrowed from another row
    assert conn.execute("SELECT room_no, ceiling_height FROM rooms").fetchall() == [("102", None)]


def test_room_name_differs_from_finish_schedule(tmp_path, conn_closer):
    flags, conn = import_broken(tmp_path, names=((3000.0, 2000.0, "101 会議室"),))
    conn_closer(conn)
    assert has_flag(flags, "Room 101", "会議室", "事務室", "differs")
    # not fixed: the drawing's name is kept, not replaced by the schedule's
    assert conn.execute("SELECT room_no, name FROM rooms").fetchall() == [("101", "会議室")]


def test_boundary_edge_without_wall(tmp_path, conn_closer):
    # The right-hand wall (x = 6000) is not drawn
    walls = [((0, 0), (W, 0)), ((W, H), (0, H)), ((0, H), (0, 0))]
    flags, conn = import_broken(tmp_path, walls=walls)
    conn_closer(conn)
    assert has_flag(flags, "has no wall")
    # not fixed: no wall is created for the bare edge
    assert conn.execute("SELECT COUNT(*) FROM walls").fetchone()[0] == 3


def test_wall_center_not_on_boundary(tmp_path, conn_closer):
    # An extra wall center line across the room, with no boundary under it
    square = [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))]
    walls = square + [((3000, 0), (3000, H))]
    flags, conn = import_broken(tmp_path, walls=walls)
    conn_closer(conn)
    assert has_flag(flags, "does not follow any boundary line")
    # not fixed: the wall is kept where it was drawn, not snapped or dropped,
    # and it does not follow any edge
    rows = conn.execute(
        "SELECT wall_id, follow_edge, adopted_geometry FROM walls "
        "WHERE wall_id NOT IN (SELECT wall_id FROM wall_edge_spans)"
    ).fetchall()
    assert len(rows) == 1
    _, follow_edge, geometry = rows[0]
    assert follow_edge == 0
    assert json.loads(geometry) == [[3000.0, 0.0], [3000.0, H]]
    # no extra room is split off by the wall
    assert conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0] == 1
