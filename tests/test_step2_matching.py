"""Matching elements across versions (SPEC.md 8: Unknown, proposal (a)-(e)).

Built up in order: nodes, edges, rooms, walls. Cases outside the samples use
drawings made in a temporary folder.
"""

import json
import sqlite3

import pytest

from conftest import DEFAULT_WALL_TYPE, FINISH_SCHEDULE_CSV, WALL_TYPES_CSV
from test_step1_review import H, W, build_dxf

SQUARE = [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))]
ROOM_101 = ((4500.0, 2000.0, "101 事務室"),)


def import_version(db, dxf):
    from building_db.importer import import_drawing

    return import_drawing(
        db_path=db,
        dxf_path=dxf,
        wall_types_csv=WALL_TYPES_CSV,
        finish_schedule_csv=FINISH_SCHEDULE_CSV,
        default_wall_type=DEFAULT_WALL_TYPE,
    )


def partitions(*xs):
    return [((x, 0), (x, H)) for x in xs]


def drawing(tmp_path, name, boundary_xs=(), wall_xs=None, names=ROOM_101):
    """A 6000 x 4000 outline with vertical partitions at the given x positions."""
    wall_xs = boundary_xs if wall_xs is None else wall_xs
    return build_dxf(
        tmp_path / f"{name}.dxf",
        boundary=SQUARE + partitions(*boundary_xs),
        walls=SQUARE + partitions(*wall_xs),
        names=names,
    )


def has_flag(flags, *parts):
    return any(all(p in f for p in parts) for f in flags)


def read_rows(db, sql):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# --- Nodes: the matching rule on its own ---------------------------------------

STEP2_NODES = {
    "N1": (0, 0), "N2": (6000, 0), "N3": (6000, 4000), "N4": (0, 4000),
    "T5": (2300, 0), "T6": (2300, 4000),
}
STEP2_EDGES = [
    ("N1", "T5"), ("T5", "N2"), ("N4", "T6"), ("T6", "N3"),
    ("N2", "N3"), ("N1", "N4"), ("T5", "T6"),
]


def step2_shape(x):
    """New nodes and edges (by index) of the step 2 shape with the partition at x."""
    nodes = [(0, 0), (6000, 0), (6000, 4000), (0, 4000), (x, 0), (x, 4000)]
    edges = [(0, 4), (4, 1), (3, 5), (5, 2), (1, 2), (0, 3), (4, 5)]
    return nodes, edges


def test_nodes_nothing_to_match_on_the_first_version():
    from building_db.matching import match_nodes

    nodes, edges = step2_shape(2300)
    result = match_nodes({}, [], nodes, edges)
    assert result.matched == {}
    assert result.problems == []


def test_nodes_in_the_same_place_are_anchors():
    from building_db.matching import match_nodes

    nodes, edges = step2_shape(2300)
    result = match_nodes(STEP2_NODES, STEP2_EDGES, nodes, edges)
    assert result.matched == {0: "N1", 1: "N2", 2: "N3", 3: "N4", 4: "T5", 5: "T6"}
    assert result.problems == []
    assert all(r.rule == "same_position" and r.moved_mm == 0 for r in result.reasons.values())


def test_moved_nodes_are_found_through_connections():
    # (2800, 0) has the same neighbors as (2300, 0), and no other node does
    from building_db.matching import match_nodes

    nodes, edges = step2_shape(2800)
    result = match_nodes(STEP2_NODES, STEP2_EDGES, nodes, edges)
    assert result.matched[4] == "T5"
    assert result.matched[5] == "T6"
    assert result.problems == []
    reason = result.reasons[4]
    assert reason.rule == "connectivity"
    assert reason.moved_mm == pytest.approx(500)
    assert set(reason.anchors) == {"N1", "N2"}


def test_move_at_the_limit_is_matched():
    from building_db.matching import MAX_MOVE_MM, match_nodes

    nodes, edges = step2_shape(2300 + MAX_MOVE_MM)
    assert match_nodes(STEP2_NODES, STEP2_EDGES, nodes, edges).matched[4] == "T5"


def test_move_over_the_limit_is_not_matched_and_is_reported():
    # SPEC.md 8 (d): over the limit is a candidate for "removed + added"
    from building_db.matching import MAX_MOVE_MM, match_nodes

    nodes, edges = step2_shape(2300 + MAX_MOVE_MM + 1)
    result = match_nodes(STEP2_NODES, STEP2_EDGES, nodes, edges)
    assert 4 not in result.matched and 5 not in result.matched
    assert has_flag(result.problems, "T5", "over the limit")
    assert has_flag(result.problems, "T6", "over the limit")


def test_no_anchor_matches_nothing_and_is_reported():
    # SPEC.md 8 (d): every node moved, so there is nothing to start from
    from building_db.matching import match_nodes

    nodes = [(x + 100, y) for x, y in STEP2_NODES.values()]
    _, edges = step2_shape(2300)
    result = match_nodes(STEP2_NODES, STEP2_EDGES, nodes, edges)
    assert result.matched == {}
    assert has_flag(result.problems, "No node stayed in place")


def test_changed_connections_are_not_matched_and_are_reported():
    # SPEC.md 8 (d): a moved node with one more edge is not the same for sure
    from building_db.matching import match_nodes

    nodes, edges = step2_shape(2800)
    nodes = nodes + [(2800, -1000)]
    edges = edges + [(4, 6)]
    result = match_nodes(STEP2_NODES, STEP2_EDGES, nodes, edges)
    assert 4 not in result.matched
    assert has_flag(result.problems, "T5", "connections changed")


def test_added_node_in_a_new_place_is_not_a_problem():
    # SPEC.md 8: an added element goes to the diff; it is not a review item by itself
    from building_db.matching import match_nodes

    nodes, edges = step2_shape(2300)
    nodes = nodes + [(3000, 0)]
    edges = edges + [(1, 6)]
    result = match_nodes(STEP2_NODES, STEP2_EDGES, nodes, edges)
    assert 6 not in result.matched
    assert result.problems == []


# --- Nodes: through the import --------------------------------------------------


def test_import_moves_the_node_and_keeps_its_id(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = dict(((round(x), round(y)), i) for i, x, y in read_rows(db, "SELECT node_id, x, y FROM nodes"))
    import_version(db, drawing(tmp_path, "moved", [2800]))
    after = {i: (round(x), round(y)) for i, x, y in read_rows(db, "SELECT node_id, x, y FROM nodes")}
    assert after[before[(2300, 0)]] == (2800, 0)
    assert after[before[(2300, 4000)]] == (2800, 4000)
    assert len(after) == 6


def test_import_over_the_limit_makes_new_nodes_and_flags(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    result = import_version(db, drawing(tmp_path, "far", [3500]))
    rows = read_rows(db, "SELECT x FROM nodes")
    assert {2300, 3500} <= {round(x) for (x,) in rows}  # nothing is deleted from the DB
    assert has_flag(result.review_flags, "over the limit")


def test_nearby_candidates_are_not_decided():
    # SPEC.md 8 (a): both moved nodes are within the limit of both previous nodes
    from building_db.matching import match_nodes

    prev_nodes = {"A": (0, 0), "B": (5000, 5000), "P": (1000, 2000), "Q": (1200, 1800)}
    prev_edges = [("A", "P"), ("P", "B"), ("A", "Q"), ("Q", "B")]
    nodes = [(0, 0), (5000, 5000), (1050, 1950), (1150, 1850)]
    edges = [(0, 2), (2, 1), (0, 3), (3, 1)]
    result = match_nodes(prev_nodes, prev_edges, nodes, edges)
    assert result.matched == {0: "A", 1: "B"}
    assert has_flag(result.problems, "P", "Q", "more than one")


# --- Edges: the same two nodes make the same edge --------------------------------


def edge_ids_by_points(db):
    rows = read_rows(
        db,
        """SELECT e.edge_id, ns.x, ns.y, ne.x, ne.y FROM edges e
           JOIN nodes ns ON ns.node_id = e.node_start
           JOIN nodes ne ON ne.node_id = e.node_end""",
    )
    return {((round(x1), round(y1)), (round(x2), round(y2))): i for i, x1, y1, x2, y2 in rows}


def test_edges_keep_their_ids_when_both_nodes_match(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = edge_ids_by_points(db)
    import_version(db, drawing(tmp_path, "moved", [2800]))
    after = edge_ids_by_points(db)
    assert len(after) == 7
    assert after[((2800, 0), (2800, 4000))] == before[((2300, 0), (2300, 4000))]
    assert after[((0, 0), (2800, 0))] == before[((0, 0), (2300, 0))]
    assert after[((2800, 0), (6000, 0))] == before[((2300, 0), (6000, 0))]


def test_edge_with_an_unmatched_node_gets_a_new_id(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = edge_ids_by_points(db)
    import_version(db, drawing(tmp_path, "far", [3500]))
    after = edge_ids_by_points(db)
    assert after[((3500, 0), (3500, 4000))] not in before.values()
    assert after[((2300, 0), (2300, 4000))] == before[((2300, 0), (2300, 4000))]  # still in the DB


def test_adding_a_partition_keeps_the_edges_it_does_not_touch(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base"))
    before = edge_ids_by_points(db)
    import_version(db, drawing(tmp_path, "added", [2300]))
    after = edge_ids_by_points(db)
    assert after[((6000, 0), (6000, 4000))] == before[((6000, 0), (6000, 4000))]
    assert after[((0, 0), (0, 4000))] == before[((0, 0), (0, 4000))]
    assert after[((0, 0), (2300, 0))] not in before.values()


# --- Rooms: the same boundary edges make the same room ---------------------------


def room_ids_by_xmax(db):
    rows = read_rows(
        db,
        """SELECT rb.room_id, MAX(n.x) FROM room_boundaries rb
           JOIN edges e ON e.edge_id = rb.edge_id
           JOIN nodes n ON n.node_id IN (e.node_start, e.node_end)
           GROUP BY rb.room_id""",
    )
    return {round(xmax): room_id for room_id, xmax in rows}


def test_rooms_keep_their_ids_when_the_partition_moves(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = room_ids_by_xmax(db)
    result = import_version(db, drawing(tmp_path, "moved", [2800]))
    assert room_ids_by_xmax(db) == {2800: before[2300], 6000: before[6000]}
    assert not has_flag(result.review_flags, "split or merge")


def test_room_with_a_new_name_on_the_same_boundary_keeps_its_id(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base"))
    before = read_rows(db, "SELECT room_id FROM rooms")
    names = ((4500.0, 2000.0, "102 会議室"),)
    result = import_version(db, drawing(tmp_path, "renamed", names=names))
    assert read_rows(db, "SELECT room_id, room_no, name FROM rooms") == [(before[0][0], "102", "会議室")]
    assert not has_flag(result.review_flags, "split or merge")


def test_split_room_is_reported_and_the_named_side_keeps_the_id(tmp_path):
    # SPEC.md 8 (d): proposal: the side with the room name keeps the ID
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base"))
    ((old_id,),) = read_rows(db, "SELECT room_id FROM rooms")
    result = import_version(db, drawing(tmp_path, "split", [2300]))
    rooms = dict(read_rows(db, "SELECT room_id, room_no FROM rooms"))
    assert rooms[old_id] == "101"
    assert len(rooms) == 2
    flags = [f for f in result.review_flags if "split or merge" in f]
    assert len(flags) == 2
    assert all(old_id in f for f in flags)


def test_merged_room_is_reported_and_nothing_is_deleted(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = room_ids_by_xmax(db)
    result = import_version(db, drawing(tmp_path, "merged"))
    assert has_flag(result.review_flags, before[6000], "split or merge")
    assert {r for (r,) in read_rows(db, "SELECT room_id FROM rooms")} == set(before.values())


def test_new_number_and_new_shape_at_once_gets_a_new_id_and_is_reported(tmp_path):
    # SPEC.md 8 (d): name and shape changed together
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = room_ids_by_xmax(db)
    names = ((4500.0, 2000.0, "102 会議室"),)
    result = import_version(db, drawing(tmp_path, "both", names=names))
    new_ids = {r for (r,) in read_rows(db, "SELECT room_id FROM rooms WHERE room_no = '102'")}
    assert len(new_ids) == 1 and not new_ids & set(before.values())
    assert has_flag(result.review_flags, before[6000], "split or merge")


def drawing_in_order(tmp_path, name, boundary_xs, names, partitions_first):
    """Like drawing(), but the order of the boundary lines in the file is chosen.

    The order decides the order of the contours during import, which must not
    change which room keeps which ID.
    """
    parts = partitions(*boundary_xs)
    boundary = parts + SQUARE if partitions_first else SQUARE + parts
    return build_dxf(tmp_path / f"{name}.dxf", boundary=boundary, walls=SQUARE + parts, names=names)


@pytest.mark.parametrize("partitions_first", [False, True])
def test_boundary_match_wins_over_room_number_in_any_order(tmp_path, partitions_first):
    # Base: left room unnamed, right room 101. New: the left room is split again and
    # its left part is named 101; the right room keeps its shape but loses its name.
    # The right room must keep its ID by boundary; the part named 101 must not take it.
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = room_ids_by_xmax(db)
    names = ((500.0, 2000.0, "101 事務室"),)
    result = import_version(
        db, drawing_in_order(tmp_path, "new", [1000, 2300], names, partitions_first))
    rooms = dict(read_rows(db, "SELECT room_id, room_no FROM rooms"))
    assert rooms[before[6000]] is None  # kept by boundary, now unnamed
    assert room_ids_by_xmax(db)[6000] == before[6000]
    named = [r for r, no in rooms.items() if no == "101"]
    assert len(named) == 1 and named[0] not in before.values()  # a new room
    assert has_flag(result.review_flags, "101", before[6000], "conflict")


@pytest.mark.parametrize("partitions_first", [False, True])
def test_room_number_claimed_by_two_contours_is_not_inherited(tmp_path, partitions_first):
    # Base: one room 101. New: split in two, both halves named 101. Neither half
    # can be told to be the old room, so neither inherits; the old room stays.
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base"))
    ((old_id,),) = read_rows(db, "SELECT room_id FROM rooms")
    names = ((1000.0, 2000.0, "101 事務室"), (4500.0, 2000.0, "101 事務室"))
    result = import_version(
        db, drawing_in_order(tmp_path, "two", [2300], names, partitions_first))
    # The two contours of the new drawing are the rooms bounded by the partition.
    partition = edge_ids_by_points(db)[((2300, 0), (2300, 4000))]
    contour_rooms = {r for (r,) in read_rows(
        db, f"SELECT room_id FROM room_boundaries WHERE edge_id = '{partition}'")}
    assert len(contour_rooms) == 2
    assert old_id not in contour_rooms  # the old room is not carried into either contour
    numbers = dict(read_rows(db, "SELECT room_id, room_no FROM rooms"))
    assert all(numbers[r] == "101" for r in contour_rooms)  # each contour is its own new room
    assert has_flag(result.review_flags, "101", "2 contours")


# --- Walls: through the followed edges, then directly -----------------------------


def wall_ids_by_line(db):
    """{(x1, y1, x2, y2) of the observed line: [wall IDs]}"""
    lines = {}
    for wall_id, geometry in read_rows(db, "SELECT wall_id, observed_geometry FROM walls"):
        (x1, y1), (x2, y2) = json.loads(geometry)
        lines.setdefault((round(x1), round(y1), round(x2), round(y2)), []).append(wall_id)
    return lines


SOUTH = (0, 0, 6000, 0)
NORTH = (0, 4000, 6000, 4000)


def line_at(x):
    return (x, 0, x, 4000)


def count_walls(db):
    return read_rows(db, "SELECT COUNT(*) FROM walls")[0][0]


def test_walls_keep_their_ids_through_the_followed_edges(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    before = wall_ids_by_line(db)
    result = import_version(db, drawing(tmp_path, "moved", [2800]))
    after = wall_ids_by_line(db)
    assert count_walls(db) == 5
    assert after[line_at(2800)] == before[line_at(2300)]
    assert after[SOUTH] == before[SOUTH]
    assert result.review_flags == [f for f in result.review_flags if "no room name" in f]


def test_wall_left_behind_is_matched_directly_and_keeps_its_edge(tmp_path):
    # Only the boundary moved: the line at x = 2300 follows no edge any more
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    wall_id = wall_ids_by_line(db)[line_at(2300)][0]
    before_spans = read_rows(db, f"SELECT edge_id FROM wall_edge_spans WHERE wall_id = '{wall_id}'")
    import_version(db, drawing(tmp_path, "boundary", [2800], wall_xs=[2300]))
    assert count_walls(db) == 5
    assert wall_ids_by_line(db)[line_at(2300)] == [wall_id]
    assert read_rows(db, f"SELECT edge_id FROM wall_edge_spans WHERE wall_id = '{wall_id}'") == before_spans
    assert read_rows(db, f"SELECT follow_edge FROM walls WHERE wall_id = '{wall_id}'") == [(1,)]


def test_wall_moved_alone_is_matched_directly(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    wall_id = wall_ids_by_line(db)[line_at(2300)][0]
    import_version(db, drawing(tmp_path, "wall", [2300], wall_xs=[2500]))
    assert count_walls(db) == 5
    assert wall_ids_by_line(db)[line_at(2500)] == [wall_id]


def test_wall_moved_over_the_limit_gets_a_new_id_and_is_reported(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    wall_id = wall_ids_by_line(db)[line_at(2300)][0]
    result = import_version(db, drawing(tmp_path, "far", [2300], wall_xs=[3500]))
    assert count_walls(db) == 6  # the old wall stays in the DB
    assert wall_ids_by_line(db)[line_at(3500)] != [wall_id]
    assert has_flag(result.review_flags, wall_id, "over the limit")


def test_wall_with_more_than_one_candidate_is_not_decided(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2000, 2600]))
    before = wall_ids_by_line(db)
    result = import_version(db, drawing(tmp_path, "both", [2000, 2600], wall_xs=[2200, 2400]))
    after = wall_ids_by_line(db)
    old = {before[line_at(2000)][0], before[line_at(2600)][0]}
    assert not old & set(after[line_at(2200)] + after[line_at(2400)])
    assert has_flag(result.review_flags, *old, "more than one")


def test_wall_over_a_changed_number_of_edges_is_reported(tmp_path):
    # SPEC.md 8 (d): the south wall now spans two edges and touches a new wall
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base"))
    before = wall_ids_by_line(db)
    result = import_version(db, drawing(tmp_path, "added", [2300]))
    assert wall_ids_by_line(db)[SOUTH] != before[SOUTH]
    assert has_flag(result.review_flags, before[SOUTH][0], "connections changed")
    assert has_flag(result.review_flags, before[NORTH][0], "connections changed")


def test_wall_without_a_boundary_keeps_its_id_on_reimport(tmp_path):
    db = tmp_path / "b.sqlite"
    dxf = drawing(tmp_path, "extra", wall_xs=[3000])
    import_version(db, dxf)
    before = wall_ids_by_line(db)
    import_version(db, dxf)
    assert wall_ids_by_line(db) == before


def test_wall_touching_nothing_keeps_its_id_on_reimport(tmp_path):
    db = tmp_path / "b.sqlite"
    dxf = build_dxf(
        tmp_path / "alone.dxf", boundary=SQUARE,
        walls=SQUARE + [((1000, 1000), (2000, 1000))], names=ROOM_101,
    )
    import_version(db, dxf)
    before = wall_ids_by_line(db)
    import_version(db, dxf)
    assert wall_ids_by_line(db) == before
