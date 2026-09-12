"""凸多边形直径与最远点对的增量检测。

实现内容
--------
1. 凸多边形初始直径：旋转卡壳，时间复杂度 O(m)。
2. 新增观测扇形后：先检查旧直径端点是否仍在新扇形内。
3. 两端均保留时直接复用旧直径；否则对新多边形重新旋转卡壳。
4. 每一步维护最新最长连线点对和每条边的两个候选支撑顶点。

二维数组约定
------------
- ``DiameterState.pair``：形状 ``(2, 2)``，两行分别为 A、B。
- ``RotatingCalipersResult.edge_candidates``：长度等于多边形边数；每项包含
  两个二维顶点，分别对应当前边的支撑顶点及并列支撑候选。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np

import polygon_update as pu


class DiameterError(ValueError):
    """直径计算输入或数值状态异常。"""


def _roundoff_tolerance(tol: float, *values: float) -> float:
    scale = max(1.0, *(abs(float(value)) for value in values))
    return max(tol, 8.0 * np.finfo(np.float64).eps * scale)


def _coerce_point(point: object, name: str) -> np.ndarray:
    try:
        result = np.asarray(point, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise DiameterError(f"{name} 必须是二维坐标") from exc
    if result.shape != (2,) or not np.all(np.isfinite(result)):
        raise DiameterError(f"{name} 必须是长度为 2 的有限坐标")
    return result.copy()


def _clean_polygon_points(points: Iterable[object], tol: float) -> list[np.ndarray]:
    """清除重复点和多余共线点，并保持逆时针顺序。"""

    raw = [_coerce_point(point, f"points[{index}]") for index, point in enumerate(points)]
    if not raw:
        return []
    reference = raw[0]
    shifted = [point - reference for point in raw]
    scale = max(1.0, max(float(np.linalg.norm(point)) for point in shifted))
    distance_tol = _roundoff_tolerance(tol, scale)

    unique: list[np.ndarray] = []
    for point in raw:
        if not unique or float(np.linalg.norm(point - unique[-1])) > distance_tol:
            unique.append(point.copy())
    while len(unique) > 1 and float(np.linalg.norm(unique[0] - unique[-1])) <= distance_tol:
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
            first_norm = float(np.linalg.norm(first))
            second_norm = float(np.linalg.norm(second))
            if first_norm <= distance_tol or second_norm <= distance_tol:
                changed = True
                continue
            cross_value = pu.cross_2d(first, second)
            collinear = abs(cross_value) <= distance_tol * (first_norm + second_norm)
            forward = float(np.dot(first, second)) >= -(distance_tol ** 2)
            if collinear and forward:
                changed = True
                continue
            kept.append(point.copy())
        unique = kept

    if len(unique) >= 3 and pu._polygon_signed_area(unique) < 0.0:
        unique.reverse()
    return unique


def point_in_observation_wedge(
    point: object,
    wedge: pu.ObservationWedge,
    tol: float = pu.DEFAULT_TOLERANCE,
) -> bool:
    """判断点是否在观测点的 direction±1° 有效扇形内，包含边界。"""

    checked = _coerce_point(point, "point")
    if not isinstance(wedge, pu.ObservationWedge):
        raise TypeError("wedge 必须是 ObservationWedge")
    checked_tol = pu._validate_tolerance(tol)
    first = float(pu.cross_2d(wedge.u_minus, checked - wedge.point))
    second = float(pu.cross_2d(wedge.u_plus, checked - wedge.point))
    tolerance = _roundoff_tolerance(checked_tol, first, second)
    return first >= -tolerance and second <= tolerance


@dataclass(frozen=True, slots=True)
class DiameterState:
    """当前凸多边形的直径、最远端点以及本轮是否重算。"""

    distance: float
    point_a: np.ndarray
    point_b: np.ndarray
    recomputed: bool = False

    def __post_init__(self) -> None:
        a = _coerce_point(self.point_a, "point_a")
        b = _coerce_point(self.point_b, "point_b")
        distance = float(np.linalg.norm(a - b))
        if not math.isfinite(distance):
            raise DiameterError("直径必须为有限数值")
        object.__setattr__(self, "point_a", a)
        object.__setattr__(self, "point_b", b)
        object.__setattr__(self, "distance", distance)
        a.setflags(write=False)
        b.setflags(write=False)

    @property
    def pair(self) -> np.ndarray:
        """按 ``(2, 2)`` 返回最新最长连线的点对。"""

        return np.vstack([self.point_a, self.point_b])

    def clone(self) -> DiameterState:
        return DiameterState(
            self.distance,
            self.point_a.copy(),
            self.point_b.copy(),
            self.recomputed,
        )


@dataclass(frozen=True, slots=True)
class RotatingCalipersResult:
    """旋转卡壳结果。"""

    diameter: DiameterState
    edge_candidates: tuple[tuple[np.ndarray, np.ndarray], ...]


def _edge_area(
    first: np.ndarray,
    second: np.ndarray,
    point: np.ndarray,
) -> float:
    return float(pu.cross_2d(second - first, point - first))


def rotating_calipers(
    polygon: pu.RegionPolygon | Iterable[object],
    tol: float = pu.DEFAULT_TOLERANCE,
) -> RotatingCalipersResult:
    """使用旋转卡壳计算凸多边形直径和最远点对。"""

    checked_tol = pu._validate_tolerance(tol)
    if isinstance(polygon, pu.RegionPolygon):
        if not polygon.is_closed or polygon.infinite_node is not None:
            raise DiameterError("直径只适用于有界闭合多边形")
        source = polygon.finite_vertex_chain()
    else:
        source = polygon
    points = _clean_polygon_points(source, checked_tol)
    count = len(points)
    if count < 2:
        raise DiameterError("直径计算至少需要两个点")
    if count == 2:
        state = DiameterState(
            float(np.linalg.norm(points[0] - points[1])),
            points[0],
            points[1],
            recomputed=True,
        )
        return RotatingCalipersResult(state, ((points[0], points[1]),))

    best_distance_sq = -1.0
    best_a: np.ndarray | None = None
    best_b: np.ndarray | None = None
    edge_candidates: list[tuple[np.ndarray, np.ndarray]] = []
    support = 2 % count

    for index in range(count):
        next_index = (index + 1) % count
        current = _edge_area(points[index], points[next_index], points[support])
        advanced = 0
        while True:
            next_support = (support + 1) % count
            following = _edge_area(
                points[index], points[next_index], points[next_support]
            )
            area_tol = _roundoff_tolerance(checked_tol, current, following)
            if following <= current + area_tol:
                break
            support = next_support
            current = following
            advanced += 1
            if advanced > count + 1:
                raise DiameterError("旋转卡壳支撑点推进出现异常循环")

        next_support = (support + 1) % count
        following = _edge_area(points[index], points[next_index], points[next_support])
        area_tol = _roundoff_tolerance(checked_tol, current, following)
        tie_point = points[next_support] if abs(following - current) <= area_tol else points[support]
        edge_candidates.append((points[support].copy(), tie_point.copy()))

        candidates = (
            (points[index], points[support]),
            (points[next_index], points[support]),
            (points[index], tie_point),
            (points[next_index], tie_point),
        )
        for first, second in candidates:
            delta = first - second
            distance_sq = float(np.dot(delta, delta))
            if distance_sq > best_distance_sq:
                best_distance_sq = distance_sq
                best_a = first.copy()
                best_b = second.copy()

    if best_a is None or best_b is None:
        raise DiameterError("旋转卡壳未找到有效候选点对")
    state = DiameterState(
        math.sqrt(max(0.0, best_distance_sq)),
        best_a,
        best_b,
        recomputed=True,
    )
    return RotatingCalipersResult(state, tuple(edge_candidates))


def compute_polygon_diameter(
    polygon: pu.RegionPolygon | Iterable[object],
    tol: float = pu.DEFAULT_TOLERANCE,
) -> DiameterState:
    """计算凸多边形直径，并返回最远端点。"""

    return rotating_calipers(polygon, tol).diameter


@dataclass(frozen=True, slots=True)
class DiameterUpdateResult:
    """一次裁剪后的直径增量更新结果。"""

    state: DiameterState
    reused: bool


def update_diameter_after_wedge(
    polygon: pu.RegionPolygon,
    wedge: pu.ObservationWedge,
    previous: DiameterState,
    tol: float = pu.DEFAULT_TOLERANCE,
) -> DiameterUpdateResult:
    """按新观测扇形更新直径状态。

    先检查旧端点是否仍在扇形内；两端均满足时直接复用。否则对新多边形旋转
    卡壳重算。新多边形必须已经由同一个 ``wedge`` 更新完成。
    """

    if not isinstance(previous, DiameterState):
        raise TypeError("previous 必须是 DiameterState")
    checked_tol = pu._validate_tolerance(tol)
    if point_in_observation_wedge(previous.point_a, wedge, checked_tol) and point_in_observation_wedge(
        previous.point_b, wedge, checked_tol
    ):
        return DiameterUpdateResult(previous.clone(), reused=True)

    recomputed = compute_polygon_diameter(polygon, checked_tol)
    if recomputed.distance > previous.distance + _roundoff_tolerance(
        checked_tol, recomputed.distance, previous.distance
    ):
        raise DiameterError("裁剪后直径不应增大，检测到数值或逻辑异常")
    return DiameterUpdateResult(recomputed, reused=False)


__all__ = [
    "DiameterError",
    "DiameterState",
    "RotatingCalipersResult",
    "DiameterUpdateResult",
    "point_in_observation_wedge",
    "rotating_calipers",
    "compute_polygon_diameter",
    "update_diameter_after_wedge",
]
