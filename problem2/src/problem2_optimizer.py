"""Problem 2 geometry and finite-element optimizer.

Stage 1:
1. First measurement has bearing error [-e, +e].
2. Possible target region = target disk 1800 m
   intersect maximum detection disk 1500 m intersect bearing wedge.
3. Guaranteed second-measurement region = intersection of disks with radius
   1000 m centered at boundary samples of the possible target region.

Stage 2:
1. Sample candidate second positions on a variable-width finite grid.
2. Sweep possible second bearing directions.
3. For each direction, intersect the possible target region with the new
   bearing wedge and compute the polygon diameter.
4. Keep the maximum diameter dmax for the candidate.
5. Refine around the best coarse grid point with a smaller grid width.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

import diameter as diameter_module
import polygon_update as pu


class Problem2GeometryError(ValueError):
    """Invalid geometry or empty search region."""


class EmptyCandidateRegionError(Problem2GeometryError):
    """The guaranteed detection region is empty; this is a valid outcome."""

    def __init__(self, possible_region: "CurvedRegion") -> None:
        self.possible_region = possible_region
        super().__init__("guaranteed detection region is empty")


@dataclass(frozen=True, slots=True)
class EdgeRecord:
    start: np.ndarray
    end: np.ndarray
    kind: str
    center: np.ndarray | None = None
    radius: float | None = None
    diameter: float | None = None
    clockwise: bool | None = None
    start_angle_rad: float | None = None
    end_angle_rad: float | None = None
    span_rad: float | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "start": self.start.tolist(),
            "end": self.end.tolist(),
            "kind": self.kind,
        }
        if self.center is not None:
            result["center"] = self.center.tolist()
            result["radius"] = self.radius
            result["diameter"] = self.diameter
            result["clockwise"] = self.clockwise
            result["start_angle_deg"] = math.degrees(self.start_angle_rad or 0.0)
            result["end_angle_deg"] = math.degrees(self.end_angle_rad or 0.0)
            result["span_deg"] = math.degrees(self.span_rad or 0.0)
        return result


@dataclass(slots=True, eq=False)
class RegionNode:
    point: np.ndarray
    edge_kind: str = "line"
    arc_center: np.ndarray | None = None
    arc_radius: float | None = None
    arc_diameter: float | None = None
    arc_clockwise: bool | None = None
    arc_start_angle_rad: float | None = None
    arc_end_angle_rad: float | None = None
    arc_span_rad: float | None = None
    prev: RegionNode | None = None
    next: RegionNode | None = None

    def clone(self) -> RegionNode:
        return RegionNode(
            self.point.copy(),
            self.edge_kind,
            None if self.arc_center is None else self.arc_center.copy(),
            self.arc_radius,
            self.arc_diameter,
            self.arc_clockwise,
            self.arc_start_angle_rad,
            self.arc_end_angle_rad,
            self.arc_span_rad,
        )


@dataclass(slots=True)
class RegionLinkedList:
    head: RegionNode | None = None
    tail: RegionNode | None = None
    size: int = 0

    @classmethod
    def from_region(cls, region: CurvedRegion) -> RegionLinkedList:
        records = region.edges()
        if not records:
            return cls()
        nodes = [
            RegionNode(
                record.start.copy(),
                record.kind,
                None if record.center is None else record.center.copy(),
                record.radius,
                record.diameter,
                record.clockwise,
                record.start_angle_rad,
                record.end_angle_rad,
                record.span_rad,
            )
            for record in records
        ]
        for index, node in enumerate(nodes):
            node.prev = nodes[index - 1]
            node.next = nodes[(index + 1) % len(nodes)]
        return cls(nodes[0], nodes[-1], len(nodes))

    def __len__(self) -> int:
        return self.size

    def iter_nodes(self):
        if self.head is None:
            return
        node = self.head
        for _ in range(self.size):
            yield node
            if node.next is None:
                raise Problem2GeometryError("region linked list is broken")
            node = node.next


@dataclass
class CurvedRegion:
    vertices: list[np.ndarray]
    circles: list[tuple[np.ndarray, float]] = field(default_factory=list)
    name: str = ""
    disk_constraints: list[tuple[np.ndarray, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.vertices = _clean_convex_polygon(self.vertices)
        if len(self.vertices) >= 3:
            self.vertices = _ensure_ccw(self.vertices)
        checked: list[tuple[np.ndarray, float]] = []
        for center, radius in self.disk_constraints:
            checked.append((_point(center, "disk constraint center"), _finite(radius, "disk constraint radius")))
        self.disk_constraints = checked

    @property
    def empty(self) -> bool:
        return len(self.vertices) < 3

    def bounds(self) -> tuple[float, float, float, float]:
        if self.empty:
            raise Problem2GeometryError(f"{self.name or 'region'} is empty")
        points = np.vstack(self.vertices)
        return (
            float(np.min(points[:, 0])),
            float(np.max(points[:, 0])),
            float(np.min(points[:, 1])),
            float(np.max(points[:, 1])),
        )

    def contains(self, point: object, tol: float = 1e-8) -> bool:
        if self.empty:
            return False
        candidate = np.asarray(point, dtype=np.float64)
        for index, start in enumerate(self.vertices):
            end = self.vertices[(index + 1) % len(self.vertices)]
            edge = end - start
            if pu.cross_2d(edge, candidate - start) < -tol:
                return False
        for center, radius in self.disk_constraints:
            if float(np.linalg.norm(candidate - center)) > radius + tol:
                return False
        return True

    def edges(self, arc_tol: float = 0.5) -> tuple[EdgeRecord, ...]:
        records: list[EdgeRecord] = []
        for index, start in enumerate(self.vertices):
            end = self.vertices[(index + 1) % len(self.vertices)]
            midpoint = 0.5 * (start + end)
            kind = "line"
            center = None
            radius = None
            diameter = None
            clockwise = None
            start_angle = None
            end_angle = None
            span = None
            for circle_center, circle_radius in self.circles:
                if abs(float(np.linalg.norm(midpoint - circle_center)) - circle_radius) <= arc_tol:
                    kind = "arc"
                    center = circle_center.copy()
                    radius = float(circle_radius)
                    diameter = 2.0 * radius
                    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
                    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
                    ccw_span = (end_angle - start_angle) % (2.0 * math.pi)
                    cw_span = (start_angle - end_angle) % (2.0 * math.pi)
                    if ccw_span <= cw_span:
                        clockwise = False
                        span = ccw_span
                    else:
                        clockwise = True
                        span = cw_span
                    break
            records.append(
                EdgeRecord(
                    start.copy(),
                    end.copy(),
                    kind,
                    center,
                    radius,
                    diameter,
                    clockwise,
                    start_angle,
                    end_angle,
                    span,
                )
            )
        return tuple(records)

    def to_linked_list(self) -> RegionLinkedList:
        return RegionLinkedList.from_region(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "vertices": [point.tolist() for point in self.vertices],
            "edges": [record.to_dict() for record in self.edges()],
        }


@dataclass(frozen=True, slots=True)
class GridEvaluation:
    x: float
    y: float
    value: float
    metric: str = "max"

    @property
    def score(self) -> float:
        return self.value

    @property
    def dmax(self) -> float:
        """Backward-compatible alias; in mean mode this is the angular mean."""

        return self.value


@dataclass(frozen=True, slots=True)
class FEMSearchResult:
    possible_region: CurvedRegion
    candidate_region: CurvedRegion
    coarse: tuple[GridEvaluation, ...]
    refined: tuple[GridEvaluation, ...]
    best_coarse: GridEvaluation
    best: GridEvaluation
    coarse_width: float
    refine_width: float
    angle_step_deg: float
    metric: str = "max"

    def summary(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "best_point": [self.best.x, self.best.y],
            "best_value": self.best.value,
            "coarse_best_point": [self.best_coarse.x, self.best_coarse.y],
            "coarse_best_value": self.best_coarse.value,
            "coarse_width": self.coarse_width,
            "refine_width": self.refine_width,
            "angle_step_deg": self.angle_step_deg,
            "coarse_count": len(self.coarse),
            "refined_count": len(self.refined),
        }


@dataclass(frozen=True, slots=True)
class DualGridEvaluation:
    x: float
    y: float
    max_value: float
    mean_value: float


@dataclass(frozen=True, slots=True)
class DualFEMSearchResult:
    possible_region: CurvedRegion
    candidate_region: CurvedRegion
    coarse: tuple[DualGridEvaluation, ...]
    refined: tuple[DualGridEvaluation, ...]
    best_max_coarse: DualGridEvaluation
    best_mean_coarse: DualGridEvaluation
    best_max: DualGridEvaluation
    best_mean: DualGridEvaluation
    coarse_width: float
    refine_width: float
    angle_step_deg: float

    def summary(self) -> dict[str, object]:
        return {
            "metric": "both",
            "best_max_point": [self.best_max.x, self.best_max.y],
            "best_max_value": self.best_max.max_value,
            "best_mean_point": [self.best_mean.x, self.best_mean.y],
            "best_mean_value": self.best_mean.mean_value,
            "coarse_best_max_point": [self.best_max_coarse.x, self.best_max_coarse.y],
            "coarse_best_mean_point": [self.best_mean_coarse.x, self.best_mean_coarse.y],
            "coarse_width": self.coarse_width,
            "refine_width": self.refine_width,
            "angle_step_deg": self.angle_step_deg,
            "coarse_count": len(self.coarse),
            "refined_count": len(self.refined),
        }


def near_optimal_points(result, tolerance: float):
    """Return all evaluated points within tolerance of the best score.

    No nearest-distance filtering is applied. This is used to show multiple
    acceptable second-measurement positions simultaneously.
    """

    checked = _finite(tolerance, "tolerance")
    if checked < 0.0:
        raise Problem2GeometryError("tolerance must be non-negative")
    if isinstance(result, DualFEMSearchResult):
        max_threshold = result.best_max.max_value + checked
        mean_threshold = result.best_mean.mean_value + checked
        combined = list(result.coarse) + list(result.refined)
        unique: dict[tuple[float, float], DualGridEvaluation] = {}
        for item in combined:
            unique[(item.x, item.y)] = item
        items = tuple(unique.values())
        return {
            "max": tuple(item for item in items if item.max_value <= max_threshold + 1e-9),
            "mean": tuple(item for item in items if item.mean_value <= mean_threshold + 1e-9),
        }
    if isinstance(result, FEMSearchResult):
        threshold = result.best.value + checked
        combined = list(result.coarse) + list(result.refined)
        unique_points: dict[tuple[float, float], GridEvaluation] = {}
        for item in combined:
            unique_points[(item.x, item.y)] = item
        return tuple(item for item in unique_points.values() if item.value <= threshold + 1e-9)
    raise TypeError("result must be FEMSearchResult or DualFEMSearchResult")


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise Problem2GeometryError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise Problem2GeometryError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise Problem2GeometryError(f"{name} must be a finite number")
    return result


def _point(value: object, name: str = "point") -> np.ndarray:
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise Problem2GeometryError(f"{name} must be a 2D point") from exc
    if result.shape != (2,) or not np.all(np.isfinite(result)):
        raise Problem2GeometryError(f"{name} must be a finite 2D point")
    return result.copy()


def _ensure_ccw(points: Sequence[np.ndarray]) -> list[np.ndarray]:
    result = [point.copy() for point in points]
    area = pu._polygon_signed_area(result)
    if area < 0.0:
        result.reverse()
    return result


def _clean_convex_polygon(points: Iterable[object], tol: float = 1e-8) -> list[np.ndarray]:
    raw = [_point(point, f"vertices[{index}]") for index, point in enumerate(points)]
    if not raw:
        return []
    unique: list[np.ndarray] = []
    for point in raw:
        if not unique or float(np.linalg.norm(point - unique[-1])) > tol:
            unique.append(point)
    if len(unique) > 1 and float(np.linalg.norm(unique[0] - unique[-1])) <= tol:
        unique.pop()
    if len(unique) < 3:
        return unique
    changed = True
    while changed and len(unique) >= 3:
        changed = False
        kept: list[np.ndarray] = []
        count = len(unique)
        for index, point in enumerate(unique):
            previous = unique[index - 1]
            following = unique[(index + 1) % count]
            first = point - previous
            second = following - point
            if float(np.linalg.norm(first)) <= tol or float(np.linalg.norm(second)) <= tol:
                changed = True
                continue
            area = abs(pu.cross_2d(first, second))
            if area <= tol * (float(np.linalg.norm(first)) + float(np.linalg.norm(second))):
                changed = True
                continue
            kept.append(point.copy())
        unique = kept
    return unique


def _simplify_convex(points: list[np.ndarray], tolerance: float) -> list[np.ndarray]:
    if len(points) < 4:
        return [point.copy() for point in points]
    result = [point.copy() for point in points]
    changed = True
    while changed and len(result) >= 3:
        changed = False
        kept: list[np.ndarray] = []
        count = len(result)
        for index, point in enumerate(result):
            previous = result[index - 1]
            following = result[(index + 1) % count]
            edge = following - previous
            edge_norm = float(np.linalg.norm(edge))
            if edge_norm <= 1e-12:
                changed = True
                continue
            distance = abs(pu.cross_2d(edge, point - previous)) / edge_norm
            if distance <= tolerance:
                changed = True
            else:
                kept.append(point.copy())
        result = kept
    return result


def disk_polygon(center: object, radius: float, samples: int = 720) -> list[np.ndarray]:
    center_array = _point(center, "disk center")
    checked_radius = _finite(radius, "disk radius")
    checked_samples = int(samples)
    if checked_radius <= 0.0 or checked_samples < 8:
        raise Problem2GeometryError("disk radius must be positive and samples >= 8")
    angles = np.linspace(0.0, 2.0 * math.pi, checked_samples, endpoint=False)
    return [
        center_array + checked_radius * np.array([math.cos(angle), math.sin(angle)])
        for angle in angles
    ]


def _clip_points_halfplane(
    source: Sequence[np.ndarray],
    line_start: np.ndarray,
    line_direction: np.ndarray,
    *,
    keep_left: bool,
    tol: float,
) -> list[np.ndarray]:
    if len(source) < 3:
        return []
    output: list[np.ndarray] = []

    def inside(point: np.ndarray) -> bool:
        value = pu.cross_2d(line_direction, point - line_start)
        return value >= -tol if keep_left else value <= tol

    count = len(source)
    for index, first in enumerate(source):
        second = source[(index + 1) % count]
        first_inside = inside(first)
        second_inside = inside(second)
        if first_inside and second_inside:
            output.append(second.copy())
        elif first_inside and not second_inside:
            output.append(_line_intersection(line_start, line_direction, first, second))
        elif not first_inside and second_inside:
            output.append(_line_intersection(line_start, line_direction, first, second))
            output.append(second.copy())
    return output


def outer_polygon(center: object, radius: float, samples: int = 720) -> list[np.ndarray]:
    """Circumscribed regular polygon: disk is contained in the polygon.

    Each polygon edge is tangent to the circle. The returned vertices are the
    intersections of adjacent tangent lines, not points sampled on the circle.
    """

    center_array = _point(center, "outer polygon center")
    checked_radius = _finite(radius, "outer polygon radius")
    checked_samples = int(samples)
    if checked_radius <= 0.0 or checked_samples < 3:
        raise Problem2GeometryError("outer polygon radius must be positive and samples >= 3")
    angles = np.linspace(0.0, 2.0 * math.pi, checked_samples, endpoint=False)
    normals = [np.array([math.cos(angle), math.sin(angle)]) for angle in angles]
    vertices: list[np.ndarray] = []
    for index, normal in enumerate(normals):
        following = normals[(index + 1) % len(normals)]
        determinant = float(
            normal[0] * following[1] - normal[1] * following[0]
        )
        if abs(determinant) <= 1e-14:
            continue
        # Solve the two tangent-line equations analytically.
        point = np.array(
            [
                checked_radius * (following[1] - normal[1]) / determinant,
                checked_radius * (normal[0] - following[0]) / determinant,
            ],
            dtype=np.float64,
        )
        vertices.append(center_array + point)
    return vertices


def clip_halfplane(
    region: CurvedRegion | Sequence[np.ndarray],
    start: object,
    direction: object,
    *,
    keep_left: bool = True,
    tol: float = 1e-9,
) -> CurvedRegion:
    source = region.vertices if isinstance(region, CurvedRegion) else list(region)
    clips = region.circles if isinstance(region, CurvedRegion) else []
    constraints = region.disk_constraints if isinstance(region, CurvedRegion) else []
    line_start = _point(start, "line start")
    line_direction = _point(direction, "line direction")
    output = _clip_points_halfplane(
        source,
        line_start,
        line_direction,
        keep_left=keep_left,
        tol=tol,
    )
    name = region.name if isinstance(region, CurvedRegion) else ""
    return CurvedRegion(output, clips, name=name, disk_constraints=constraints)


def _line_intersection(
    line_point: np.ndarray,
    line_direction: np.ndarray,
    first: np.ndarray,
    second: np.ndarray,
) -> np.ndarray:
    segment = second - first
    denominator = pu.cross_2d(line_direction, segment)
    if abs(denominator) <= 1e-14:
        return second.copy()
    ratio = pu.cross_2d(line_direction, line_point - first) / denominator
    return first + ratio * segment


def intersect_convex_regions(
    first: CurvedRegion,
    second: CurvedRegion,
    *,
    tol: float = 1e-8,
) -> CurvedRegion:
    if first.empty or second.empty:
        return CurvedRegion([], first.circles + second.circles, name=first.name or second.name)
    points = [point.copy() for point in first.vertices]
    for index, start in enumerate(second.vertices):
        end = second.vertices[(index + 1) % len(second.vertices)]
        points = _clip_points_halfplane(
            points,
            start,
            end - start,
            keep_left=True,
            tol=tol,
        )
        if len(points) < 3:
            return CurvedRegion(
                [],
                first.circles + second.circles,
                name=first.name,
                disk_constraints=first.disk_constraints + second.disk_constraints,
            )
    return CurvedRegion(
        points,
        first.circles + second.circles,
        name=first.name,
        disk_constraints=first.disk_constraints + second.disk_constraints,
    )


def bearing_wedge_circles(point: object, bearing_deg: float, error_deg: float) -> tuple[np.ndarray, np.ndarray]:
    center = _point(point, "bearing point")
    bearing = math.radians(_finite(bearing_deg, "bearing_deg"))
    error = math.radians(_finite(error_deg, "error_deg"))
    lower = center + np.array([math.cos(bearing - error), math.sin(bearing - error)])
    upper = center + np.array([math.cos(bearing + error), math.sin(bearing + error)])
    return lower, upper


def possible_target_region(
    first_point: object,
    first_bearing_deg: float,
    *,
    target_radius: float = 1800.0,
    maximum_detection_radius: float = 1500.0,
    bearing_error_deg: float = 1.0,
    circle_samples: int = 720,
    simplify_tolerance: float = 0.0,
) -> CurvedRegion:
    """Intersect target disk, maximum detection disk and first bearing wedge."""

    first = _point(first_point, "first_point")
    target_circle = CurvedRegion(
        outer_polygon((0.0, 0.0), target_radius, circle_samples),
        [((np.array([0.0, 0.0])), float(target_radius))],
        name="possible_target_region",
    )
    detection_circle = CurvedRegion(
        outer_polygon(first, maximum_detection_radius, circle_samples),
        [(first.copy(), float(maximum_detection_radius))],
        name="detection_disk",
    )
    region = intersect_convex_regions(target_circle, detection_circle)
    lower, upper = bearing_wedge_circles(first, first_bearing_deg, bearing_error_deg)
    # lower ray: point must be left/on it; upper ray: point must be right/on it.
    region = clip_halfplane(region, first, lower - first, keep_left=True)
    region = clip_halfplane(region, first, upper - first, keep_left=False)
    region.name = "possible_target_region"
    if simplify_tolerance > 0.0:
        region.vertices = _simplify_convex(region.vertices, simplify_tolerance)
    return region


def _sample_boundary_points(vertices: Sequence[np.ndarray], spacing: float) -> list[np.ndarray]:
    checked_spacing = _finite(spacing, "spacing")
    if checked_spacing <= 0.0:
        raise Problem2GeometryError("spacing must be positive")
    result: list[np.ndarray] = []
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        edge = end - start
        length = float(np.linalg.norm(edge))
        count = max(1, int(math.ceil(length / checked_spacing)))
        for step in range(count):
            result.append(start + (step / count) * edge)
    return result


def guaranteed_detection_region(
    possible_region: CurvedRegion,
    *,
    first_point: object,
    guaranteed_radius: float = 1000.0,
    coverage_spacing: float = 50.0,
    safety_margin: float | None = None,
    circle_samples: int = 360,
    maximum_constraints: int | None = None,
    simplify_tolerance: float = 0.0,
) -> CurvedRegion:
    """Intersection of disks with fixed radius ``guaranteed_radius``.

    The default guaranteed radius is 1000 m. ``safety_margin`` compensates for
    polygon sampling of the possible region. The returned polygon uses inscribed
    disk polygons, so it is a conservative subset of the exact guaranteed-signal
    region. No target-circle restriction is applied: the second detection point
    may lie outside the 1800 m area.
    """

    if possible_region.empty:
        return CurvedRegion([], [], name="guaranteed_detection_region")
    _point(first_point, "first_point")
    checked_guaranteed = _finite(guaranteed_radius, "guaranteed_radius")
    checked_spacing = _finite(coverage_spacing, "coverage_spacing")
    if safety_margin is None:
        margin = 2.0 * checked_spacing
    else:
        margin = _finite(safety_margin, "safety_margin")
        if margin < 0.0:
            raise Problem2GeometryError("safety_margin must be non-negative")

    samples = _sample_boundary_points(possible_region.vertices, checked_spacing)
    if maximum_constraints is not None and len(samples) > maximum_constraints:
        # Coarser sampling is not conservative: shrink further by the extra gap.
        indices = np.linspace(0, len(samples) - 1, maximum_constraints, dtype=int)
        selected = [samples[int(index)] for index in indices]
        extra_gap = max(
            float(np.linalg.norm(samples[int(indices[index + 1])] - samples[int(indices[index])]))
            for index in range(len(indices) - 1)
        ) if len(indices) > 1 else checked_spacing
        margin += 2.0 * extra_gap
        samples = selected

    constraints: list[tuple[np.ndarray, float]] = []
    for center in samples:
        required_radius = checked_guaranteed - margin
        if required_radius <= 0.0:
            return CurvedRegion([], [], name="guaranteed_detection_region")
        constraints.append((center.copy(), required_radius))
    if not constraints:
        return CurvedRegion([], [], name="guaranteed_detection_region")

    result = CurvedRegion(
        disk_polygon(constraints[0][0], constraints[0][1], circle_samples),
        [(constraints[0][0].copy(), constraints[0][1])],
        name="guaranteed_detection_region",
        disk_constraints=[constraints[0]],
    )
    circles = [(constraints[0][0].copy(), constraints[0][1])]
    for center, radius in constraints[1:]:
        disk = CurvedRegion(
            disk_polygon(center, radius, circle_samples),
            [(center.copy(), radius)],
            name="guaranteed_detection_region",
            disk_constraints=[(center.copy(), radius)],
        )
        result = intersect_convex_regions(result, disk)
        circles.append((center.copy(), radius))
        if result.empty:
            result.circles = circles
            result.disk_constraints = constraints
            return result
        if simplify_tolerance > 0.0:
            result.vertices = _simplify_convex(result.vertices, simplify_tolerance)
    result.circles = circles
    result.disk_constraints = constraints
    result.name = "guaranteed_detection_region"
    return result


def _sample_region_vertices(vertices: Sequence[np.ndarray], maximum: int) -> list[np.ndarray]:
    count = len(vertices)
    if count <= maximum:
        return [point.copy() for point in vertices]
    indices = np.linspace(0, count - 1, maximum, dtype=int)
    return [vertices[int(index)].copy() for index in indices]


def intersect_region_with_bearing_wedge(
    possible_region: CurvedRegion,
    detection_point: object,
    measured_bearing_deg: float,
    *,
    bearing_error_deg: float = 1.0,
) -> CurvedRegion:
    point = _point(detection_point, "detection_point")
    lower, upper = bearing_wedge_circles(point, measured_bearing_deg, bearing_error_deg)
    result = clip_halfplane(possible_region, point, lower - point, keep_left=True)
    result = clip_halfplane(result, point, upper - point, keep_left=False)
    result.name = "updated_possible_region"
    return result


def region_diameter(region: CurvedRegion) -> float:
    if len(region.vertices) < 2:
        return 0.0
    return float(diameter_module.compute_polygon_diameter(region.vertices).distance)


def diameter_values_at(
    detection_point: object,
    possible_region: CurvedRegion,
    *,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
) -> tuple[float, ...]:
    """Return diameters for all sampled angles with a non-empty intersection."""

    point = _point(detection_point, "detection_point")
    step = _finite(angle_step_deg, "angle_step_deg")
    if step <= 0.0 or step > 180.0:
        raise Problem2GeometryError("angle_step_deg must be in (0, 180]")
    values: list[float] = []
    for angle in np.arange(0.0, 360.0, step):
        clipped = intersect_region_with_bearing_wedge(
            possible_region,
            point,
            float(angle),
            bearing_error_deg=bearing_error_deg,
        )
        if len(clipped.vertices) >= 2:
            value = region_diameter(clipped)
            if value > 0.0:
                values.append(value)
    return tuple(values)


def angle_metric_at(
    detection_point: object,
    possible_region: CurvedRegion,
    *,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
    metric: str = "max",
) -> float:
    values = diameter_values_at(
        detection_point,
        possible_region,
        bearing_error_deg=bearing_error_deg,
        angle_step_deg=angle_step_deg,
    )
    if not values:
        return math.inf
    if metric == "max":
        return max(values)
    if metric == "mean":
        # Uniform angular step: numerical integral average is sum/N.
        return math.fsum(values) / len(values)
    raise Problem2GeometryError("metric must be 'max' or 'mean'")


def worst_case_diameter_at(
    detection_point: object,
    possible_region: CurvedRegion,
    *,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
) -> float:
    return angle_metric_at(
        detection_point,
        possible_region,
        bearing_error_deg=bearing_error_deg,
        angle_step_deg=angle_step_deg,
        metric="max",
    )


def angular_mean_diameter_at(
    detection_point: object,
    possible_region: CurvedRegion,
    *,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
) -> float:
    return angle_metric_at(
        detection_point,
        possible_region,
        bearing_error_deg=bearing_error_deg,
        angle_step_deg=angle_step_deg,
        metric="mean",
    )


def _grid_points(
    bounds: tuple[float, float, float, float],
    width: float,
) -> Iterable[tuple[float, float]]:
    xmin, xmax, ymin, ymax = bounds
    xs = np.arange(math.floor(xmin / width) * width, xmax + 0.5 * width, width)
    ys = np.arange(math.floor(ymin / width) * width, ymax + 0.5 * width, width)
    for y in ys:
        for x in xs:
            yield float(x), float(y)


def evaluate_dual_finite_grid(
    candidate_region: CurvedRegion,
    possible_region: CurvedRegion,
    *,
    width: float,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
) -> tuple[DualGridEvaluation, ...]:
    checked_width = _finite(width, "width")
    if checked_width <= 0.0:
        raise Problem2GeometryError("width must be positive")
    if candidate_region.empty:
        return tuple()
    xmin, xmax, ymin, ymax = candidate_region.bounds()
    results: list[DualGridEvaluation] = []
    for x, y in _grid_points((xmin, xmax, ymin, ymax), checked_width):
        point = np.array([x, y], dtype=np.float64)
        if not candidate_region.contains(point, tol=checked_width * 1e-9):
            continue
        values = diameter_values_at(
            point,
            possible_region,
            bearing_error_deg=bearing_error_deg,
            angle_step_deg=angle_step_deg,
        )
        if values:
            max_value = max(values)
            mean_value = math.fsum(values) / len(values)
        else:
            max_value = math.inf
            mean_value = math.inf
        results.append(DualGridEvaluation(x, y, max_value, mean_value))
    return tuple(results)


def evaluate_finite_grid(
    candidate_region: CurvedRegion,
    possible_region: CurvedRegion,
    *,
    width: float,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
    metric: str = "max",
) -> tuple[GridEvaluation, ...]:
    checked_width = _finite(width, "width")
    if checked_width <= 0.0:
        raise Problem2GeometryError("width must be positive")
    if candidate_region.empty:
        return tuple()
    xmin, xmax, ymin, ymax = candidate_region.bounds()
    results: list[GridEvaluation] = []
    for x, y in _grid_points((xmin, xmax, ymin, ymax), checked_width):
        point = np.array([x, y], dtype=np.float64)
        if not candidate_region.contains(point, tol=checked_width * 1e-9):
            continue
        value = angle_metric_at(
            point,
            possible_region,
            bearing_error_deg=bearing_error_deg,
            angle_step_deg=angle_step_deg,
            metric=metric,
        )
        results.append(GridEvaluation(x, y, value, metric=metric))
    return tuple(results)


def finite_element_search(
    first_point: object,
    first_bearing_deg: float,
    *,
    target_radius: float = 1800.0,
    maximum_detection_radius: float = 1500.0,
    guaranteed_radius: float = 1000.0,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
    coarse_width: float = 100.0,
    refine_width: float = 20.0,
    refine_span: float | None = None,
    circle_samples: int = 360,
    maximum_constraints: int = 96,
    coverage_spacing: float = 50.0,
    safety_margin: float | None = None,
    metric: str = "max",
) -> FEMSearchResult:
    """Run Stage 1 and two finite-element passes for Problem 2."""

    if metric not in {"max", "mean"}:
        raise Problem2GeometryError("metric must be 'max' or 'mean'")

    possible = possible_target_region(
        first_point,
        first_bearing_deg,
        target_radius=target_radius,
        maximum_detection_radius=maximum_detection_radius,
        bearing_error_deg=bearing_error_deg,
        circle_samples=circle_samples,
    )
    if possible.empty:
        raise Problem2GeometryError("possible target region is empty")
    candidate = guaranteed_detection_region(
        possible,
        first_point=first_point,
        guaranteed_radius=guaranteed_radius,
        coverage_spacing=coverage_spacing,
        safety_margin=safety_margin,
        circle_samples=max(72, circle_samples // 4),
        maximum_constraints=maximum_constraints,
    )
    if candidate.empty:
        raise EmptyCandidateRegionError(possible)
    coarse = evaluate_finite_grid(
        candidate,
        possible,
        width=coarse_width,
        bearing_error_deg=bearing_error_deg,
        angle_step_deg=angle_step_deg,
        metric=metric,
    )
    if not coarse:
        raise Problem2GeometryError("coarse finite-element search found no valid points")
    best_coarse = min(coarse, key=lambda item: item.value)

    if refine_span is None:
        refined_span = max(2.5 * coarse_width, 2.0 * refine_width)
    else:
        refined_span = _finite(refine_span, "refine_span")
    xmin = max(candidate.bounds()[0], best_coarse.x - refined_span)
    xmax = min(candidate.bounds()[1], best_coarse.x + refined_span)
    ymin = max(candidate.bounds()[2], best_coarse.y - refined_span)
    ymax = min(candidate.bounds()[3], best_coarse.y + refined_span)
    refined: list[GridEvaluation] = []
    for x, y in _grid_points((xmin, xmax, ymin, ymax), refine_width):
        point = np.array([x, y], dtype=np.float64)
        if not candidate.contains(point, tol=refine_width * 1e-9):
            continue
        value = angle_metric_at(
            point,
            possible,
            bearing_error_deg=bearing_error_deg,
            angle_step_deg=angle_step_deg,
            metric=metric,
        )
        refined.append(GridEvaluation(x, y, value, metric=metric))
    if not refined:
        refined = [best_coarse]
    best = min(refined, key=lambda item: item.value)
    return FEMSearchResult(
        possible,
        candidate,
        coarse,
        tuple(refined),
        best_coarse,
        best,
        float(coarse_width),
        float(refine_width),
        float(angle_step_deg),
        metric,
    )


def finite_element_search_dual(
    first_point: object,
    first_bearing_deg: float,
    *,
    target_radius: float = 1800.0,
    maximum_detection_radius: float = 1500.0,
    guaranteed_radius: float = 1000.0,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
    coarse_width: float = 100.0,
    refine_width: float = 20.0,
    refine_span: float | None = None,
    circle_samples: int = 360,
    maximum_constraints: int = 96,
    coverage_spacing: float = 50.0,
    safety_margin: float | None = None,
) -> DualFEMSearchResult:
    """Run both metrics while evaluating each grid point only once."""

    possible = possible_target_region(
        first_point,
        first_bearing_deg,
        target_radius=target_radius,
        maximum_detection_radius=maximum_detection_radius,
        bearing_error_deg=bearing_error_deg,
        circle_samples=circle_samples,
    )
    if possible.empty:
        raise Problem2GeometryError("possible target region is empty")
    candidate = guaranteed_detection_region(
        possible,
        first_point=first_point,
        guaranteed_radius=guaranteed_radius,
        coverage_spacing=coverage_spacing,
        safety_margin=safety_margin,
        circle_samples=max(72, circle_samples // 4),
        maximum_constraints=maximum_constraints,
    )
    if candidate.empty:
        raise EmptyCandidateRegionError(possible)

    coarse = evaluate_dual_finite_grid(
        candidate,
        possible,
        width=coarse_width,
        bearing_error_deg=bearing_error_deg,
        angle_step_deg=angle_step_deg,
    )
    if not coarse:
        raise Problem2GeometryError("coarse finite-element search found no valid points")
    best_max_coarse = min(coarse, key=lambda item: item.max_value)
    best_mean_coarse = min(coarse, key=lambda item: item.mean_value)

    if refine_span is None:
        refined_span = max(2.5 * coarse_width, 2.0 * refine_width)
    else:
        refined_span = _finite(refine_span, "refine_span")

    xmin = max(candidate.bounds()[0], min(best_max_coarse.x, best_mean_coarse.x) - refined_span)
    xmax = min(candidate.bounds()[1], max(best_max_coarse.x, best_mean_coarse.x) + refined_span)
    ymin = max(candidate.bounds()[2], min(best_max_coarse.y, best_mean_coarse.y) - refined_span)
    ymax = min(candidate.bounds()[3], max(best_max_coarse.y, best_mean_coarse.y) + refined_span)

    refined_points: dict[tuple[float, float], DualGridEvaluation] = {}
    for x, y in _grid_points((xmin, xmax, ymin, ymax), refine_width):
        point = np.array([x, y], dtype=np.float64)
        if not candidate.contains(point, tol=refine_width * 1e-9):
            continue
        values = diameter_values_at(
            point,
            possible,
            bearing_error_deg=bearing_error_deg,
            angle_step_deg=angle_step_deg,
        )
        if values:
            max_value = max(values)
            mean_value = math.fsum(values) / len(values)
        else:
            max_value = math.inf
            mean_value = math.inf
        refined_points[(x, y)] = DualGridEvaluation(x, y, max_value, mean_value)
    refined = tuple(refined_points.values())
    if not refined:
        refined = (best_max_coarse, best_mean_coarse)

    best_max = min(refined, key=lambda item: item.max_value)
    best_mean = min(refined, key=lambda item: item.mean_value)
    return DualFEMSearchResult(
        possible,
        candidate,
        coarse,
        refined,
        best_max_coarse,
        best_mean_coarse,
        best_max,
        best_mean,
        float(coarse_width),
        float(refine_width),
        float(angle_step_deg),
    )


__all__ = [
    "Problem2GeometryError",
    "EmptyCandidateRegionError",
    "EdgeRecord",
    "RegionNode",
    "RegionLinkedList",
    "CurvedRegion",
    "GridEvaluation",
    "FEMSearchResult",
    "DualGridEvaluation",
    "DualFEMSearchResult",
    "disk_polygon",
    "clip_halfplane",
    "intersect_convex_regions",
    "possible_target_region",
    "guaranteed_detection_region",
    "intersect_region_with_bearing_wedge",
    "region_diameter",
    "diameter_values_at",
    "angle_metric_at",
    "worst_case_diameter_at",
    "angular_mean_diameter_at",
    "evaluate_finite_grid",
    "evaluate_dual_finite_grid",
    "finite_element_search",
    "finite_element_search_dual",
    "near_optimal_points",
]
