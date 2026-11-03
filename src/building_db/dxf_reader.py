"""Read a DXF drawing as observed values.

- Layer names are normalized: the Jw_cad prefix such as "_0-2_" is removed.
- Coordinates are moved so that the X1/Y1 grid intersection is the origin.
- Units are assumed to be mm (SPEC.md 2). A warning is added if the header
  says otherwise.
- Text is decoded as cp932 when the header has no $DWGCODEPAGE (Jw_cad export).

Nothing here is corrected silently: anything unexpected becomes a warning.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from ezdxf import recover

from building_db.layers import FIXED_LAYERS, GRID

# Jw_cad writes layers as "_<group>-<layer>_<name>", group and layer in hex 0-F.
_JW_PREFIX = re.compile(r"^_[0-9A-Fa-f]-[0-9A-Fa-f]_")

# $INSUNITS value for millimeters.
_INSUNITS_MM = 4


@dataclass(frozen=True)
class Line:
    layer: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)


@dataclass(frozen=True)
class Text:
    layer: str
    text: str
    x: float
    y: float


@dataclass(frozen=True)
class Grid:
    label: str
    direction: str  # "X": a line of constant x (X1, X2...), "Y": constant y
    position: float


@dataclass
class Drawing:
    path: Path
    layers: list[str]
    lines: list[Line]
    texts: list[Text]
    grids: list[Grid]
    origin: tuple[float, float]  # raw DXF coordinates of the X1/Y1 intersection
    # Drawing-level problems a person should look at.
    warnings: list[str] = field(default_factory=list)
    # File-format repairs made by ezdxf (e.g. unnamed table entries).
    repairs: list[str] = field(default_factory=list)

    def lines_on(self, layer: str) -> list[Line]:
        return [ln for ln in self.lines if ln.layer == layer]

    def texts_on(self, layer: str) -> list[Text]:
        return [t for t in self.texts if t.layer == layer]


def normalize_layer_name(raw: str) -> str:
    """Strip the Jw_cad prefix such as "_0-2_" from a layer name."""
    return _JW_PREFIX.sub("", raw, count=1)


def read_drawing(path: Path) -> Drawing:
    """Read the fixed layers of a DXF file and return a Drawing."""
    path = Path(path)
    warnings: list[str] = []

    doc, repairs = _read_document(path)
    _check_units(doc, warnings)

    layers = sorted({normalize_layer_name(layer.dxf.name) for layer in doc.layers})

    raw_lines: list[Line] = []
    raw_texts: list[Text] = []
    raw_circles: list[tuple[float, float]] = []  # grid bubble centers
    for entity in doc.modelspace():
        layer = normalize_layer_name(entity.dxf.layer)
        kind = entity.dxftype()
        if kind == "LINE":
            s, e = entity.dxf.start, entity.dxf.end
            raw_lines.append(Line(layer, s.x, s.y, e.x, e.y))
        elif kind == "TEXT":
            x, y = _text_location(entity)
            raw_texts.append(Text(layer, entity.dxf.text, x, y))
        elif kind == "CIRCLE" and layer == GRID:
            c = entity.dxf.center
            raw_circles.append((c.x, c.y))
        elif layer in FIXED_LAYERS:
            warnings.append(f"Unsupported entity {kind} on fixed layer {layer}")

    raw_grids = _find_grids(raw_lines, raw_texts, raw_circles, warnings)
    origin = _find_origin(raw_grids, warnings)
    ox, oy = origin

    return Drawing(
        path=path,
        layers=layers,
        lines=[Line(ln.layer, ln.x1 - ox, ln.y1 - oy, ln.x2 - ox, ln.y2 - oy) for ln in raw_lines],
        texts=[Text(t.layer, t.text, t.x - ox, t.y - oy) for t in raw_texts],
        grids=[
            Grid(g.label, g.direction, g.position - (ox if g.direction == "X" else oy))
            for g in raw_grids
        ],
        origin=origin,
        warnings=warnings,
        repairs=repairs,
    )


def _read_document(path: Path):
    """Read with ezdxf's recover mode; return (document, repair messages).

    Jw_cad exports have table entries without names and duplicate handles,
    which the strict loader rejects. Recover mode repairs them, and the
    repairs are reported instead of being hidden.
    """
    data = path.read_bytes()
    if b"$DWGCODEPAGE" not in data[:4096]:
        # Without $DWGCODEPAGE ezdxf decodes text as cp1252, which garbles
        # Japanese. Insert the code page into an in-memory copy only.
        data = _insert_codepage(data, b"ANSI_932")
    doc, auditor = recover.read(io.BytesIO(data))
    repairs = [f"{e.message}" for e in auditor.fixes] + [f"{e.message}" for e in auditor.errors]
    return doc, repairs


def _insert_codepage(data: bytes, codepage: bytes) -> bytes:
    newline = b"\r\n" if b"\r\n" in data[:200] else b"\n"
    marker = b"HEADER" + newline
    pos = data.find(marker)
    if pos < 0:
        return data
    pos += len(marker)
    tag = b"  9" + newline + b"$DWGCODEPAGE" + newline + b"  3" + newline + codepage + newline
    return data[:pos] + tag + data[pos:]


def _check_units(doc, warnings: list[str]) -> None:
    units = doc.header.get("$INSUNITS")
    if units not in (None, 0, _INSUNITS_MM):
        warnings.append(f"$INSUNITS is {units}, not mm; coordinates are read as mm")


def _text_location(entity) -> tuple[float, float]:
    # With any alignment other than left/baseline, the align point is the anchor.
    if entity.dxf.get("halign", 0) or entity.dxf.get("valign", 0):
        p = entity.dxf.get("align_point", entity.dxf.insert)
    else:
        p = entity.dxf.insert
    return p.x, p.y


def _find_grids(
    lines: list[Line],
    texts: list[Text],
    circles: list[tuple[float, float]],
    warnings: list[str],
) -> list[Grid]:
    """Pair each grid label with its grid line (raw coordinates)."""
    grid_lines = [ln for ln in lines if ln.layer == GRID]
    grids: list[Grid] = []
    for t in texts:
        if t.layer != GRID:
            continue
        # The label sits in a bubble; the bubble center lies on the grid line.
        anchor = min(circles, key=lambda c: math.dist(c, (t.x, t.y)), default=(t.x, t.y))
        best = min(grid_lines, key=lambda ln: _distance_to_infinite_line(ln, anchor), default=None)
        if best is None:
            warnings.append(f"Grid label {t.text} has no grid line")
            continue
        if math.isclose(best.x1, best.x2):
            grids.append(Grid(t.text, "X", best.x1))
        elif math.isclose(best.y1, best.y2):
            grids.append(Grid(t.text, "Y", best.y1))
        else:
            warnings.append(f"Grid {t.text} is neither vertical nor horizontal")
    return grids


def _distance_to_infinite_line(ln: Line, p: tuple[float, float]) -> float:
    dx, dy = ln.x2 - ln.x1, ln.y2 - ln.y1
    length = math.hypot(dx, dy)
    if length == 0:
        return math.dist((ln.x1, ln.y1), p)
    return abs(dy * (p[0] - ln.x1) - dx * (p[1] - ln.y1)) / length


def _find_origin(grids: list[Grid], warnings: list[str]) -> tuple[float, float]:
    by_label = {g.label: g for g in grids}
    x1, y1 = by_label.get("X1"), by_label.get("Y1")
    if x1 is None or y1 is None or x1.direction != "X" or y1.direction != "Y":
        warnings.append("Grid X1/Y1 not found; the DXF origin is used as is")
        return (0.0, 0.0)
    return (x1.position, y1.position)
