"""Decisions on wall deviations (SPEC.md 8, expected_diff.md "判断の記録").

The API-shape tests live in test_step2_diff.py. These cover what they leave
out: a decision needs a deviation to decide on, the premise is recorded as a
fact of the moment (SPEC.md 3), and a premise that no longer holds is reported.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from test_step2_matching import drawing, import_version, read_rows

STEP2 = Path(__file__).resolve().parent.parent / "samples" / "step2"
ALIGN = "align_to_boundary"
UNFOLLOW = "unfollow"


def partition_wall_id(db):
    for wall_id, geometry in read_rows(db, "SELECT wall_id, observed_geometry FROM walls"):
        (x1, _), (x2, _) = json.loads(geometry)
        if round(x1) == round(x2) and round(x1) not in (0, 6000):
            return wall_id
    raise AssertionError("no partition wall")


@pytest.fixture
def deviating(tmp_path):
    """Base: partition at 2300. New: boundary moved to 2800, line left at 2300."""
    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    import_version(db, drawing(tmp_path, "boundary", [2800], wall_xs=[2300]))
    return db, partition_wall_id(db)


def test_decision_needs_a_deviation(tmp_path):
    from building_db.versions import record_decision

    db = tmp_path / "b.sqlite"
    import_version(db, drawing(tmp_path, "base", [2300]))
    wall_id = partition_wall_id(db)
    with pytest.raises(ValueError, match="no deviation"):
        record_decision(db, wall_id, ALIGN)
    assert read_rows(db, "SELECT COUNT(*) FROM decisions") == [(0,)]


def test_decision_needs_an_existing_wall(deviating):
    from building_db.versions import record_decision

    db, _ = deviating
    with pytest.raises(ValueError, match="A-WL-9999"):
        record_decision(db, "A-WL-9999", ALIGN)


def test_decision_needs_a_known_choice(deviating):
    from building_db.versions import record_decision

    db, wall_id = deviating
    with pytest.raises(ValueError, match="choice"):
        record_decision(db, wall_id, "snap_it")
    assert read_rows(db, "SELECT COUNT(*) FROM decisions") == [(0,)]


def test_decided_on_defaults_to_today(deviating):
    from building_db.versions import decisions_for, record_decision

    db, wall_id = deviating
    record_decision(db, wall_id, UNFOLLOW)
    (record,) = decisions_for(db, wall_id)
    assert record.decided_on == dt.date.today().isoformat()


def test_premise_is_recorded_as_a_fact_of_the_moment(deviating):
    # SPEC.md 3: the premise is stored, not recomputed from the current state
    from building_db.versions import decisions_for, record_decision

    db, wall_id = deviating
    record_decision(db, wall_id, ALIGN, decided_on="2026-11-05")
    (record,) = decisions_for(db, wall_id)
    assert record.premise["observed_geometry"] == ((2300, 0), (2300, 4000))
    assert record.premise["expected_geometry"] == ((2800, 0), (2800, 4000))
    assert record.premise["distance_mm"] == pytest.approx(500)
    assert record.premise_valid is True


def test_decisions_are_a_history(deviating):
    from building_db.versions import decisions_for, record_decision

    db, wall_id = deviating
    record_decision(db, wall_id, UNFOLLOW, reason="first thought", decided_on="2026-11-04")
    record_decision(db, wall_id, ALIGN, reason="changed my mind", decided_on="2026-11-05")
    records = decisions_for(db, wall_id)
    assert [(r.choice, r.decided_on) for r in records] == [(UNFOLLOW, "2026-11-04"), (ALIGN, "2026-11-05")]


def test_decision_is_recorded_with_its_batch(deviating):
    from building_db.versions import record_decision

    db, wall_id = deviating
    record_decision(db, wall_id, ALIGN, decided_on="2026-11-05")
    (latest,) = read_rows(db, "SELECT MAX(batch_id) FROM import_batches")[0]
    assert read_rows(db, "SELECT batch_id FROM decisions") == [(latest,)]


def test_premise_no_longer_holds_when_the_edge_moves_again(deviating, tmp_path):
    # After "align", the followed edge moves once more: the decision's premise
    # (expected x = 2800) is no longer true. It is marked and reported, not applied.
    from building_db.versions import decisions_for, record_decision

    db, wall_id = deviating
    record_decision(db, wall_id, ALIGN, decided_on="2026-11-05")
    result = import_version(db, drawing(tmp_path, "again", [3200], wall_xs=[2300]))
    (record,) = decisions_for(db, wall_id)
    assert record.premise_valid is False
    assert [f for f in result.review_flags if wall_id in f and "premise" in f]
    (adopted,) = read_rows(db, f"SELECT adopted_geometry FROM walls WHERE wall_id = '{wall_id}'")[0]
    assert round(json.loads(adopted)[0][0]) == 2800  # not moved to 3200 without a new decision


def test_premise_still_holds_when_the_drawing_is_fixed(tmp_path):
    from building_db.versions import decisions_for, record_decision

    db = tmp_path / "b.sqlite"
    import_version(db, STEP2 / "step2.dxf")
    import_version(db, STEP2 / "step2_boundary_only.dxf")
    wall_id = partition_wall_id(db)
    record_decision(db, wall_id, ALIGN, decided_on="2026-11-05")
    import_version(db, STEP2 / "step2_moved.dxf")
    (record,) = decisions_for(db, wall_id)
    assert record.premise_valid is True
