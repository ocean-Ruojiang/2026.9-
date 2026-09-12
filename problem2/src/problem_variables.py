"""全国大学生数学建模竞赛问题变量准备模块。

本模块集中保存观测点数量、观测点坐标、目标多边形双向链表和观测方向。
所有公开接口仅依赖 Python 标准库与 NumPy，不包含任何具体赛题数据。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from numbers import Real
from typing import Final

import numpy as np

__all__ = [
    "PolygonNode",
    "DoublyLinkedList",
    "num_measure",
    "position",
    "goal_position",
    "direction",
    "set_problem_variables",
    "validate_problem_variables",
    "clear_problem_variables",
    "get_problem_variables",
]


class PolygonNode:
    """双向链表中的多边形顶点节点。

    Parameters
    ----------
    point:
        长度为 2 的一维浮点 NumPy 数组。
    prev:
        前驱节点；未连接时为 ``None``。
    next:
        后继节点；未连接时为 ``None``。
    """

    __slots__ = ("point", "prev", "next")

    def __init__(
        self,
        point: np.ndarray,
        prev: PolygonNode | None = None,
        next: PolygonNode | None = None,
    ) -> None:
        self.point = _coerce_point(point, "point")
        self.prev = prev
        self.next = next


class DoublyLinkedList:
    """由二维坐标构成的双向链表，可选首尾闭合。

    Parameters
    ----------
    coordinates:
        二维坐标序列；每个坐标应可转换为长度为 2 的浮点数组。
    closed:
        是否令首节点与尾节点互相连接。默认为 ``True``。

    Examples
    --------
    >>> polygon = DoublyLinkedList.from_coordinates([[0, 0], [1, 0], [0, 1]])
    >>> len(polygon)
    3
    """

    __slots__ = ("head", "tail", "size", "closed")

    def __init__(
        self,
        coordinates: Iterable[object] | None = None,
        *,
        closed: bool = True,
    ) -> None:
        if not isinstance(closed, bool):
            raise TypeError("closed 必须是 bool")

        self.head: PolygonNode | None = None
        self.tail: PolygonNode | None = None
        self.size: int = 0
        self.closed: bool = closed

        if coordinates is not None:
            points = _coerce_coordinate_sequence(coordinates)
            self._build_from_points(points)

    @classmethod
    def from_coordinates(
        cls,
        coordinates: Iterable[object],
        *,
        closed: bool = True,
    ) -> DoublyLinkedList:
        """由二维坐标序列构建双向链表，并自动按 ``closed`` 首尾连接。"""

        return cls(coordinates, closed=closed)

    def _build_from_points(self, points: list[np.ndarray]) -> None:
        """使用已经校验的坐标点重建链表。"""

        self.head = None
        self.tail = None
        self.size = 0

        if not points:
            return

        first = PolygonNode(points[0])
        previous = first
        for point in points[1:]:
            current = PolygonNode(point)
            previous.next = current
            current.prev = previous
            previous = current

        self.head = first
        self.tail = previous
        self.size = len(points)

        if self.closed:
            self.tail.next = self.head
            self.head.prev = self.tail

    def iter_nodes(self) -> Iterator[PolygonNode]:
        """按链表顺序迭代节点。调用前应保证链表结构有效。"""

        current = self.head
        for _ in range(self.size):
            if current is None:
                raise RuntimeError("链表结构损坏：节点数不足")
            yield current
            current = current.next

    def to_coordinates(self, *, copy: bool = True) -> list[np.ndarray]:
        """返回按链表顺序排列的坐标列表。"""

        return [
            node.point.copy() if copy else node.point
            for node in self.iter_nodes()
        ]

    def copy(self) -> DoublyLinkedList:
        """返回当前链表的独立副本。"""

        return DoublyLinkedList.from_coordinates(
            self.to_coordinates(copy=True),
            closed=self.closed,
        )

    def is_empty(self) -> bool:
        """判断链表是否为空。"""

        return self.size == 0

    def is_closed(self) -> bool:
        """判断链表是否设置为首尾闭合。"""

        return self.closed

    def __len__(self) -> int:
        """返回链表中的节点数量。"""

        return self.size


def _coerce_point(point: object, name: str = "坐标") -> np.ndarray:
    """把单个坐标转换成 float64 一维数组。"""

    if isinstance(point, (str, bytes)):
        raise TypeError(f"{name} 不能是字符串或字节串")

    try:
        array = np.asarray(point, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} 必须可转换为浮点数组") from exc

    if array.ndim != 1 or array.shape != (2,):
        raise ValueError(f"{name} 必须是一维且长度为 2 的数组")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} 的所有分量必须是有限数值")

    return np.array(array, dtype=np.float64, copy=True)


def _coerce_coordinate_sequence(
    coordinates: Iterable[object],
) -> list[np.ndarray]:
    """校验并复制二维坐标序列。"""

    if isinstance(coordinates, (str, bytes)):
        raise TypeError("coordinates 不能是字符串或字节串")

    try:
        raw_points = list(coordinates)
    except TypeError as exc:
        raise TypeError("coordinates 必须是可迭代的二维坐标序列") from exc

    return [
        _coerce_point(point, f"coordinates[{index}]")
        for index, point in enumerate(raw_points)
    ]


def _validate_num_measure(value: object) -> int:
    """校验观测点数量。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("num_measure 必须是 int")
    if value < 0:
        raise ValueError("num_measure 不能为负数")
    return value


def _validate_position(
    value: object,
    expected_length: int,
) -> list[np.ndarray]:
    """严格校验观测点坐标列表并返回 float64 副本。"""

    if not isinstance(value, list):
        raise TypeError("position 必须是 list[np.ndarray]")
    if len(value) != expected_length:
        raise ValueError(
            f"position 长度必须等于 num_measure：期望 {expected_length}，实际 {len(value)}"
        )

    result: list[np.ndarray] = []
    for index, point in enumerate(value):
        if not isinstance(point, np.ndarray):
            raise TypeError(f"position[{index}] 必须是 numpy.ndarray")
        if point.ndim != 1 or point.shape != (2,):
            raise ValueError(f"position[{index}] 必须是一维且长度为 2 的数组")
        if not np.issubdtype(point.dtype, np.floating):
            raise TypeError(f"position[{index}] 的 dtype 必须是浮点类型")
        if not np.all(np.isfinite(point)):
            raise ValueError(f"position[{index}] 的所有分量必须是有限数值")
        result.append(np.array(point, dtype=np.float64, copy=True))

    return result


def _validate_direction(
    value: object,
    expected_length: int,
) -> list[float]:
    """严格校验方向列表并返回 float 副本。"""

    if not isinstance(value, list):
        raise TypeError("direction 必须是 list[float]")
    if len(value) != expected_length:
        raise ValueError(
            f"direction 长度必须等于 num_measure：期望 {expected_length}，实际 {len(value)}"
        )

    result: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, Real):
            raise TypeError(f"direction[{index}] 必须是实数")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"direction[{index}] 必须是有限数值")
        result.append(number)

    return result


def _validate_linked_list(value: object) -> DoublyLinkedList:
    """严格校验双向链表的结构、闭环状态和节点坐标。"""

    if not isinstance(value, DoublyLinkedList):
        raise TypeError("goal_position 必须是 DoublyLinkedList")

    if not isinstance(value.closed, bool):
        raise TypeError("goal_position.closed 必须是 bool")
    if not isinstance(value.size, int) or isinstance(value.size, bool):
        raise TypeError("goal_position.size 必须是 int")
    if value.size < 0:
        raise ValueError("goal_position.size 不能为负数")

    if value.size == 0:
        if value.head is not None or value.tail is not None:
            raise ValueError("空链表的 head 和 tail 必须均为 None")
        return value

    if not isinstance(value.head, PolygonNode) or not isinstance(value.tail, PolygonNode):
        raise TypeError("非空链表的 head 和 tail 必须是 PolygonNode")

    nodes: list[PolygonNode] = [value.head]
    seen: set[int] = {id(value.head)}
    current = value.head

    while current.next is not None:
        following = current.next
        if not isinstance(following, PolygonNode):
            raise TypeError("next 必须指向 PolygonNode 或 None")

        if following is value.head:
            if not value.closed:
                raise ValueError("未闭合链表不能形成循环")
            break
        if id(following) in seen:
            raise ValueError("链表存在非首尾的重复节点或异常环")
        if following.prev is not current:
            raise ValueError("相邻节点的 prev/next 关系不一致")

        seen.add(id(following))
        nodes.append(following)
        current = following

        if len(nodes) > value.size:
            raise ValueError("链表实际节点数超过 size")

    if len(nodes) != value.size:
        raise ValueError(
            f"链表 size 与实际可达节点数不一致：{value.size} != {len(nodes)}"
        )
    if current is not value.tail:
        raise ValueError("链表 tail 不是沿 next 方向可达的尾节点")

    if value.closed:
        if value.tail.next is not value.head:
            raise ValueError("闭合链表必须满足 tail.next is head")
        if value.head.prev is not value.tail:
            raise ValueError("闭合链表必须满足 head.prev is tail")
        if value.size == 1 and value.head.next is not value.head:
            raise ValueError("单节点闭合链表必须自连接")
    else:
        if value.head.prev is not None:
            raise ValueError("未闭合链表的 head.prev 必须为 None")
        if value.tail.next is not None:
            raise ValueError("未闭合链表的 tail.next 必须为 None")

    for index, node in enumerate(nodes):
        _coerce_point(node.point, f"goal_position 节点[{index}].point")

    return value


def set_problem_variables(
    num_measure: int,
    position: list[np.ndarray],
    goal_position: DoublyLinkedList,
    direction: list[float],
) -> None:
    """初始化并严格校验模块级问题变量。

    校验通过前不会修改模块级变量；校验通过后，坐标和方向均保存独立副本。

    Raises
    ------
    TypeError
        参数类型不符合接口约束。
    ValueError
        长度、数值或链表结构不符合约束。
    """

    checked_num_measure = _validate_num_measure(num_measure)
    checked_position = _validate_position(position, checked_num_measure)
    checked_goal_position = _validate_linked_list(goal_position)
    checked_direction = _validate_direction(direction, checked_num_measure)

    globals()["num_measure"] = checked_num_measure
    globals()["position"] = checked_position
    globals()["goal_position"] = checked_goal_position.copy()
    globals()["direction"] = checked_direction


def validate_problem_variables() -> bool:
    """校验当前模块级问题变量；合法时返回 ``True``，否则抛出异常。"""

    checked_num_measure = _validate_num_measure(globals()["num_measure"])
    _validate_position(globals()["position"], checked_num_measure)
    _validate_linked_list(globals()["goal_position"])
    _validate_direction(globals()["direction"], checked_num_measure)
    return True


def clear_problem_variables() -> None:
    """将模块级问题变量恢复为空状态。"""

    globals()["num_measure"] = 0
    globals()["position"] = []
    globals()["goal_position"] = DoublyLinkedList(closed=True)
    globals()["direction"] = []


def get_problem_variables(
    *,
    copy: bool = True,
) -> tuple[int, list[np.ndarray], DoublyLinkedList, list[float]]:
    """读取当前问题变量。

    Parameters
    ----------
    copy:
        为 ``True`` 时返回独立副本，避免调用方意外修改模块状态；为 ``False``
        时直接返回当前对象，适合只读计算以减少复制开销。
    """

    if not isinstance(copy, bool):
        raise TypeError("copy 必须是 bool")

    current_num_measure = globals()["num_measure"]
    current_position = globals()["position"]
    current_goal_position = globals()["goal_position"]
    current_direction = globals()["direction"]

    if not copy:
        return (
            current_num_measure,
            current_position,
            current_goal_position,
            current_direction,
        )

    return (
        current_num_measure,
        [point.copy() for point in current_position],
        current_goal_position.copy(),
        list(current_direction),
    )


_DEFAULT_GOAL_POSITION: Final[DoublyLinkedList] = DoublyLinkedList(closed=True)
num_measure: int = 0
position: list[np.ndarray] = []
goal_position: DoublyLinkedList = _DEFAULT_GOAL_POSITION
direction: list[float] = []
