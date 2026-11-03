"""Build the room network (nodes, edges, closed contours) from boundary lines.

CAD lines do not share endpoints, so endpoints within a tolerance are merged
into nodes. A line whose interior touches another line's endpoint (T-junction)
is split there. Edge direction is fixed by the DB (start = smaller (x, y)),
not by the direction the line was drawn in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from building_db.dxf_reader import Line

# Provisional tolerance for merging endpoints, in mm (SPEC.md 3: Unknown).
DEFAULT_TOLERANCE_MM = 1.0


@dataclass(frozen=True)
class Edge:
    start: int  # node index
    end: int  # node index


@dataclass(frozen=True)
class Face:
    """A closed contour, counter-clockwise."""

    nodes: list[int]  # node indices in order, not repeating the first
    edges: list[int]  # edge indices in the same order
    forward: list[bool]  # True if the contour runs start -> end of the edge
    area: float  # in mm2, positive


@dataclass
class Network:
    nodes: list[tuple[float, float]]
    edges: list[Edge]
    faces: list[Face]
    problems: list[str] = field(default_factory=list)

    def edge_length(self, i: int) -> float:
        e = self.edges[i]
        return math.dist(self.nodes[e.start], self.nodes[e.end])


def build_network(lines: Iterable[Line], tol: float = DEFAULT_TOLERANCE_MM) -> Network:
    segments = [((ln.x1, ln.y1), (ln.x2, ln.y2)) for ln in lines]
    problems: list[str] = []

    nodes: list[tuple[float, float]] = []

    def node_at(p: tuple[float, float]) -> int:
        for i, q in enumerate(nodes):
            if math.dist(p, q) <= tol:
                return i
        nodes.append(p)
        return len(nodes) - 1

    pairs: list[tuple[int, int]] = []
    for a, b in segments:
        i, j = node_at(a), node_at(b)
        if i == j:
            problems.append(f"Zero-length boundary line at {a}")
            continue
        pairs.append((i, j))

    pairs = _split_at_t_junctions(pairs, nodes, tol)

    edges: list[Edge] = []
    seen: set[tuple[int, int]] = set()
    for i, j in pairs:
        start, end = (i, j) if nodes[i] <= nodes[j] else (j, i)
        if (start, end) in seen:
            problems.append(f"Duplicate boundary line {nodes[start]}-{nodes[end]}")
            continue
        seen.add((start, end))
        edges.append(Edge(start, end))

    degree = [0] * len(nodes)
    for e in edges:
        degree[e.start] += 1
        degree[e.end] += 1
    for i, d in enumerate(degree):
        if d == 1:
            problems.append(f"Open contour: node {nodes[i]} has only one boundary line")

    faces = _find_faces(nodes, edges)
    return Network(nodes=nodes, edges=edges, faces=faces, problems=problems)


def _split_at_t_junctions(pairs, nodes, tol):
    """Split a segment wherever another node lies on its interior."""
    result = []
    for i, j in pairs:
        a, b = nodes[i], nodes[j]
        inner = [
            (k, _param(a, b, p))
            for k, p in enumerate(nodes)
            if k not in (i, j) and _on_segment_interior(a, b, p, tol)
        ]
        chain = [i] + [k for k, _ in sorted(inner, key=lambda kt: kt[1])] + [j]
        result.extend(zip(chain, chain[1:]))
    return result


def _param(a, b, p) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)


def _on_segment_interior(a, b, p, tol) -> bool:
    length = math.dist(a, b)
    if length == 0:
        return False
    t = _param(a, b, p)
    if t * length <= tol or (1 - t) * length <= tol:
        return False
    dx, dy = b[0] - a[0], b[1] - a[1]
    distance = abs(dy * (p[0] - a[0]) - dx * (p[1] - a[1])) / length
    return distance <= tol


def _find_faces(nodes, edges) -> list[Face]:
    """Trace the bounded faces of the planar graph (counter-clockwise)."""
    outgoing: dict[int, list[tuple[float, int, int, bool]]] = {i: [] for i in range(len(nodes))}
    for idx, e in enumerate(edges):
        for u, v, fwd in ((e.start, e.end, True), (e.end, e.start, False)):
            angle = math.atan2(nodes[v][1] - nodes[u][1], nodes[v][0] - nodes[u][0])
            outgoing[u].append((angle, v, idx, fwd))
    for lst in outgoing.values():
        lst.sort()

    visited: set[tuple[int, int]] = set()
    faces: list[Face] = []
    for u in outgoing:
        for _, v, idx, fwd in outgoing[u]:
            if (u, v) in visited:
                continue
            face_nodes, face_edges, face_fwd = [], [], []
            a, b, ei, ef = u, v, idx, fwd
            while (a, b) not in visited:
                visited.add((a, b))
                face_nodes.append(a)
                face_edges.append(ei)
                face_fwd.append(ef)
                # At b, take the edge just clockwise of the way back (b -> a):
                # this keeps the face on the left, so bounded faces run CCW.
                lst = outgoing[b]
                k = next(n for n, item in enumerate(lst) if item[1] == a and item[2] == ei)
                _, nxt, ni, nf = lst[(k - 1) % len(lst)]
                a, b, ei, ef = b, nxt, ni, nf
            area = _signed_area([nodes[n] for n in face_nodes])
            if area > 0:
                faces.append(Face(face_nodes, face_edges, face_fwd, area))
    return faces


def _signed_area(points) -> float:
    s = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        s += x1 * y2 - x2 * y1
    return s / 2


def point_in_polygon(p: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """Ray casting test. Points exactly on the outline count as outside."""
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xc > x:
                inside = not inside
    return inside
