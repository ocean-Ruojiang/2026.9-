"""Conservative adaptive spatial cells for the directional-source model.

Cell rejection uses enclosing distance/angle bounds.  A failed centre-point
test NEVER discards a cell.  The cell budget changes precision, not safety.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
import numpy as np

from . import angles


@dataclass
class GridResult:
    cells: np.ndarray
    polygons: list[np.ndarray]
    areas: np.ndarray
    omni_possible: np.ndarray
    directional_possible: np.ndarray
    diagnostics: dict


def polygon_area(poly: np.ndarray) -> float:
    if len(poly) < 3:
        return 0.0
    return float(abs(np.dot(poly[:, 0], np.roll(poly[:, 1], -1)) -
                     np.dot(poly[:, 1], np.roll(poly[:, 0], -1))) * 0.5)


def clip_box(poly: np.ndarray, box) -> np.ndarray:
    """Closed clipping, retaining boundary vertices and degenerate segments."""
    out = np.asarray(poly, dtype=float)
    for axis, bound, sign in ((0, box[0], 1), (0, box[2], -1),
                              (1, box[1], 1), (1, box[3], -1)):
        if not len(out):
            return np.empty((0, 2))
        nxt = []
        prev = out[-1]
        prev_dist = sign * (prev[axis] - bound)
        for cur in out:
            dist = sign * (cur[axis] - bound)
            prev_in, cur_in = prev_dist >= 0.0, dist >= 0.0
            if prev_in != cur_in:
                nxt.append(prev + (cur - prev) * (prev_dist / (prev_dist - dist)))
            if cur_in:
                nxt.append(cur)
            prev, prev_dist = cur, dist
        out = np.asarray(nxt, dtype=float).reshape((-1, 2))
    return out


def distance_bounds(box, point) -> tuple[float, float]:
    p = np.asarray(point, dtype=float)
    low, high = np.asarray(box[:2]), np.asarray(box[2:])
    close = np.maximum(np.maximum(low - p, p - high), 0.0)
    far = np.maximum(np.abs(low - p), np.abs(high - p))
    return float(np.linalg.norm(close)), float(np.linalg.norm(far))


def _relaxed_arc(point, center, delta):
    vector = np.asarray(point) - center
    distance = float(np.linalg.norm(vector))
    if distance <= delta:
        return angles.full_circle()
    return angles.arc(math.atan2(vector[1], vector[0]),
                      math.acos(max(-1.0, -delta / distance)))


def cell_possible(box, positives, negatives, failures, cfg):
    """Return conservative possible type flags plus forced-negative count."""
    eps = cfg.geometry_eps
    if distance_bounds(box, (0.0, 0.0))[0] > cfg.domain_radius + eps:
        return False, False, 0
    if any(distance_bounds(box, p)[1] < cfg.clear_radius - eps for p in failures):
        return False, False, 0
    lower = cfg.recv_min
    for obs in positives:
        lo, _ = distance_bounds(box, obs.point)
        maximum = cfg.near_radius if obs.result == 'near' else cfg.recv_max
        if lo > maximum + eps:
            return False, False, 0
        lower = max(lower, lo)
    if lower > cfg.recv_max + eps:
        return False, False, 0
    forced = [n for n in negatives
              if distance_bounds(box, n.point)[1] < lower - eps]
    omni = not forced
    center = (np.asarray(box[:2]) + np.asarray(box[2:])) * 0.5
    delta = float(np.linalg.norm(np.asarray(box[2:]) - np.asarray(box[:2])) * 0.5)
    allowed = angles.full_circle()
    for obs in positives:
        allowed = angles.intersect(allowed, _relaxed_arc(obs.point, center, delta))
        if not allowed:
            return omni, False, len(forced)
    for obs in forced:
        # The strict backside constraint is relaxed to a closed arc.  This
        # deliberately keeps uncertain boundary cells for later refinement.
        opposite = 2.0 * center - np.asarray(obs.point)
        allowed = angles.intersect(allowed, _relaxed_arc(opposite, center, delta))
        if not allowed:
            return omni, False, len(forced)
    return omni, True, len(forced)


def build_grid(polygon, history, failures, cfg) -> GridResult:
    polygon = np.asarray(polygon, dtype=float).reshape((-1, 2))
    empty = GridResult(np.empty((0, 4)), [], np.empty(0),
                       np.empty(0, dtype=bool), np.empty(0, dtype=bool), {})
    if not len(polygon):
        empty.diagnostics = {'empty_input_polygon': True}
        return empty
    positives = [o for o in history if o.result in ('direction', 'near')]
    negatives = [o for o in history if o.result == 'no_signal']
    budget = max(16, int(cfg.grid_max_cells))
    minimum = max(0.01, float(cfg.grid_min_m))
    span = np.max(polygon, axis=0) - np.min(polygon, axis=0)
    step = max(minimum, float(cfg.grid_initial_m),
               math.sqrt(float(np.prod(span)) / max(1, budget)))
    low = np.floor(np.min(polygon, axis=0) / step) * step
    high = np.max(polygon, axis=0)
    heap = []
    serial = 0
    rejected = checked = forced_count = 0

    def add(box, parent_polygon):
        nonlocal serial, checked, rejected, forced_count
        poly = clip_box(parent_polygon, box)
        if not len(poly):
            return
        checked += 1
        omni, directional, forced = cell_possible(box, positives, negatives, failures, cfg)
        forced_count += forced
        if not omni and not directional:
            rejected += 1
            return
        serial += 1
        edge = max(box[2] - box[0], box[3] - box[1])
        heapq.heappush(heap, (-edge, serial, tuple(box), poly, omni, directional))

    for x in np.arange(low[0], high[0] + step * 1e-9, step):
        for y in np.arange(low[1], high[1] + step * 1e-9, step):
            add((x, y, x + step, y + step), polygon)
    leaves = []
    while heap:
        item = heapq.heappop(heap)
        edge, _, box, poly, omni, directional = item
        if -edge <= minimum * (1.0 + 1e-10) or len(heap) + len(leaves) + 4 > budget:
            leaves.append(item)
            continue
        mid_x, mid_y = (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5
        for child in ((box[0], box[1], mid_x, mid_y),
                      (mid_x, box[1], box[2], mid_y),
                      (box[0], mid_y, mid_x, box[3]),
                      (mid_x, mid_y, box[2], box[3])):
            add(child, poly)
    cells = np.array([v[2] for v in leaves], dtype=float).reshape((-1, 4))
    polys = [v[3] for v in leaves]
    return GridResult(cells, polys, np.array([polygon_area(p) for p in polys]),
                      np.array([v[4] for v in leaves], dtype=bool),
                      np.array([v[5] for v in leaves], dtype=bool),
                      {'cells_checked': checked, 'cells_rejected': rejected,
                       'forced_negative_checks': forced_count,
                       'cell_budget': budget,
                       'budget_limited': any(-v[0] > minimum * (1 + 1e-10) for v in leaves)})
