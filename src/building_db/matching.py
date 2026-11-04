"""Decide which elements of a new version are the same as in the previous one.

Nothing here touches the DB. The approach follows SPEC.md 8 (proposal):
start from anchors, spread through connections, and decide automatically only
when one candidate remains, the move is small and the connections are the same.
Anything else keeps no ID and is reported for a person to check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Point = tuple[float, float]
Segment = tuple[Point, Point]  # ends sorted by (x, y)

# SPEC.md 8 (e): what counts as a small move, in mm (provisional).
MAX_MOVE_MM = 1000.0


@dataclass(frozen=True)
class MatchReason:
    """Why an element was matched."""

    rule: str
    moved_mm: float
    anchors: tuple[str, ...] = ()


@dataclass
class NodeMatch:
    matched: dict[int, str] = field(default_factory=dict)  # new node index -> previous node ID
    reasons: dict[int, MatchReason] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def match_nodes(
    prev_nodes: dict[str, Point],
    prev_edges: list[tuple[str, str]],
    new_nodes: list[Point],
    new_edges: list[tuple[int, int]],
    tol: float = 1.0,
    max_move: float = MAX_MOVE_MM,
) -> NodeMatch:
    """Match the nodes of a new version to the previous version's node IDs."""
    result = NodeMatch()
    if not prev_nodes or not new_nodes:
        return result

    prev_adj: dict[str, set[str]] = {p: set() for p in prev_nodes}
    for a, b in prev_edges:
        prev_adj[a].add(b)
        prev_adj[b].add(a)
    new_adj: dict[int, set[int]] = {i: set() for i in range(len(new_nodes))}
    for a, b in new_edges:
        new_adj[a].add(b)
        new_adj[b].add(a)

    # Anchors: nodes that did not move.
    taken: set[str] = set()
    for i, position in enumerate(new_nodes):
        near = [(math.dist(position, q), p) for p, q in prev_nodes.items()
                if p not in taken and math.dist(position, q) <= tol]
        if near:
            _, p = min(near)
            result.matched[i] = p
            result.reasons[i] = MatchReason("same_position", 0.0)
            taken.add(p)
    if not result.matched:
        result.problems.append("No node stayed in place, so there is nothing to start matching from")
        return result

    def known_neighbors(i: int) -> dict[str, int]:
        return {result.matched[k]: k for k in new_adj[i] if k in result.matched}

    def same_connections(i: int, p: str) -> bool:
        known = known_neighbors(i)
        return (
            bool(known)
            and len(prev_adj[p]) == len(new_adj[i])
            and set(known) == prev_adj[p] & taken
            and all(
                _direction(prev_nodes[p], prev_nodes[q], tol) == _direction(new_nodes[i], new_nodes[k], tol)
                for q, k in known.items()
            )
        )

    def candidates_of(i: int) -> list[str]:
        return [p for p in prev_nodes if p not in taken and same_connections(i, p)
                and math.dist(new_nodes[i], prev_nodes[p]) <= max_move]

    # Spread: accept only pairs where each side has a single candidate.
    while True:
        waiting = [i for i in range(len(new_nodes)) if i not in result.matched]
        candidates = {i: candidates_of(i) for i in waiting}
        accepted = [
            (i, found[0]) for i, found in candidates.items()
            if len(found) == 1 and sum(found[0] in other for other in candidates.values()) == 1
        ]
        if not accepted:
            break
        for i, p in accepted:
            result.reasons[i] = MatchReason(
                "connectivity", math.dist(new_nodes[i], prev_nodes[p]), tuple(sorted(known_neighbors(i))))
        for i, p in accepted:
            result.matched[i] = p
            taken.add(p)

    # What is left keeps no ID. Report it when a previous node looks related.
    for i in range(len(new_nodes)):
        if i in result.matched:
            continue
        where = f"Node at ({new_nodes[i][0]:.0f}, {new_nodes[i][1]:.0f})"
        known = set(known_neighbors(i))
        found = candidates_of(i)
        if found:
            result.problems.append(f"{where}: more than one match is possible ({', '.join(found)})")
            continue
        for p in prev_nodes:
            if p in taken:
                continue
            if same_connections(i, p):
                moved = math.dist(new_nodes[i], prev_nodes[p])
                result.problems.append(
                    f"{where} may be {p}, moved {moved:.0f} mm, over the limit of {max_move:.0f} mm "
                    "(candidate for removed + added)"
                )
            elif known & prev_adj[p]:
                result.problems.append(f"{where} may be {p}, but its connections changed")
    return result


@dataclass
class WallMatch:
    matched: dict[int, str] = field(default_factory=dict)  # new line index -> previous wall ID
    reasons: dict[int, MatchReason] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def match_walls(
    prev_walls: dict[str, Segment],
    new_lines: list[Segment],
    by_edges: dict[int, str],
    tol: float = 1.0,
    max_move: float = MAX_MOVE_MM,
) -> WallMatch:
    """Match wall center lines to previous walls.

    by_edges holds the lines already matched through the edges they follow.
    The other lines are compared directly: same direction, same touching walls
    and a small move (SPEC.md 8 (c)).
    """
    result = WallMatch()
    for i, wall_id in by_edges.items():
        result.matched[i] = wall_id
        result.reasons[i] = MatchReason("wall_span", _line_move(new_lines[i], prev_walls[wall_id]))
    taken = set(by_edges.values())

    prev_touch = {w: {o for o, other in prev_walls.items() if o != w and _touching(seg, other, tol)}
                  for w, seg in prev_walls.items()}
    new_touch = {i: {k for k, other in enumerate(new_lines) if k != i and _touching(seg, other, tol)}
                 for i, seg in enumerate(new_lines)}

    def known_touching(i: int) -> set[str]:
        return {result.matched[k] for k in new_touch[i] if k in result.matched}

    def same_touching(i: int, w: str) -> bool:
        known = known_touching(i)
        same = len(prev_touch[w]) == len(new_touch[i]) and known == prev_touch[w] & taken
        # With nothing to go by, only a line that did not move can be the same wall.
        return same and (bool(known) or _line_move(new_lines[i], prev_walls[w]) <= tol)

    def candidates_of(i: int) -> list[str]:
        return [w for w, seg in prev_walls.items()
                if w not in taken and _parallel(new_lines[i], seg) and same_touching(i, w)
                and _line_move(new_lines[i], seg) <= max_move]

    while True:
        waiting = [i for i in range(len(new_lines)) if i not in result.matched]
        candidates = {i: candidates_of(i) for i in waiting}
        accepted = [
            (i, found[0]) for i, found in candidates.items()
            if len(found) == 1 and sum(found[0] in other for other in candidates.values()) == 1
        ]
        if not accepted:
            break
        for i, w in accepted:
            result.reasons[i] = MatchReason(
                "wall_direct", _line_move(new_lines[i], prev_walls[w]), tuple(sorted(known_touching(i))))
        for i, w in accepted:
            result.matched[i] = w
            taken.add(w)

    for i, seg in enumerate(new_lines):
        if i in result.matched:
            continue
        where = f"Wall center line {seg[0]}-{seg[1]}"
        found = candidates_of(i)
        if found:
            result.problems.append(f"{where}: more than one match is possible ({', '.join(found)})")
            continue
        for w, prev in prev_walls.items():
            if w in taken or not _parallel(seg, prev):
                continue
            moved = _line_move(seg, prev)
            if moved > max_move and same_touching(i, w):
                result.problems.append(
                    f"{where} may be {w}, moved {moved:.0f} mm, over the limit of {max_move:.0f} mm "
                    "(candidate for removed + added)"
                )
            elif moved <= max_move:
                result.problems.append(f"{where} may be {w}, but its connections changed")
    return result


def on_segment(a: Point, b: Point, p: Point, tol: float) -> bool:
    """True if p lies on the segment a-b, within the tolerance."""
    length = math.dist(a, b)
    if length == 0:
        return math.dist(a, p) <= tol
    t = position_along(a, b, p)
    if t * length < -tol or (1 - t) * length < -tol:
        return False
    dx, dy = b[0] - a[0], b[1] - a[1]
    return abs(dy * (p[0] - a[0]) - dx * (p[1] - a[1])) / length <= tol


def position_along(a: Point, b: Point, p: Point) -> float:
    """Where p falls along a-b: 0 at a, 1 at b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)


def _touching(s: Segment, t: Segment, tol: float) -> bool:
    return (on_segment(*t, s[0], tol) or on_segment(*t, s[1], tol)
            or on_segment(*s, t[0], tol) or on_segment(*s, t[1], tol))


def _parallel(s: Segment, t: Segment) -> bool:
    ax, ay = s[1][0] - s[0][0], s[1][1] - s[0][1]
    bx, by = t[1][0] - t[0][0], t[1][1] - t[0][1]
    return abs(ax * by - ay * bx) <= 1e-6 * math.hypot(ax, ay) * math.hypot(bx, by)


def _line_move(s: Segment, t: Segment) -> float:
    """How far a line moved: the larger move of its two ends."""
    return max(math.dist(s[0], t[0]), math.dist(s[1], t[1]))


def _direction(start: Point, end: Point, tol: float) -> tuple[int, int]:
    """The direction from start to end as signs of (dx, dy)."""
    def sign(d: float) -> int:
        return 0 if abs(d) <= tol else (1 if d > 0 else -1)

    return sign(end[0] - start[0]), sign(end[1] - start[1])
