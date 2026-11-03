"""Read the wall type table and the finish schedule (CSV, UTF-8 with BOM)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WallTypeRow:
    type_code: str
    name: str
    core: str
    core_thickness_mm: int
    board_thickness_mm: float
    boards_per_side: int
    total_thickness_mm: int
    role: str


@dataclass(frozen=True)
class FinishRow:
    room_no: str
    room_name: str
    floor: str
    baseboard: str
    wall: str
    ceiling: str
    ceiling_height_mm: int


def _rows(path: Path) -> list[dict[str, str]]:
    # utf-8-sig drops the BOM that Excel writes at the start of the file.
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def read_wall_types(path: Path) -> list[WallTypeRow]:
    return [
        WallTypeRow(
            type_code=r["type_code"],
            name=r["name"],
            core=r["core"],
            core_thickness_mm=int(r["core_thickness_mm"]),
            board_thickness_mm=float(r["board_thickness_mm"]),
            boards_per_side=int(r["boards_per_side"]),
            total_thickness_mm=int(r["total_thickness_mm"]),
            role=r["role"],
        )
        for r in _rows(path)
    ]


def read_finish_schedule(path: Path) -> list[FinishRow]:
    return [
        FinishRow(
            room_no=r["room_no"],
            room_name=r["room_name"],
            floor=r["floor"],
            baseboard=r["baseboard"],
            wall=r["wall"],
            ceiling=r["ceiling"],
            ceiling_height_mm=int(r["ceiling_height_mm"]),
        )
        for r in _rows(path)
    ]
