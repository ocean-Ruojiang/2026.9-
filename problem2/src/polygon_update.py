"""有效区域多边形更新算法：观测范围、归纳迭代与双向链表。

术语约定
--------
1. 本模块中的“多边形”均指“有效区域多边形”，即各观测点 2° 无限扇形观测
   范围的交集。
2. “有效区域”“有效扇形交集”和“区域多边形”在本模块中表示同一对象。
3. 题目输入的目标多边形 ``problem_variables.goal_position`` 是另一对象，
   仅描述目标轮廓；本模块不读取、不修改它，也不将其与有效区域混用。

单位与边界约定
--------------
1. :mod:`problem_variables` 中的 ``direction`` 按“度”解释；本模块生成的
   ``real_direction_range`` 同样按“度”解释。
2. 对第 ``i`` 个观测方向 ``direction[i]``，其真实观测范围严格表示为
   ``[direction[i] - 1.0, direction[i] + 1.0]``。
3. 本阶段不将角度归一化到 ``[0, 360)``。例如范围可以包含负数或超过 360，
   以保留跨越 0 度边界时的原始区间含义。
4. ``real_direction_range`` 中元素编号和顺序与 ``position``、``direction``
   一致，每个元素均为形状 ``(2,)`` 的 ``float64`` NumPy 数组。

归纳迭代说明
----------------
本模块先构造方向范围，再逐点求扇形交集的精确边界，并把每轮有效区域输出为
真顶点与无限节点统一组织的双向链表。
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable
from numbers import Integral, Real
from typing import Final

import numpy as np
from scipy.optimize import linprog

import problem_variables

__all__ = [
    "DIRECTION_HALF_WIDTH_DEG",
    "real_direction_range",
    "build_real_direction_range",
    "validate_real_direction_range",
]

DIRECTION_HALF_WIDTH_DEG: Final[float] = 1.0
"""单个方向左右两侧的角度半宽，单位为度。"""

real_direction_range: list[np.ndarray] = []
"""按观测点顺序保存的观测方向范围；每个数组为 ``[下界, 上界]``。

这是构造有效区域多边形（有效扇形交集）的基态约束，不描述目标多边形
``problem_variables.goal_position``。
"""


def _validate_expected_length(value: object, *, name: str) -> int:
    """校验长度参数是否为非负整数。"""

    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} 必须是非负整数")
    result = int(value)
    if result < 0:
        raise ValueError(f"{name} 必须是非负整数")
    return result


def _coerce_direction_values(direction: object) -> list[float]:
    """将显式输入转换为有限浮点列表，不改变原始顺序。"""

    if isinstance(direction, (str, bytes)) or not isinstance(direction, Iterable):
        raise TypeError("direction 必须是有限实数组成的可迭代对象")

    result: list[float] = []
    for index, item in enumerate(direction):
        if isinstance(item, bool) or not isinstance(item, Real):
            raise TypeError(f"direction[{index}] 必须是实数")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"direction[{index}] 必须是有限数值")
        result.append(number)

    return result


def _copy_real_direction_range(
    ranges: list[np.ndarray],
) -> list[np.ndarray]:
    """复制方向范围列表，避免调用方修改模块内部状态。"""

    return [item.copy() for item in ranges]


def build_real_direction_range(
    direction: Iterable[Real] | None = None,
    *,
    expected_length: int | None = None,
) -> list[np.ndarray]:
    """构造有效区域多边形的初始观测方向范围。

    这里生成的是各观测点 2° 有效扇形约束的基态，不是题目给定的目标多边形
    ``problem_variables.goal_position``。

    Parameters
    ----------
    direction:
        显式传入的有限实数方向序列。为 ``None`` 时读取当前
        :data:`problem_variables.direction`，编号和顺序保持不变。
    expected_length:
        期望的输入/输出长度。为 ``None`` 时，默认读取当前问题的
        ``num_measure``；显式传入 ``direction`` 时默认使用其长度。

    Returns
    -------
    list[numpy.ndarray]
        独立副本列表；第 ``i`` 项为 ``[direction_i - 1.0,
        direction_i + 1.0]``，单位为度。模块级
        :data:`real_direction_range` 同步更新为相同内容。

    Notes
    -----
    本函数不对角度执行 0--360 度归一化，因此负角度和大于 360 度的范围均会
    原样保留。
    """

    if direction is None:
        source_direction = list(problem_variables.direction)
        if expected_length is None:
            expected_length = problem_variables.num_measure
    else:
        source_direction = _coerce_direction_values(direction)

    if expected_length is None:
        checked_length = len(source_direction)
    else:
        checked_length = _validate_expected_length(
            expected_length,
            name="expected_length",
        )
        if len(source_direction) != checked_length:
            raise ValueError(
                "direction 长度与 expected_length 不一致："
                f"期望 {checked_length}，实际 {len(source_direction)}"
            )

    # 先完整构造并校验结果，再一次性更新全局变量，避免失败时留下半成品。
    ranges: list[np.ndarray] = []
    for index, item in enumerate(source_direction):
        if isinstance(item, bool) or not isinstance(item, Real):
            raise TypeError(f"direction[{index}] 必须是实数")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"direction[{index}] 必须是有限数值")

        lower = number - DIRECTION_HALF_WIDTH_DEG
        upper = number + DIRECTION_HALF_WIDTH_DEG
        if not math.isfinite(lower) or not math.isfinite(upper):
            raise ValueError(
                f"direction[{index}] 加减 {DIRECTION_HALF_WIDTH_DEG} 度后必须是有限数值"
            )
        ranges.append(
            np.array([lower, upper], dtype=np.float64)
        )

    validate_real_direction_range(ranges, expected_length=checked_length)

    # 该基态供后续 iterative_select_until_bounded 按观测点顺序构造有效区域。

    globals()["real_direction_range"] = _copy_real_direction_range(ranges)
    return _copy_real_direction_range(ranges)


def validate_real_direction_range(
    value: object | None = None,
    *,
    expected_length: int | None = None,
) -> bool:
    """严格校验观测方向范围列表。

    Parameters
    ----------
    value:
        待校验对象；为 ``None`` 时校验模块级全局变量。
    expected_length:
        可选的非负期望长度。给出时，输入长度必须与其完全一致。

    Returns
    -------
    bool
        校验通过返回 ``True``；否则抛出异常。

    Notes
    -----
    每个元素必须是形状 ``(2,)`` 的有限浮点 NumPy 数组，且下界不大于上界。
    校验不执行 0--360 度归一化。
    """

    checked = globals()["real_direction_range"] if value is None else value
    if not isinstance(checked, list):
        raise TypeError("real_direction_range 必须是 list[np.ndarray]")

    if expected_length is not None:
        checked_length = _validate_expected_length(
            expected_length,
            name="expected_length",
        )
        if len(checked) != checked_length:
            raise ValueError(
                "real_direction_range 长度与 expected_length 不一致："
                f"期望 {checked_length}，实际 {len(checked)}"
            )

    for index, item in enumerate(checked):
        if not isinstance(item, np.ndarray):
            raise TypeError(
                f"real_direction_range[{index}] 必须是 np.ndarray"
            )
        if item.shape != (2,):
            raise ValueError(
                f"real_direction_range[{index}] 的形状必须是 (2,)，实际为 {item.shape}"
            )
        if not np.issubdtype(item.dtype, np.floating):
            raise TypeError(
                f"real_direction_range[{index}] 必须是浮点数组"
            )
        if not bool(np.all(np.isfinite(item))):
            raise ValueError(
                f"real_direction_range[{index}] 的两个边界必须是有限数值"
            )
        if item[0] > item[1]:
            raise ValueError(
                f"real_direction_range[{index}] 下界不能大于上界"
            )

    return True

# ---------------------------------------------------------------------------
# 归纳奠基：观测扇形约束与齐次方向锥有界性判定
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any

DEFAULT_TOLERANCE: Final[float] = 1e-12
"""叉积约束判定使用的默认绝对数值容差。"""


def _coerce_finite_real(value: object, *, name: str) -> float:
    """将对象严格转换为有限实数。"""

    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} 必须是有限实数")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限实数")
    return result


def _coerce_finite_vector2(value: object, *, name: str) -> np.ndarray:
    """将对象转换为形状 (2,) 的有限 float64 向量。"""

    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} 必须是长度为 2 的有限实数向量")
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"{name} 必须是长度为 2 的有限实数向量"
        ) from exc
    if result.shape != (2,):
        raise ValueError(f"{name} 的形状必须是 (2,)，实际为 {result.shape}")
    if not bool(np.all(np.isfinite(result))):
        raise ValueError(f"{name} 的两个分量必须是有限数值")
    return result.copy()


def _validate_tolerance(tol: object) -> float:
    """校验叉积比较容差。"""

    if isinstance(tol, bool) or not isinstance(tol, Real):
        raise TypeError("tol 必须是非负有限实数")
    result = float(tol)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError("tol 必须是非负有限实数")
    return result


def cross_2d(a: object, b: object) -> float:
    """返回二维向量 ``a`` 与 ``b`` 的叉积。

    采用标准数学坐标及右手约定：
    ``cross(a, b) = a_x * b_y - a_y * b_x``。
    """

    vector_a = _coerce_finite_vector2(a, name="a")
    vector_b = _coerce_finite_vector2(b, name="b")
    return float(vector_a[0] * vector_b[1] - vector_a[1] * vector_b[0])


@dataclass(frozen=True, slots=True)
class ObservationWedge:
    """一个观测点的无限扇形约束，也是有效区域多边形的一类边界约束。

    单个对象只表示一个观测点的 2° 有效扇形；多个对象的方向交集才是“有效区域
    多边形”。该对象与目标多边形 ``problem_variables.goal_position`` 无关。

    Parameters
    ----------
    index:
        观测点编号。
    point:
        观测点二维坐标。
    direction_deg:
        原始中心方向，单位为度，角度从 +x 轴逆时针。
    lower_angle_deg, upper_angle_deg:
        扇形约束的较低、较高角度端点，单位为度。区间按原始实数角度保存，
        不归一化；例如 ``[359, 361]`` 是合法连续区间。
    u_minus, u_plus:
        分别对应较低、较高角端点的单位方向射线。
    """

    index: int
    point: np.ndarray
    direction_deg: float
    lower_angle_deg: float
    upper_angle_deg: float
    u_minus: np.ndarray
    u_plus: np.ndarray

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, Integral):
            raise TypeError("index 必须是非负整数")
        checked_index = int(self.index)
        if checked_index < 0:
            raise ValueError("index 必须是非负整数")
        object.__setattr__(self, "index", checked_index)

        point = _coerce_finite_vector2(self.point, name="point")
        direction = _coerce_finite_real(
            self.direction_deg, name="direction_deg"
        )
        lower = _coerce_finite_real(
            self.lower_angle_deg, name="lower_angle_deg"
        )
        upper = _coerce_finite_real(
            self.upper_angle_deg, name="upper_angle_deg"
        )
        if not lower < upper:
            raise ValueError(
                "扇形角度区间必须满足 lower_angle_deg < upper_angle_deg；"
                "跨越 0 度时应显式增加 360 度，不能使用上界小于下界的非连续区间"
            )
        span = upper - lower
        if span >= 360.0:
            raise ValueError("扇形角度跨度必须小于 360 度，不能退化为全平面")
        angle_tol = 1e-12
        if direction < lower - angle_tol or direction > upper + angle_tol:
            raise ValueError("direction_deg 必须位于扇形角度区间内")

        u_minus = _coerce_finite_vector2(self.u_minus, name="u_minus")
        u_plus = _coerce_finite_vector2(self.u_plus, name="u_plus")
        norm_minus = float(np.linalg.norm(u_minus))
        norm_plus = float(np.linalg.norm(u_plus))
        if norm_minus == 0.0 or norm_plus == 0.0:
            raise ValueError("u_minus 与 u_plus 必须是非零方向向量")
        u_minus = u_minus / norm_minus
        u_plus = u_plus / norm_plus

        lower_rad = math.radians(lower)
        upper_rad = math.radians(upper)
        expected_minus = np.array(
            [math.cos(lower_rad), math.sin(lower_rad)], dtype=np.float64
        )
        expected_plus = np.array(
            [math.cos(upper_rad), math.sin(upper_rad)], dtype=np.float64
        )
        if (
            float(np.dot(u_minus, expected_minus)) < 1.0 - 1e-10
            or float(np.dot(u_plus, expected_plus)) < 1.0 - 1e-10
        ):
            raise ValueError("u_minus 与 u_plus 必须分别与较低、较高角度端点一致")

        object.__setattr__(self, "point", point)
        object.__setattr__(self, "direction_deg", direction)
        object.__setattr__(self, "lower_angle_deg", lower)
        object.__setattr__(self, "upper_angle_deg", upper)
        object.__setattr__(self, "u_minus", u_minus)
        object.__setattr__(self, "u_plus", u_plus)
        point.setflags(write=False)
        u_minus.setflags(write=False)
        u_plus.setflags(write=False)

    def clone(self) -> ObservationWedge:
        """Return a deep copy safe for snapshot storage."""
        return ObservationWedge(
            index=self.index,
            point=self.point,
            direction_deg=self.direction_deg,
            lower_angle_deg=self.lower_angle_deg,
            upper_angle_deg=self.upper_angle_deg,
            u_minus=self.u_minus,
            u_plus=self.u_plus,
        )

    @classmethod
    def from_direction(
        cls,
        index: int,
        point: object,
        direction_deg: Real,
        *,
        half_width_deg: Real = DIRECTION_HALF_WIDTH_DEG,
    ) -> ObservationWedge:
        """由中心方向和半角宽度构造观测扇形。"""

        checked_direction = _coerce_finite_real(
            direction_deg, name="direction_deg"
        )
        half_width = _coerce_finite_real(
            half_width_deg, name="half_width_deg"
        )
        if half_width <= 0.0:
            raise ValueError("half_width_deg 必须是正有限实数")
        if 2.0 * half_width >= 360.0:
            raise ValueError("扇形角度跨度必须小于 360 度")

        lower = checked_direction - half_width
        upper = checked_direction + half_width
        lower_rad = math.radians(lower)
        upper_rad = math.radians(upper)
        return cls(
            index=index,
            point=point,
            direction_deg=checked_direction,
            lower_angle_deg=lower,
            upper_angle_deg=upper,
            u_minus=np.array(
                [math.cos(lower_rad), math.sin(lower_rad)],
                dtype=np.float64,
            ),
            u_plus=np.array(
                [math.cos(upper_rad), math.sin(upper_rad)],
                dtype=np.float64,
            ),
        )


def make_observation_wedge(
    index: int,
    point: object,
    direction_deg: Real,
    *,
    half_width_deg: Real = DIRECTION_HALF_WIDTH_DEG,
) -> ObservationWedge:
    """构造一个观测扇形的便捷接口。"""

    return ObservationWedge.from_direction(
        index,
        point,
        direction_deg,
        half_width_deg=half_width_deg,
    )


def _coerce_wedges(wedges: object) -> list[ObservationWedge]:
    """将输入转换为观测扇形列表并校验元素类型。"""

    if isinstance(wedges, (str, bytes)) or not isinstance(wedges, Iterable):
        raise TypeError("wedges 必须是 ObservationWedge 的可迭代对象")
    result: list[ObservationWedge] = []
    for index, wedge in enumerate(wedges):
        if not isinstance(wedge, ObservationWedge):
            raise TypeError(f"wedges[{index}] 必须是 ObservationWedge")
        result.append(wedge)
    return result


def _direction_satisfies_wedges(
    direction: np.ndarray,
    wedges: list[ObservationWedge],
    tol: float,
) -> bool:
    """按题目给定的叉积不等式检验候选方向。"""

    for wedge in wedges:
        if cross_2d(wedge.u_minus, direction) < -tol:
            return False
        if cross_2d(wedge.u_plus, direction) > tol:
            return False
    return True


def find_unbounded_direction(
    wedges: Iterable[ObservationWedge],
    tol: Real = DEFAULT_TOLERANCE,
) -> np.ndarray | None:
    """寻找有效区域多边形可沿其无限延伸的非零方向。

    有效区域多边形是全部观测扇形（2° 射线扇形）的交集。第 ``i`` 个扇形对应
    条件

    ``cross(u_minus_i, d) >= 0`` 且 ``cross(u_plus_i, d) <= 0``。

    二维齐次半平面锥的极方向必属于所有端点射线 ``u`` 与 ``-u`` 的有限集合，
    因此枚举这些候选即可，不使用包围盒近似。找到时返回归一化方向；不存在时
    返回 ``None``。无约束输入（空 ``wedges``）视为整个平面，返回 ``[1, 0]``。
    """

    checked_wedges = _coerce_wedges(wedges)
    checked_tol = _validate_tolerance(tol)
    if not checked_wedges:
        return np.array([1.0, 0.0], dtype=np.float64)

    candidates: list[np.ndarray] = []
    for wedge in checked_wedges:
        for vector in (
            wedge.u_minus,
            wedge.u_plus,
            -wedge.u_minus,
            -wedge.u_plus,
        ):
            norm = float(np.linalg.norm(vector))
            if norm == 0.0 or not math.isfinite(norm):
                continue
            candidate = vector / norm
            if any(
                bool(np.allclose(candidate, existing, rtol=0.0, atol=1e-15))
                for existing in candidates
            ):
                continue
            candidates.append(candidate)

    for candidate in candidates:
        if _direction_satisfies_wedges(
            candidate, checked_wedges, checked_tol
        ):
            return candidate.copy()
    return None


def is_intersection_bounded(
    wedges: Iterable[ObservationWedge],
    tol: Real = DEFAULT_TOLERANCE,
) -> bool:
    """判断有效区域多边形（全部观测扇形交集）是否有界。

    返回 ``True`` 表示有效区域多边形有界，并非判断目标多边形
    ``problem_variables.goal_position``。存在共同非零无穷方向时有效区域无界；
    否则有界。空约束对应全平面，因此视为无界。
    """

    return find_unbounded_direction(wedges, tol) is None


class EffectiveRegionError(ValueError):
    """有效区域几何错误。"""


class EmptyEffectiveRegionError(EffectiveRegionError):
    """有效扇形交集为空。"""


class DegenerateEffectiveRegionError(EffectiveRegionError):
    """有效区域退化，无法唯一构造边界链表。"""


class LinkedListInvariantError(RuntimeError):
    """有效区域双向链表不满足结构性不变量。"""


GEOMETRY_TOLERANCE: Final[float] = 1e-9
"""半平面顶点、去重和排序使用的几何容差。"""


@dataclass(slots=True, eq=False)
class RegionNode:
    """Effective-region node for a finite vertex or one infinite boundary node."""

    point: np.ndarray | None = None
    is_infinite: bool = False
    direction: np.ndarray | None = None
    direction_interval: tuple[float, float] | None = None
    extreme_rays: tuple[np.ndarray, np.ndarray] | None = None
    cone_kind: str | None = None
    prev: RegionNode | None = field(default=None, repr=False, compare=False)
    next: RegionNode | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.is_infinite, bool):
            raise TypeError("is_infinite must be bool")
        if not self.is_infinite:
            if self.point is None:
                raise ValueError("finite vertex must contain a point")
            if self.direction is not None:
                raise ValueError("finite vertex cannot contain infinite direction")
            if self.direction_interval is not None or self.extreme_rays is not None:
                raise ValueError("finite vertex cannot contain infinite-cone metadata")
            if self.cone_kind is not None:
                raise ValueError("finite vertex cannot contain cone_kind")
            point = _coerce_finite_vector2(self.point, name="point")
            point.setflags(write=False)
            object.__setattr__(self, "point", point)
        else:
            if self.point is not None:
                raise ValueError("infinite node cannot contain a finite point")
            if self.direction is None:
                raise ValueError("infinite node must contain a direction")
            direction = _coerce_finite_vector2(self.direction, name="direction")
            norm = float(np.linalg.norm(direction))
            if norm <= 0.0:
                raise ValueError("infinite direction must be non-zero")
            direction = direction / norm

            if self.direction_interval is None:
                angle = math.atan2(float(direction[1]), float(direction[0]))
                interval = (angle, angle)
                kind = self.cone_kind or "ray"
            else:
                if len(self.direction_interval) != 2:
                    raise ValueError("direction_interval must have two angles")
                start = _coerce_finite_real(
                    self.direction_interval[0], name="direction_interval[0]"
                )
                end = _coerce_finite_real(
                    self.direction_interval[1], name="direction_interval[1]"
                )
                span = end - start
                if span < -1e-12 or span > math.pi + 1e-9:
                    raise ValueError("direction_interval span must be in [0, pi]")
                if span < 0.0:
                    span = 0.0
                    end = start
                interval = (start, end)
                kind = self.cone_kind or (
                    "line" if abs(span - math.pi) <= 1e-9 else "cone"
                )

            if self.extreme_rays is None:
                start_ray = np.array(
                    [math.cos(interval[0]), math.sin(interval[0])],
                    dtype=np.float64,
                )
                end_ray = np.array(
                    [math.cos(interval[1]), math.sin(interval[1])],
                    dtype=np.float64,
                )
                rays = (start_ray, end_ray)
            else:
                if len(self.extreme_rays) != 2:
                    raise ValueError("extreme_rays must contain two rays")
                first = _coerce_finite_vector2(
                    self.extreme_rays[0], name="extreme_rays[0]"
                )
                second = _coerce_finite_vector2(
                    self.extreme_rays[1], name="extreme_rays[1]"
                )
                first_norm = float(np.linalg.norm(first))
                second_norm = float(np.linalg.norm(second))
                if first_norm <= 0.0 or second_norm <= 0.0:
                    raise ValueError("extreme rays must be non-zero")
                rays = (first / first_norm, second / second_norm)

            direction.setflags(write=False)
            rays[0].setflags(write=False)
            rays[1].setflags(write=False)
            object.__setattr__(self, "direction", direction)
            object.__setattr__(self, "direction_interval", interval)
            object.__setattr__(self, "extreme_rays", rays)
            object.__setattr__(self, "cone_kind", kind)
        self.prev = None
        self.next = None

    @classmethod
    def finite(cls, point: object) -> RegionNode:
        return cls(point=point, is_infinite=False)

    @classmethod
    def infinite(
        cls,
        direction: object,
        *,
        direction_interval: tuple[float, float] | None = None,
        extreme_rays: tuple[object, object] | None = None,
        cone_kind: str | None = None,
    ) -> RegionNode:
        return cls(
            point=None,
            is_infinite=True,
            direction=direction,
            direction_interval=direction_interval,
            extreme_rays=extreme_rays,
            cone_kind=cone_kind,
        )

    def clone(self) -> RegionNode:
        if not self.is_infinite:
            return RegionNode.finite(self.point)
        return RegionNode.infinite(
            self.direction,
            direction_interval=self.direction_interval,
            extreme_rays=self.extreme_rays,
            cone_kind=self.cone_kind,
        )


class RegionPolygon:
    """有效区域边界的有序双向循环链表。

    有界多边形只含真顶点；无界多边形含唯一无限节点，并连接有限边界链的
    两端。节点 ``next/prev`` 始终成对一致。
    """

    __slots__ = (
        "head",
        "tail",
        "size",
        "is_closed",
        "degenerate_reason",
        "halfplanes",
        "source_wedges",
    )

    def __init__(
        self,
        nodes: Iterable[RegionNode],
        *,
        is_closed: bool,
        degenerate_reason: str | None = None,
        halfplanes: Iterable[object] | None = None,
        source_wedges: Iterable[ObservationWedge] | None = None,
    ) -> None:
        if not isinstance(is_closed, bool):
            raise TypeError("is_closed 必须是 bool")
        if degenerate_reason is not None and (
            not isinstance(degenerate_reason, str) or not degenerate_reason
        ):
            raise ValueError("degenerate_reason 必须是非空字符串或 None")
        items = list(nodes)
        if not items:
            raise DegenerateEffectiveRegionError(
                "有效区域没有可编号的边界节点，不能构造双向链表"
            )
        for index, node in enumerate(items):
            if not isinstance(node, RegionNode):
                raise TypeError(f"nodes[{index}] 必须是 RegionNode")

        self.head: RegionNode | None = None
        self.tail: RegionNode | None = None
        self.size = 0
        self.is_closed = is_closed
        self.degenerate_reason = degenerate_reason
        self.halfplanes = tuple(
            plane.clone() for plane in (halfplanes or ())
        )
        self.source_wedges = tuple(
            wedge.clone() for wedge in (source_wedges or ())
        )

        built = [node.clone() for node in items]
        for index, node in enumerate(built):
            node.prev = built[index - 1]
            node.next = built[(index + 1) % len(built)]
        self.head = built[0]
        self.tail = built[-1]
        self.size = len(built)
        self.validate()

    def __len__(self) -> int:
        return self.size

    def __iter__(self):
        if self.head is None:
            return
        node = self.head
        for _ in range(self.size):
            yield node
            if node.next is None:
                raise LinkedListInvariantError("链表出现未连接节点")
            node = node.next
        if node is not self.head:
            raise LinkedListInvariantError("链表头尾未闭合")

    def iter_reverse(self):
        if self.tail is None:
            return
        node = self.tail
        for _ in range(self.size):
            yield node
            if node.prev is None:
                raise LinkedListInvariantError("反向链表出现未连接节点")
            node = node.prev
        if node is not self.tail:
            raise LinkedListInvariantError("反向链表头尾未闭合")

    def finite_vertex_chain(self) -> tuple[np.ndarray, ...]:
        return tuple(
            node.point.copy()
            for node in self
            if not node.is_infinite and node.point is not None
        )

    def infinite_nodes(self) -> tuple[RegionNode, ...]:
        return tuple(node for node in self if node.is_infinite)

    @property
    def infinite_node(self) -> RegionNode | None:
        result = self.infinite_nodes()
        if not result:
            return None
        if len(result) != 1:
            raise LinkedListInvariantError("无界有效区域必须恰有一个无限节点")
        return result[0]

    @property
    def infinite_direction(self) -> np.ndarray | None:
        node = self.infinite_node
        if node is None or node.direction is None:
            return None
        return node.direction.copy()

    def clone(self) -> RegionPolygon:
        return RegionPolygon(
            list(self),
            is_closed=self.is_closed,
            degenerate_reason=self.degenerate_reason,
            halfplanes=self.halfplanes,
            source_wedges=self.source_wedges,
        )

    def replace_vertices_in_place(
        self,
        points: Iterable[object],
        *,
        halfplanes: Iterable[object] | None = None,
        source_wedges: Iterable[ObservationWedge] | None = None,
    ) -> None:
        """原地替换有效区域顶点，保持当前 ``RegionPolygon`` 对象不变。

        真节点按给定顺序首尾互连。该方法用于真正迭代阶段：每加入一个观测点，
        当前多边形对象被更新，而不是创建新的 ``RegionPolygon``。
        """

        ordered = [
            _coerce_finite_vector2(point, name=f"points[{index}]")
            for index, point in enumerate(points)
        ]
        if len(ordered) < 3:
            raise DegenerateEffectiveRegionError(
                "原地更新后的有效区域少于三个真顶点"
            )

        nodes = [RegionNode.finite(point) for point in ordered]
        for index, node in enumerate(nodes):
            node.prev = nodes[index - 1]
            node.next = nodes[(index + 1) % len(nodes)]

        self.head = nodes[0]
        self.tail = nodes[-1]
        self.size = len(nodes)
        self.is_closed = True
        self.degenerate_reason = None
        if halfplanes is not None:
            self.halfplanes = tuple(plane.clone() for plane in halfplanes)
        if source_wedges is not None:
            self.source_wedges = tuple(wedge.clone() for wedge in source_wedges)
        self.validate()

    def validate(self) -> bool:
        if self.head is None or self.tail is None or self.size <= 0:
            raise LinkedListInvariantError("空链表不能作为有效区域")
        seen: list[RegionNode] = []
        node: RegionNode | None = self.head
        for _ in range(self.size):
            if node is None:
                raise LinkedListInvariantError("链表提前结束")
            if node in seen:
                raise LinkedListInvariantError("链表出现重复节点或环")
            seen.append(node)
            if node.next is None or node.next.prev is not node:
                raise LinkedListInvariantError("next/prev 关系不对称")
            if node.prev is None or node.prev.next is not node:
                raise LinkedListInvariantError("prev/next 关系不对称")
            node = node.next
        if node is not self.head:
            raise LinkedListInvariantError("头尾未互连")
        if self.tail is not seen[-1]:
            raise LinkedListInvariantError("tail 指针不正确")
        infinite_count = sum(item.is_infinite for item in seen)
        if self.is_closed:
            if infinite_count:
                raise LinkedListInvariantError("有界闭合链表不能含无限节点")
            if self.size < 3:
                raise LinkedListInvariantError("有界闭合链表至少需要三个真顶点")
        elif infinite_count != 1:
            raise LinkedListInvariantError("无界链表必须恰含一个无限节点")
        return True

    def __repr__(self) -> str:
        kind = "closed" if self.is_closed else "unbounded"
        return f"RegionPolygon(size={self.size}, kind={kind})"


@dataclass(frozen=True, slots=True)
class _HalfPlane:
    normal: np.ndarray
    offset: float

    def __post_init__(self) -> None:
        normal = _coerce_finite_vector2(self.normal, name="normal")
        norm = float(np.linalg.norm(normal))
        if norm <= 0.0:
            raise ValueError("半平面法向量必须非零")
        normal = normal / norm
        offset = _coerce_finite_real(self.offset, name="offset") / norm
        normal.setflags(write=False)
        object.__setattr__(self, "normal", normal)
        object.__setattr__(self, "offset", offset)

    def value(self, point: np.ndarray) -> float:
        return float(np.dot(self.normal, point) - self.offset)

    def clone(self) -> _HalfPlane:
        return _HalfPlane(self.normal.copy(), self.offset)

    def translated(self, origin: object) -> _HalfPlane:
        reference = _coerce_finite_vector2(origin, name="origin")
        return _HalfPlane(
            self.normal.copy(),
            self.offset - float(np.dot(self.normal, reference)),
        )


@dataclass(frozen=True, slots=True)
class _RecessionInfo:
    direction: np.ndarray
    kind: str
    angular_span: float
    direction_interval: tuple[float, float]
    extreme_rays: tuple[np.ndarray, np.ndarray]


def _wedge_halfplanes(wedge: ObservationWedge) -> tuple[_HalfPlane, _HalfPlane]:
    minus_normal = np.array(
        [wedge.u_minus[1], -wedge.u_minus[0]], dtype=np.float64
    )
    plus_normal = np.array(
        [-wedge.u_plus[1], wedge.u_plus[0]], dtype=np.float64
    )
    return (
        _HalfPlane(minus_normal, float(np.dot(minus_normal, wedge.point))),
        _HalfPlane(plus_normal, float(np.dot(plus_normal, wedge.point))),
    )


def _roundoff_tolerance(*values: float) -> float:
    scale = max(1.0, *(abs(value) for value in values))
    return max(GEOMETRY_TOLERANCE, 64.0 * np.finfo(float).eps * scale)


def _halfplane_tolerance(point: np.ndarray, plane: _HalfPlane) -> float:
    value = abs(plane.value(point))
    return _roundoff_tolerance(value, abs(plane.offset))


def _satisfies_halfplanes(
    point: np.ndarray, planes: list[_HalfPlane]
) -> bool:
    return all(
        plane.value(point) <= _halfplane_tolerance(point, plane)
        for plane in planes
    )


def _find_strict_interior(
    planes: list[_HalfPlane],
) -> np.ndarray:
    """用 Chebyshev 半径 LP 求严格内点，不使用包围盒近似区域。"""

    if not planes:
        return np.zeros(2, dtype=np.float64)
    matrix = np.vstack([plane.normal for plane in planes])
    offsets = np.array([plane.offset for plane in planes], dtype=np.float64)
    objective = np.array([0.0, 0.0, -1.0], dtype=np.float64)
    constraints = np.column_stack(
        [matrix, np.ones(matrix.shape[0], dtype=np.float64)]
    )
    result = linprog(
        objective,
        A_ub=constraints,
        b_ub=offsets,
        bounds=[(None, None), (None, None), (0.0, 1.0)],
        method="highs",
    )
    if result.status == 2 or not result.success:
        if result.status == 2:
            raise EmptyEffectiveRegionError("有效扇形交集为空")
        raise DegenerateEffectiveRegionError(
            f"无法求有效区域严格内点：{result.message}"
        )
    point = np.asarray(result.x[:2], dtype=np.float64)
    radius = float(result.x[2])
    if radius <= GEOMETRY_TOLERANCE:
        raise DegenerateEffectiveRegionError(
            "有效区域没有二维内点，已退化到点、线段或射线"
        )
    return point


def _line_intersection(
    first: _HalfPlane,
    second: _HalfPlane,
) -> np.ndarray | None:
    determinant = float(
        first.normal[0] * second.normal[1]
        - first.normal[1] * second.normal[0]
    )
    if abs(determinant) <= 1e-12:
        return None
    point = np.array(
        [
            (first.offset * second.normal[1] - first.normal[1] * second.offset)
            / determinant,
            (first.normal[0] * second.offset - first.offset * second.normal[0])
            / determinant,
        ],
        dtype=np.float64,
    )
    return point


def _dedupe_points(points: Iterable[np.ndarray]) -> list[np.ndarray]:
    """Deduplicate after translating by a local reference point."""

    items = [np.asarray(point, dtype=np.float64).copy() for point in points]
    if not items:
        return []
    reference = items[0].copy()
    tolerance = _roundoff_tolerance(float(np.linalg.norm(reference)))
    result: list[np.ndarray] = []
    shifted_result: list[np.ndarray] = []
    for point in items:
        shifted = point - reference
        if any(
            float(np.linalg.norm(shifted - existing)) <= tolerance
            for existing in shifted_result
        ):
            continue
        result.append(point)
        shifted_result.append(shifted)
    return result


def _enumerate_vertices(
    planes: list[_HalfPlane],
) -> list[np.ndarray]:
    points: list[np.ndarray] = []
    for first_index in range(len(planes)):
        for second_index in range(first_index + 1, len(planes)):
            point = _line_intersection(
                planes[first_index], planes[second_index]
            )
            if point is None or not _satisfies_halfplanes(point, planes):
                continue
            points.append(point)
    return _dedupe_points(points)


def _normalized_angles(vectors: Iterable[np.ndarray]) -> list[float]:
    angles = [
        math.atan2(float(vector[1]), float(vector[0])) % (2.0 * math.pi)
        for vector in vectors
    ]
    if not angles:
        return []
    angles.sort()
    unique: list[float] = [angles[0]]
    for angle in angles[1:]:
        if angle - unique[-1] > 1e-12:
            unique.append(angle)
    if (
        len(unique) > 1
        and unique[0] + 2.0 * math.pi - unique[-1] <= 1e-12
    ):
        unique.pop()
    return unique


def _unit_at_angle(angle: float) -> np.ndarray:
    return np.array([math.cos(angle), math.sin(angle)], dtype=np.float64)


def _make_interval_info(
    direction: np.ndarray,
    kind: str,
    start: float,
    span: float,
    *,
    extreme_rays: tuple[np.ndarray, np.ndarray] | None = None,
) -> _RecessionInfo:
    end = start + span
    if extreme_rays is None:
        extreme_rays = (_unit_at_angle(start), _unit_at_angle(end))
    return _RecessionInfo(
        direction=np.asarray(direction, dtype=np.float64).copy(),
        kind=kind,
        angular_span=span,
        direction_interval=(float(start), float(end)),
        extreme_rays=(
            np.asarray(extreme_rays[0], dtype=np.float64).copy(),
            np.asarray(extreme_rays[1], dtype=np.float64).copy(),
        ),
    )


def _recession_info(
    wedges: list[ObservationWedge],
    tol: float,
) -> _RecessionInfo:
    """Find the complete common recession cone and both extreme rays."""

    if not wedges:
        return _make_interval_info(
            np.array([1.0, 0.0], dtype=np.float64),
            "full",
            0.0,
            math.pi,
            extreme_rays=(
                np.array([1.0, 0.0], dtype=np.float64),
                np.array([-1.0, 0.0], dtype=np.float64),
            ),
        )

    raw_angles: list[float] = []
    for wedge in wedges:
        for vector in (
            wedge.u_minus,
            wedge.u_plus,
            -wedge.u_minus,
            -wedge.u_plus,
        ):
            norm = float(np.linalg.norm(vector))
            if norm > 0.0:
                raw_angles.append(
                    math.atan2(float(vector[1]), float(vector[0]))
                    % (2.0 * math.pi)
                )
    base_angles = _normalized_angles(
        np.array([math.cos(angle), math.sin(angle)]) for angle in raw_angles
    )
    if not base_angles:
        raise DegenerateEffectiveRegionError(
            "cannot sample a non-empty recession cone"
        )
    sample_angles = list(base_angles)
    for index, angle in enumerate(base_angles):
        next_angle = base_angles[(index + 1) % len(base_angles)]
        gap = (next_angle - angle) % (2.0 * math.pi)
        if gap > 0.0:
            sample_angles.append((angle + 0.5 * gap) % (2.0 * math.pi))

    feasible: list[np.ndarray] = []
    for angle in sample_angles:
        vector = _unit_at_angle(angle)
        if _direction_satisfies_wedges(vector, wedges, tol):
            feasible.append(vector)
    if not feasible:
        fallback = find_unbounded_direction(wedges, tol)
        if fallback is None:
            raise DegenerateEffectiveRegionError(
                "unbounded region has no common recession direction"
            )
        angle = math.atan2(float(fallback[1]), float(fallback[0]))
        return _make_interval_info(fallback, "ray", angle, 0.0)

    base = feasible[0]
    collinear = all(
        abs(cross_2d(base, vector)) <= 1e-10 for vector in feasible
    )
    if collinear:
        opposite = any(
            float(np.dot(base, vector)) < -1.0 + 1e-10
            for vector in feasible
        )
        angle = math.atan2(float(base[1]), float(base[0]))
        if opposite:
            return _make_interval_info(
                base,
                "line",
                angle,
                math.pi,
                extreme_rays=(base, -base),
            )
        return _make_interval_info(base, "ray", angle, 0.0)

    angles = _normalized_angles(feasible)
    if len(angles) < 2:
        angle = math.atan2(float(base[1]), float(base[0]))
        return _make_interval_info(base, "ray", angle, 0.0)
    gaps = [
        (angles[(index + 1) % len(angles)] - angle) % (2.0 * math.pi)
        for index, angle in enumerate(angles)
    ]
    largest_index = max(range(len(gaps)), key=gaps.__getitem__)
    largest_gap = gaps[largest_index]
    if largest_gap < 1e-8:
        return _make_interval_info(
            base,
            "full",
            0.0,
            math.pi,
            extreme_rays=(
                np.array([1.0, 0.0], dtype=np.float64),
                np.array([-1.0, 0.0], dtype=np.float64),
            ),
        )
    span = 2.0 * math.pi - largest_gap
    start = angles[(largest_index + 1) % len(angles)]
    middle = start + 0.5 * span
    direction = _unit_at_angle(middle)
    kind = "halfplane" if abs(span - math.pi) <= 1e-8 else "cone"
    if not _direction_satisfies_wedges(direction, wedges, tol):
        fallback = find_unbounded_direction(wedges, tol)
        if fallback is None:
            raise DegenerateEffectiveRegionError(
                "failed to recover a recession-cone interior direction"
            )
        direction = fallback
    return _make_interval_info(
        direction,
        kind,
        start,
        span,
        extreme_rays=(_unit_at_angle(start), _unit_at_angle(start + span)),
    )


def _direction_satisfies_planes(
    direction: np.ndarray,
    planes: list[_HalfPlane],
    tol: float,
) -> bool:
    return all(
        float(np.dot(plane.normal, direction)) <= tol
        for plane in planes
    )


def _recession_info_from_planes(
    planes: list[_HalfPlane],
    tol: float,
) -> _RecessionInfo | None:
    """Classify the complete homogeneous cone A d <= 0."""

    if not planes:
        return _make_interval_info(
            np.array([1.0, 0.0], dtype=np.float64),
            "full",
            0.0,
            math.pi,
            extreme_rays=(
                np.array([1.0, 0.0], dtype=np.float64),
                np.array([-1.0, 0.0], dtype=np.float64),
            ),
        )

    base_angles = _normalized_angles(
        np.array([plane.normal[1], -plane.normal[0]], dtype=np.float64)
        for plane in planes
    )
    for angle in list(base_angles):
        base_angles.append((angle + math.pi) % (2.0 * math.pi))
    base_angles = _normalized_angles(
        _unit_at_angle(angle) for angle in base_angles
    )
    sample_angles = list(base_angles)
    for index, angle in enumerate(base_angles):
        next_angle = base_angles[(index + 1) % len(base_angles)]
        gap = (next_angle - angle) % (2.0 * math.pi)
        if gap > 0.0:
            sample_angles.append((angle + 0.5 * gap) % (2.0 * math.pi))

    feasible = [
        _unit_at_angle(angle)
        for angle in sample_angles
        if _direction_satisfies_planes(_unit_at_angle(angle), planes, tol)
    ]
    if not feasible:
        return None

    base = feasible[0]
    collinear = all(
        abs(cross_2d(base, vector)) <= 1e-10 for vector in feasible
    )
    if collinear:
        opposite = any(
            float(np.dot(base, vector)) < -1.0 + 1e-10
            for vector in feasible
        )
        angle = math.atan2(float(base[1]), float(base[0]))
        if opposite:
            return _make_interval_info(
                base,
                "line",
                angle,
                math.pi,
                extreme_rays=(base, -base),
            )
        return _make_interval_info(base, "ray", angle, 0.0)

    angles = _normalized_angles(feasible)
    if len(angles) < 2:
        angle = math.atan2(float(base[1]), float(base[0]))
        return _make_interval_info(base, "ray", angle, 0.0)
    gaps = [
        (angles[(index + 1) % len(angles)] - angle) % (2.0 * math.pi)
        for index, angle in enumerate(angles)
    ]
    largest_index = max(range(len(gaps)), key=gaps.__getitem__)
    largest_gap = gaps[largest_index]
    if largest_gap < 1e-8:
        return _make_interval_info(
            base,
            "full",
            0.0,
            math.pi,
            extreme_rays=(
                np.array([1.0, 0.0], dtype=np.float64),
                np.array([-1.0, 0.0], dtype=np.float64),
            ),
        )
    span = 2.0 * math.pi - largest_gap
    start = angles[(largest_index + 1) % len(angles)]
    middle = start + 0.5 * span
    kind = "halfplane" if abs(span - math.pi) <= 1e-8 else "cone"
    return _make_interval_info(
        _unit_at_angle(middle),
        kind,
        start,
        span,
        extreme_rays=(_unit_at_angle(start), _unit_at_angle(start + span)),
    )


def _sort_vertices_ccw(
    points: list[np.ndarray],
    interior: np.ndarray,
) -> tuple[list[np.ndarray], list[float]]:
    if not points:
        return [], []
    angles = [
        math.atan2(float(point[1] - interior[1]), float(point[0] - interior[0]))
        % (2.0 * math.pi)
        for point in points
    ]
    order = sorted(range(len(points)), key=angles.__getitem__)
    sorted_points = [points[index] for index in order]
    sorted_angles = [angles[index] for index in order]
    for index in range(1, len(sorted_angles)):
        if sorted_angles[index] - sorted_angles[index - 1] <= 1e-10:
            raise DegenerateEffectiveRegionError(
                "多个真顶点与内点共线，无法唯一确定边界顺序"
            )
    return sorted_points, sorted_angles


def _polygon_signed_area(points: list[np.ndarray]) -> float:
    if not points:
        return 0.0
    reference = points[0]
    shifted = [point - reference for point in points]
    area = 0.0
    for index, point in enumerate(shifted):
        next_point = shifted[(index + 1) % len(shifted)]
        area += float(cross_2d(point, next_point))
    return 0.5 * area


def _build_polygon_from_halfplanes(
    planes_input: Iterable[_HalfPlane],
    source_wedges_input: Iterable[ObservationWedge],
    tol: float,
) -> RegionPolygon:
    """Build an exact region from accumulated halfplanes."""

    planes = [plane.clone() for plane in planes_input]
    source_wedges = [wedge.clone() for wedge in source_wedges_input]
    if not planes:
        info = _recession_info([], tol)
        return RegionPolygon(
            [
                RegionNode.infinite(
                    info.direction,
                    direction_interval=info.direction_interval,
                    extreme_rays=info.extreme_rays,
                    cone_kind=info.kind,
                )
            ],
            is_closed=False,
            degenerate_reason=None,
            halfplanes=(),
            source_wedges=(),
        )

    reference = (
        source_wedges[0].point.copy()
        if source_wedges
        else np.zeros(2, dtype=np.float64)
    )
    local_planes = [plane.translated(reference) for plane in planes]
    interior_local = _find_strict_interior(local_planes)
    vertices_local = _enumerate_vertices(local_planes)
    interior = interior_local + reference
    vertices = [point + reference for point in vertices_local]
    info = _recession_info_from_planes(planes, tol)
    unbounded = info is not None

    if not unbounded:
        if len(vertices) < 3:
            raise DegenerateEffectiveRegionError(
                "bounded region has fewer than three vertices"
            )
        sorted_points, _ = _sort_vertices_ccw(vertices, interior)
        area = _polygon_signed_area(sorted_points)
        if abs(area) <= GEOMETRY_TOLERANCE:
            raise DegenerateEffectiveRegionError("bounded region has zero area")
        if area < 0.0:
            sorted_points.reverse()
        return RegionPolygon(
            [RegionNode.finite(point) for point in sorted_points],
            is_closed=True,
            halfplanes=planes,
            source_wedges=source_wedges,
        )

    if info is None:
        raise DegenerateEffectiveRegionError(
            "unbounded region has no recession direction"
        )

    if not vertices:
        return RegionPolygon(
            [
                RegionNode.infinite(
                    info.direction,
                    direction_interval=info.direction_interval,
                    extreme_rays=info.extreme_rays,
                    cone_kind=info.kind,
                )
            ],
            is_closed=False,
            degenerate_reason=(
                "unbounded ray/cone without a finite apex; "
                "infinite-cone metadata retained"
                if info.kind in {"ray", "cone"}
                else None
            ),
            halfplanes=planes,
            source_wedges=source_wedges,
        )

    sorted_points, sorted_angles = _sort_vertices_ccw(vertices, interior)
    infinity_angle = math.atan2(
        float(info.direction[1]), float(info.direction[0])
    ) % (2.0 * math.pi)
    insert_after = len(sorted_points) - 1
    for index, angle in enumerate(sorted_angles):
        next_angle = sorted_angles[(index + 1) % len(sorted_angles)]
        gap = (next_angle - angle) % (2.0 * math.pi)
        relative = (infinity_angle - angle) % (2.0 * math.pi)
        if relative <= gap + 1e-10:
            insert_after = index
            break

    nodes = [RegionNode.finite(point) for point in sorted_points]
    nodes.insert(
        insert_after + 1,
        RegionNode.infinite(
            info.direction,
            direction_interval=info.direction_interval,
            extreme_rays=info.extreme_rays,
            cone_kind=info.kind,
        ),
    )
    return RegionPolygon(
        nodes,
        is_closed=False,
        halfplanes=planes,
        source_wedges=source_wedges,
    )


def build_effective_region_polygon(
    wedges: Iterable[ObservationWedge],
    tol: Real = DEFAULT_TOLERANCE,
) -> RegionPolygon:
    """??????????????????????"""

    checked_wedges = _coerce_wedges(wedges)
    checked_tol = _validate_tolerance(tol)
    planes: list[_HalfPlane] = []
    for wedge in checked_wedges:
        planes.extend(_wedge_halfplanes(wedge))
    return _build_polygon_from_halfplanes(
        planes,
        checked_wedges,
        checked_tol,
    )


def clip_polygon_with_wedge(
    previous_polygon: RegionPolygon,
    wedge: ObservationWedge,
    tol: Real = DEFAULT_TOLERANCE,
) -> RegionPolygon:
    """Strictly clip a linked region by one observation wedge.

    The accumulated halfplanes are taken from ``previous_polygon`` rather than
    rebuilding from the caller's original wedge list.  The new halfplanes are
    appended to that exact prior state, so the resulting linked list is the
    geometric intersection of the previous region and the new wedge.
    """

    if not isinstance(previous_polygon, RegionPolygon):
        raise TypeError("previous_polygon must be RegionPolygon")
    if not isinstance(wedge, ObservationWedge):
        raise TypeError("wedge must be ObservationWedge")
    checked_tol = _validate_tolerance(tol)
    planes = [plane.clone() for plane in previous_polygon.halfplanes]
    source_wedges = list(previous_polygon.source_wedges)
    if not planes and source_wedges:
        for previous_wedge in source_wedges:
            planes.extend(_wedge_halfplanes(previous_wedge))
    planes.extend(_wedge_halfplanes(wedge))
    source_wedges.append(wedge.clone())
    return _build_polygon_from_halfplanes(
        planes,
        source_wedges,
        checked_tol,
    )


def _iteration_roundoff_tolerance(tol: float, *values: float) -> float:
    """在调用方容差与当前数值尺度之间取最小可行的舍入容差。"""

    scale = max(1.0, *(abs(float(value)) for value in values))
    return max(tol, 8.0 * np.finfo(np.float64).eps * scale)


def _warn_unhandled(message: str) -> None:
    """对题目未明确规定或理论上不会出现的分支发出 warning。"""

    warnings.warn(message, RuntimeWarning, stacklevel=2)


def _clip_points_by_retained_halfplane(
    points: list[np.ndarray],
    plane: _HalfPlane,
    tol: float,
) -> list[np.ndarray] | None:
    """按 ``g(X) >= 0`` 对有序凸多边形做一次裁剪。

    这里 ``g = -plane.value``；``_wedge_halfplanes`` 给出的半平面内部满足
    ``plane.value <= 0``。实现严格对应逐边分类和交点公式。
    """

    if len(points) < 3:
        return []
    reference = points[0]
    local_points = [point - reference for point in points]
    local_plane = plane.translated(reference)
    output: list[np.ndarray] = []

    for index, point_a in enumerate(local_points):
        point_b = local_points[(index + 1) % len(local_points)]
        value_a = -local_plane.value(point_a)
        value_b = -local_plane.value(point_b)
        edge_tol = _iteration_roundoff_tolerance(tol, value_a, value_b)
        inside_a = value_a >= -edge_tol
        inside_b = value_b >= -edge_tol

        if inside_a and inside_b:
            output.append(point_b.copy())
            continue
        if not inside_a and not inside_b:
            continue

        denominator = value_a - value_b
        if abs(denominator) <= edge_tol:
            _warn_unhandled(
                "半平面裁剪遇到数值上不可稳定的内外交点，保留当前多边形"
            )
            return None

        ratio = value_a / denominator
        intersection = point_a + ratio * (point_b - point_a)
        output.append(intersection.copy())
        if not inside_a and inside_b:
            output.append(point_b.copy())

    return [point + reference for point in output]


def _cleanup_clipped_vertices(
    points: list[np.ndarray],
    tol: float,
) -> list[np.ndarray]:
    """两次裁剪后清除重复点和落在相邻边上的多余共线点。"""

    if not points:
        return []
    reference = points[0]
    local_points = [point - reference for point in points]
    scale = max(
        1.0,
        max(float(np.linalg.norm(point)) for point in local_points),
    )
    distance_tol = _iteration_roundoff_tolerance(tol, scale)

    unique: list[np.ndarray] = []
    for point in local_points:
        if not unique or float(np.linalg.norm(point - unique[-1])) > distance_tol:
            unique.append(point.copy())
    while len(unique) > 1 and float(
        np.linalg.norm(unique[0] - unique[-1])
    ) <= distance_tol:
        unique.pop()

    if len(unique) < 3:
        return [point + reference for point in unique]

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
            collinear = abs(cross_2d(first, second)) <= distance_tol * (
                first_norm + second_norm
            )
            forward = float(np.dot(first, second)) >= -(distance_tol ** 2)
            if collinear and forward:
                changed = True
                continue
            kept.append(point.copy())
        unique = kept

    return [point + reference for point in unique]


def update_effective_region_in_place(
    polygon: RegionPolygon,
    wedge: ObservationWedge,
    tol: Real = DEFAULT_TOLERANCE,
) -> RegionPolygon:
    """用一个观测点的两个半平面原地裁剪有效区域多边形。

    两次裁剪依次执行，全部完成后再清除重复点和多余共线点。只有结果有效时
    才修改传入的 ``RegionPolygon``；同一个多边形对象贯穿整个真正迭代阶段。
    """

    if not isinstance(polygon, RegionPolygon):
        raise TypeError("polygon 必须是 RegionPolygon")
    if not isinstance(wedge, ObservationWedge):
        raise TypeError("wedge 必须是 ObservationWedge")
    checked_tol = _validate_tolerance(tol)
    if not polygon.is_closed or polygon.infinite_node is not None:
        _warn_unhandled(
            "真正迭代要求有界闭合多边形；当前区域含无限节点，保留原区域"
        )
        return polygon

    points = [
        node.point.copy()
        for node in polygon
        if not node.is_infinite and node.point is not None
    ]
    if len(points) < 3:
        _warn_unhandled("当前有效区域少于三个真顶点，保留原区域")
        return polygon

    new_planes = _wedge_halfplanes(wedge)
    working_points = points
    for plane in new_planes:
        clipped = _clip_points_by_retained_halfplane(
            working_points, plane, checked_tol
        )
        if clipped is None or len(clipped) < 3:
            _warn_unhandled(
                "半平面裁剪后未得到至少三个顶点，保留裁剪前区域"
            )
            return polygon
        working_points = clipped

    cleaned = _cleanup_clipped_vertices(working_points, checked_tol)
    if len(cleaned) < 3:
        _warn_unhandled("顶点清理后有效区域退化，保留裁剪前区域")
        return polygon

    area = _polygon_signed_area(cleaned)
    if area < 0.0:
        cleaned.reverse()

    halfplanes = [plane.clone() for plane in polygon.halfplanes]
    halfplanes.extend(plane.clone() for plane in new_planes)
    source_wedges = list(polygon.source_wedges)
    source_wedges.append(wedge.clone())
    polygon.replace_vertices_in_place(
        cleaned,
        halfplanes=halfplanes,
        source_wedges=source_wedges,
    )
    return polygon


def continue_effective_region_iteration(
    polygon: RegionPolygon,
    observations: Iterable[object],
    tol: Real = DEFAULT_TOLERANCE,
) -> RegionPolygon:
    """从奠基后的下一个观测点开始，原地继续迭代。"""

    checked_tol = _validate_tolerance(tol)
    checked_wedges = _coerce_observations(observations, minimum_count=0)
    for wedge in checked_wedges:
        update_effective_region_in_place(polygon, wedge, checked_tol)
    return polygon


def _write_effective_region_to_goal_position(
    polygon: RegionPolygon,
    wedges: list[ObservationWedge],
) -> bool:
    """把所有观测迭代后的最终有效区域写入 ``goal_position``。"""

    if not polygon.is_closed or polygon.infinite_node is not None:
        _warn_unhandled("最终有效区域不是有界闭合多边形，未写入 goal_position")
        return False
    points = list(polygon.finite_vertex_chain())
    if len(points) < 3:
        _warn_unhandled("最终有效区域少于三个顶点，未写入 goal_position")
        return False

    try:
        goal_position = problem_variables.DoublyLinkedList.from_coordinates(
            points, closed=True
        )
        problem_variables.set_problem_variables(
            len(wedges),
            [wedge.point.copy() for wedge in wedges],
            goal_position,
            [wedge.direction_deg for wedge in wedges],
        )
    except Exception as exc:  # warning-only behavior for unspecified states
        _warn_unhandled(f"无法写入 goal_position：{exc}")
        return False
    return True


def iterate_effective_region(
    observations: Iterable[object],
    tol: Real = DEFAULT_TOLERANCE,
    *,
    update_goal_position: bool = True,
) -> RegionPolygon:
    """完整执行奠基与真正迭代，并在结束时输出到 ``goal_position``。"""

    if not isinstance(update_goal_position, bool):
        raise TypeError("update_goal_position 必须是 bool")
    checked_tol = _validate_tolerance(tol)
    all_wedges = _coerce_observations(observations)
    foundation = iterative_select_until_bounded(all_wedges, checked_tol)
    polygon = foundation.final_polygon
    if not foundation.bounded or not polygon.is_closed:
        _warn_unhandled(
            "全部观测处理完后有效区域仍未形成有界多边形，无法进入原地迭代"
        )
        return polygon

    consumed = len(foundation.selected_indices)
    continue_effective_region_iteration(
        polygon,
        all_wedges[consumed:],
        checked_tol,
    )
    if update_goal_position:
        _write_effective_region_to_goal_position(polygon, all_wedges)
    return polygon


@dataclass(frozen=True, slots=True)
class InfiniteNode:
    """Compatibility marker carrying the complete infinite-cone metadata."""

    direction: np.ndarray
    marker: str = "INFINITY"
    direction_interval: tuple[float, float] | None = None
    extreme_rays: tuple[np.ndarray, np.ndarray] | None = None
    cone_kind: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.marker, str) or not self.marker:
            raise ValueError("marker must be a non-empty string")
        direction = _coerce_finite_vector2(self.direction, name="direction")
        norm = float(np.linalg.norm(direction))
        if norm == 0.0:
            raise ValueError("infinite-node direction must be non-zero")
        if self.direction_interval is not None:
            if len(self.direction_interval) != 2:
                raise ValueError("direction_interval must have two angles")
            start = _coerce_finite_real(
                self.direction_interval[0], name="direction_interval[0]"
            )
            end = _coerce_finite_real(
                self.direction_interval[1], name="direction_interval[1]"
            )
            if end - start < -1e-12 or end - start > math.pi + 1e-9:
                raise ValueError("direction_interval span must be in [0, pi]")
            interval = (start, end)
        else:
            interval = None
        rays: tuple[np.ndarray, np.ndarray] | None = None
        if self.extreme_rays is not None:
            if len(self.extreme_rays) != 2:
                raise ValueError("extreme_rays must contain two rays")
            first = _coerce_finite_vector2(
                self.extreme_rays[0], name="extreme_rays[0]"
            )
            second = _coerce_finite_vector2(
                self.extreme_rays[1], name="extreme_rays[1]"
            )
            first_norm = float(np.linalg.norm(first))
            second_norm = float(np.linalg.norm(second))
            if first_norm <= 0.0 or second_norm <= 0.0:
                raise ValueError("extreme rays must be non-zero")
            rays = (first / first_norm, second / second_norm)
            rays[0].setflags(write=False)
            rays[1].setflags(write=False)
        normalized = direction / norm
        normalized.setflags(write=False)
        object.__setattr__(self, "direction", normalized)
        object.__setattr__(self, "direction_interval", interval)
        object.__setattr__(self, "extreme_rays", rays)


@dataclass(frozen=True, slots=True)
class RegionSnapshot:
    """加入一个观测扇形后的有效区域链表状态。"""

    added_index: int
    selected_indices: tuple[int, ...]
    wedges: tuple[ObservationWedge, ...]
    unbounded_direction: np.ndarray | None
    is_closed: bool
    infinite_node: InfiniteNode | None = None
    finite_vertex_chain: tuple[np.ndarray, ...] = ()
    polygon: RegionPolygon | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.is_closed, bool):
            raise TypeError("is_closed must be bool")
        if not self.selected_indices:
            raise ValueError("selected_indices cannot be empty")
        checked_wedges: list[ObservationWedge] = []
        for index, wedge in enumerate(self.wedges):
            if not isinstance(wedge, ObservationWedge):
                raise TypeError(f"wedges[{index}] must be ObservationWedge")
            checked_wedges.append(wedge.clone())
        if len(checked_wedges) != len(self.selected_indices):
            raise ValueError("wedges length must match selected_indices")
        if self.polygon is None:
            polygon = build_effective_region_polygon(
                checked_wedges, DEFAULT_TOLERANCE
            )
        else:
            if not isinstance(self.polygon, RegionPolygon):
                raise TypeError("polygon must be RegionPolygon")
            polygon = self.polygon.clone()
        if polygon.is_closed != self.is_closed:
            raise ValueError("is_closed must match polygon.is_closed")

        infinite_direction = polygon.infinite_direction
        if self.is_closed:
            if infinite_direction is not None:
                raise ValueError("closed region cannot contain infinite direction")
            legacy_infinite = None
        else:
            if infinite_direction is None:
                raise ValueError("unbounded region must contain an infinite node")
            region_infinite = polygon.infinite_node
            if region_infinite is None:
                raise ValueError("unbounded polygon has no infinite node")
            legacy_infinite = InfiniteNode(
                infinite_direction,
                direction_interval=region_infinite.direction_interval,
                extreme_rays=region_infinite.extreme_rays,
                cone_kind=region_infinite.cone_kind,
            )

        object.__setattr__(self, "wedges", tuple(checked_wedges))
        object.__setattr__(self, "polygon", polygon)
        object.__setattr__(self, "unbounded_direction", infinite_direction)
        object.__setattr__(self, "infinite_node", legacy_infinite)
        object.__setattr__(
            self, "finite_vertex_chain", polygon.finite_vertex_chain()
        )

    @property
    def closed_polygon(self) -> bool:
        return self.is_closed

    @property
    def effective_region_closed(self) -> bool:
        return self.is_closed

    @property
    def effective_region_vertices(self) -> tuple[np.ndarray, ...]:
        return tuple(point.copy() for point in self.finite_vertex_chain)

    @property
    def constraints(self) -> tuple[ObservationWedge, ...]:
        return self.wedges


@dataclass(frozen=True, slots=True)
class IterativeBoundedResult:
    """有效区域归纳迭代奠基阶段的完整结果。"""

    selected_indices: tuple[int, ...]
    remaining_indices: tuple[int, ...]
    snapshots: tuple[RegionSnapshot, ...]
    final_state: RegionSnapshot
    bounded: bool

    def __iter__(self):
        yield self.selected_indices
        yield self.remaining_indices
        yield self.snapshots
        yield self.final_state

    @property
    def final_polygon(self) -> RegionPolygon:
        return self.final_state.polygon.clone()


_OBSERVATION_MISSING = object()


def _observation_point_and_direction(
    observation: object,
    sequence_index: int,
) -> tuple[object, object]:
    if isinstance(observation, Mapping):
        point = observation.get(
            "position", observation.get("point", _OBSERVATION_MISSING)
        )
        direction = observation.get("direction", _OBSERVATION_MISSING)
        if point is _OBSERVATION_MISSING or direction is _OBSERVATION_MISSING:
            raise ValueError(
                f"observations[{sequence_index}] 必须同时包含 position/point 和 direction"
            )
        return point, direction
    if hasattr(observation, "position") and hasattr(observation, "direction"):
        return observation.position, observation.direction
    if hasattr(observation, "point") and hasattr(observation, "direction"):
        return observation.point, observation.direction
    if isinstance(observation, (str, bytes)):
        raise TypeError(
            f"observations[{sequence_index}] 必须是 (point, direction) 或观测对象"
        )
    try:
        pair = tuple(observation)
    except TypeError as exc:
        raise TypeError(
            f"observations[{sequence_index}] 必须是 (point, direction) 或观测对象"
        ) from exc
    if len(pair) != 2:
        raise ValueError(
            f"observations[{sequence_index}] 必须恰好包含观测点和方向两个元素"
        )
    return pair[0], pair[1]


def _coerce_observations(
    observations: Iterable[object],
    *,
    minimum_count: int = 2,
) -> list[ObservationWedge]:
    if isinstance(observations, (str, bytes)) or not isinstance(
        observations, Iterable
    ):
        raise TypeError("observations 必须是可迭代对象")
    items = list(observations)
    checked_minimum = _validate_expected_length(
        minimum_count, name="minimum_count"
    )
    if len(items) < checked_minimum:
        if checked_minimum == 2:
            raise ValueError("observations 至少需要两个观测点")
        raise ValueError(
            f"observations 至少需要 {checked_minimum} 个观测点"
        )
    wedges: list[ObservationWedge] = []
    for sequence_index, observation in enumerate(items):
        if isinstance(observation, ObservationWedge):
            wedge = observation
        else:
            point, direction = _observation_point_and_direction(
                observation, sequence_index
            )
            wedge = ObservationWedge.from_direction(
                sequence_index, point, direction
            )
        wedges.append(wedge)
    return wedges


def iterative_select_until_bounded(
    observations: Iterable[object],
    tol: Real = DEFAULT_TOLERANCE,
) -> IterativeBoundedResult:
    """逐点求有效区域，每轮输出真顶点与无限节点组成的双向链表。"""

    checked_tol = _validate_tolerance(tol)
    all_wedges = _coerce_observations(observations)
    previous_polygon: RegionPolygon | None = None
    snapshots: list[RegionSnapshot] = []
    selected_indices: list[int] = []

    for position, wedge in enumerate(all_wedges, start=1):
        selected_indices.append(wedge.index)
        if previous_polygon is None:
            previous_polygon = build_effective_region_polygon(
                [wedge], checked_tol
            )
            continue

        polygon = clip_polygon_with_wedge(
            previous_polygon, wedge, checked_tol
        )
        previous_polygon = polygon
        unbounded_direction = polygon.infinite_direction
        snapshot = RegionSnapshot(
            added_index=wedge.index,
            selected_indices=tuple(selected_indices),
            wedges=tuple(all_wedges[:position]),
            unbounded_direction=unbounded_direction,
            is_closed=polygon.is_closed,
            polygon=polygon,
        )
        snapshots.append(snapshot)
        if polygon.is_closed:
            remaining_indices = tuple(
                item.index for item in all_wedges[position:]
            )
            return IterativeBoundedResult(
                selected_indices=tuple(selected_indices),
                remaining_indices=remaining_indices,
                snapshots=tuple(snapshots),
                final_state=snapshot,
                bounded=True,
            )

    if not snapshots:
        raise RuntimeError("迭代奠基步骤未生成任何区域快照")
    final_state = snapshots[-1]
    return IterativeBoundedResult(
        selected_indices=tuple(selected_indices),
        remaining_indices=(),
        snapshots=tuple(snapshots),
        final_state=final_state,
        bounded=False,
    )


__all__ = list(
    dict.fromkeys(
        [
            *__all__,
            "DEFAULT_TOLERANCE",
            "GEOMETRY_TOLERANCE",
            "EffectiveRegionError",
            "EmptyEffectiveRegionError",
            "DegenerateEffectiveRegionError",
            "LinkedListInvariantError",
            "cross_2d",
            "ObservationWedge",
            "make_observation_wedge",
            "find_unbounded_direction",
            "is_intersection_bounded",
            "RegionNode",
            "RegionPolygon",
            "build_effective_region_polygon",
            "clip_polygon_with_wedge",
            "update_effective_region_in_place",
            "continue_effective_region_iteration",
            "iterate_effective_region",
            "InfiniteNode",
            "RegionSnapshot",
            "IterativeBoundedResult",
            "iterative_select_until_bounded",
        ]
    )
)
