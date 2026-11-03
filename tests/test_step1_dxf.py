"""Step 1: reading the DXF (layers, origin, text).

Expected values come from samples/step1/expected.md, section "図面の約束"
and the "通り芯" row of the expected-values table.
"""

import pytest

from building_db.dxf_reader import normalize_layer_name, read_drawing

FIXED_LAYERS = {"GRID", "ROOM_BOUNDARY", "ROOM_NAME", "WALL_CENTER"}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("GRID", "GRID"),
        ("_0-2_GRID", "GRID"),  # Jw_cad: layer group 0, layer 2
        ("_0-3_ROOM_BOUNDARY", "ROOM_BOUNDARY"),
        ("_F-A_WALL_CENTER", "WALL_CENTER"),  # group/layer numbers are hex 0-F
        ("ADD_LINE", "ADD_LINE"),
    ],
)
def test_normalize_layer_name(raw, expected):
    assert normalize_layer_name(raw) == expected


def test_fixed_layers_are_present(dxf_path):
    # expected.md: GRID, ROOM_BOUNDARY, ROOM_NAME, WALL_CENTER
    drawing = read_drawing(dxf_path)
    assert FIXED_LAYERS <= set(drawing.layers)


def test_origin_is_at_grid_x1_y1(dxf_path):
    # expected.md: origin is the intersection of X1 and Y1;
    # grids X1=0, X2=6000, Y1=0, Y2=4000 (mm)
    drawing = read_drawing(dxf_path)
    positions = {g.label: (g.direction, g.position) for g in drawing.grids}
    assert positions == {
        "X1": ("X", pytest.approx(0.0)),
        "X2": ("X", pytest.approx(6000.0)),
        "Y1": ("Y", pytest.approx(0.0)),
        "Y2": ("Y", pytest.approx(4000.0)),
    }


def test_room_name_text_is_decoded(dxf_path):
    # expected.md: room 101, name "事務室" (Shift_JIS in the DXF)
    drawing = read_drawing(dxf_path)
    texts = [t.text for t in drawing.texts if t.layer == "ROOM_NAME"]
    assert texts == ["101 事務室"]


def test_lines_per_fixed_layer(dxf_path):
    # expected.md: 4 room boundary lines; 4 walls drawn as wall center lines
    drawing = read_drawing(dxf_path)
    assert len(drawing.lines_on("ROOM_BOUNDARY")) == 4
    assert len(drawing.lines_on("WALL_CENTER")) == 4
