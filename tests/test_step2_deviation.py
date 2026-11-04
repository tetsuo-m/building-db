"""A wall whose center line left the edge it follows is a deviation, not a fix.

SPEC.md 3 and 4: observed_geometry is what the drawing shows; adopted_geometry
changes only when the line still follows its edge (a clean move) or when a
person decides. The expected position is computed from the followed edges and
never stored. A deviation is one review item naming the wall, the edge and the
distance; it is not reported as "edge without wall" + "line on no edge".
"""

import json
import sqlite3

import pytest

from test_step1_review import H, W, build_dxf
from test_step2_matching import ROOM_101, SQUARE, drawing, import_version, read_rows

ALIGN = "align_to_boundary"
UNFOLLOW = "unfollow"


def partition_wall(db):
    """(wall_id, observed x, adopted x) of the only wall that is not an outer wall."""
    found = []
    for wall_id, observed, adopted in read_rows(db, "SELECT wall_id, observed_geometry, adopted_geometry FROM walls"):
        (ox1, _), (ox2, _) = json.loads(observed)
        if round(ox1) == round(ox2) and round(ox1) not in (0, W):
            (ax1, _), _ = json.loads(adopted)
            found.append((wall_id, round(ox1), round(ax1)))
    assert len(found) == 1
    return found[0]


def provenance_batch(db, wall_id, column):
    rows = read_rows(
        db,
        f"SELECT batch_id FROM field_provenance WHERE target_table = 'walls' "
        f"AND target_id = '{wall_id}' AND field = '{column}'",
    )
    assert len(rows) == 1
    return rows[0][0]


@pytest.fixture
def line_moved_alone(tmp_path):
    """Base: partition at 2300. New: only the wall center line moved to 2500."""
    db = tmp_path / "b.sqlite"
    first = import_version(db, drawing(tmp_path, "base", [2300]))
    second = import_version(db, drawing(tmp_path, "line", [2300], wall_xs=[2500]))
    return db, first, second


def test_observed_moves_but_adopted_stays(line_moved_alone):
    db, _, _ = line_moved_alone
    _, observed_x, adopted_x = partition_wall(db)
    assert observed_x == 2500
    assert adopted_x == 2300


def test_deviation_is_reported_with_wall_edge_and_distance(line_moved_alone):
    db, _, second = line_moved_alone
    wall_id, _, _ = partition_wall(db)
    (edge_id,) = read_rows(db, f"SELECT edge_id FROM wall_edge_spans WHERE wall_id = '{wall_id}'")[0]
    deviations = second.wall_deviations
    assert len(deviations) == 1
    d = deviations[0]
    assert d.wall_id == wall_id
    assert d.edge_id == edge_id
    assert d.edge_ids == [edge_id]
    assert d.distance_mm == pytest.approx(200)
    assert d.expected_x_mm == pytest.approx(2300)
    assert d.expected_geometry == ((2300, 0), (2300, H))
    assert list(d.options) == [ALIGN, UNFOLLOW]
    flags = [f for f in second.review_flags if wall_id in f]
    assert len(flags) == 1
    assert edge_id in flags[0] and "200" in flags[0]


def test_deviation_is_not_split_into_two_flags(line_moved_alone):
    _, _, second = line_moved_alone
    assert not [f for f in second.review_flags
                if "does not follow any boundary line" in f or "has no wall" in f]
    assert len(second.review_flags) == 2  # the deviation and "no room name" (left room)


def test_adopted_provenance_is_not_overwritten_when_kept(line_moved_alone):
    db, first, second = line_moved_alone
    wall_id, _, _ = partition_wall(db)
    assert provenance_batch(db, wall_id, "adopted_geometry") == first.batch_id
    assert provenance_batch(db, wall_id, "observed_geometry") == second.batch_id


def test_wall_still_follows_its_edge(line_moved_alone):
    db, _, _ = line_moved_alone
    wall_id, _, _ = partition_wall(db)
    assert read_rows(db, f"SELECT follow_edge FROM walls WHERE wall_id = '{wall_id}'") == [(1,)]


def test_expected_position_is_not_stored(line_moved_alone):
    db, _, _ = line_moved_alone
    columns = [r[1] for r in read_rows(db, "PRAGMA table_info(walls)")]
    assert not [c for c in columns if "expected" in c]


def test_clean_move_updates_adopted_and_provenance(tmp_path):
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    second = import_version(db, drawing(tmp_path, "moved", [2800]))
    wall_id, observed_x, adopted_x = partition_wall(db)
    assert (observed_x, adopted_x) == (2800, 2800)
    assert second.wall_deviations == []
    assert provenance_batch(db, wall_id, "adopted_geometry") == second.batch_id


def test_reimport_of_the_same_drawing_has_no_deviation(tmp_path):
    db = tmp_path / "b.sqlite"
    dxf = drawing(tmp_path, "base", [2300])
    import_version(db, dxf)
    second = import_version(db, dxf)
    assert second.wall_deviations == []


def test_deviation_stays_while_the_drawing_is_unchanged(line_moved_alone):
    # Importing the deviating drawing again reports the same single deviation
    db, _, _ = line_moved_alone
    dxf_dir = db.parent
    third = import_version(db, dxf_dir / "line.dxf")
    assert len(third.wall_deviations) == 1
    _, observed_x, adopted_x = partition_wall(db)
    assert (observed_x, adopted_x) == (2500, 2300)


def test_line_on_no_edge_and_unmatched_is_still_reported_as_such(tmp_path):
    # A new wall center line with no boundary under it is not a deviation
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base"))
    second = import_version(db, build_dxf(
        tmp_path / "extra.dxf", boundary=SQUARE, walls=SQUARE + [((3000, 0), (3000, H))], names=ROOM_101))
    assert second.wall_deviations == []
    assert [f for f in second.review_flags if "does not follow any boundary line" in f]


def test_deviation_with_the_boundary_only_sample(tmp_path):
    # samples/step2/expected_diff.md: the partition's edge moved to 2800, the
    # line stayed at 2300: 500 mm, one item, adopted stays at 2300
    from pathlib import Path

    step2 = Path(__file__).resolve().parent.parent / "samples" / "step2"
    db = tmp_path / "b.sqlite"
    import_version(db, step2 / "step2.dxf")
    second = import_version(db, step2 / "step2_boundary_only.dxf")
    (d,) = second.wall_deviations
    assert d.distance_mm == pytest.approx(500)
    assert d.expected_x_mm == pytest.approx(2800)
    _, observed_x, adopted_x = partition_wall(db)
    assert (observed_x, adopted_x) == (2300, 2300)
