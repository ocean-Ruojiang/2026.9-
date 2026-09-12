"""本地干扰源模拟器核心。

本模块实现附件规定的 4 个 HTTP 接口、虚拟时间、移动耗时、频道切换、
检测、清除、幂等 request_id 和基本请求校验。GUI 只负责编辑参数和展示状态。

默认监听 ``127.0.0.1:2026``，接口：
- POST /enter
- POST /measure
- POST /clear
- POST /exit
"""

from __future__ import annotations

import copy
import json
import math
import random
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable, Mapping

DEFAULT_PORT = 2026
ARENA_ID = "default"
CHANNEL_MIN = 1
CHANNEL_MAX = 20
TARGET_RADIUS_M = 1800.0
MOBILE_SPEED_MPS = 5.0
MEASURE_DURATION_S = 5.0
CHANNEL_SWITCH_DURATION_S = 1.0
CLEAR_PROBE_DURATION_S = 3.0
CLEAR_SUCCESS_DURATION_S = 5.0
NEAR_DISTANCE_M = 5.0
CLEAR_RADIUS_M = 20.0
DIRECTIONAL_COVERAGE_DEG = 180.0
SVD_ERROR_DEG = 1.0
MAX_VIRTUAL_DURATION_S = 360000.0
MAX_REAL_DURATION_S = 1200.0
MAX_REQUEST_BYTES = 65536
PROBLEM3_MIN_SOURCES = 10
PROBLEM3_MAX_SOURCES = 16
RECEIVE_RADIUS_MIN_M = 1000.0
RECEIVE_RADIUS_MAX_M = 1500.0


class SimulatorError(ValueError):
    """模拟器配置或请求错误。"""


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise SimulatorError(f"{name} 必须是有限数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SimulatorError(f"{name} 必须是有限数值") from exc
    if not math.isfinite(result):
        raise SimulatorError(f"{name} 必须是有限数值")
    return result


def _validate_identifier(value: object, name: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise SimulatorError(f"{name} 必须是非空字符串")
    encoded = value.encode("utf-8")
    if len(encoded) > max_bytes:
        raise SimulatorError(f"{name} 的 UTF-8 长度不能超过 {max_bytes} 字节")
    for char in value:
        if unicodedata.category(char) in {"Cc", "Cf"}:
            raise SimulatorError(f"{name} 不能包含控制字符或不可见格式字符")
    return value


def _validate_channel(value: object) -> int:
    if isinstance(value, bool):
        raise SimulatorError("channel 必须是 1..20 的整数")
    if isinstance(value, int):
        channel = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        channel = int(value)
    else:
        raise SimulatorError("channel 必须是 1..20 的整数")
    if not CHANNEL_MIN <= channel <= CHANNEL_MAX:
        raise SimulatorError("channel 必须是 1..20 的整数")
    return channel


def _validate_position(value: object) -> tuple[float, float]:
    if not isinstance(value, Mapping):
        raise SimulatorError("position 必须是包含 x、y 的对象")
    if set(value) != {"x", "y"}:
        raise SimulatorError("position 必须且只能包含 x、y")
    x = _finite_float(value["x"], "position.x")
    y = _finite_float(value["y"], "position.y")
    if abs(x) > 2_000_000.0 or abs(y) > 2_000_000.0:
        raise SimulatorError("position.x 和 position.y 的绝对值不能超过 2000000")
    return x, y


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _bearing_deg(
    origin: tuple[float, float], target: tuple[float, float]
) -> float:
    return math.degrees(math.atan2(target[1] - origin[1], target[0] - origin[0])) % 360.0


def _angle_difference(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


@dataclass
class InterferenceSource:
    """一个可配置干扰源。"""

    channel: int
    x: float
    y: float
    kind: str = "omni"
    receive_radius_m: float = 1200.0
    direction_deg: float = 0.0
    cleared: bool = False

    def __post_init__(self) -> None:
        self.channel = _validate_channel(self.channel)
        self.x = _finite_float(self.x, "x")
        self.y = _finite_float(self.y, "y")
        if self.kind not in {"omni", "directional"}:
            raise SimulatorError("kind 必须是 omni 或 directional")
        self.receive_radius_m = _finite_float(
            self.receive_radius_m, "receive_radius_m"
        )
        if not 0.0 < self.receive_radius_m <= 10_000.0:
            raise SimulatorError("receive_radius_m 必须大于 0 且不超过 10000")
        self.direction_deg = _finite_float(self.direction_deg, "direction_deg") % 360.0
        self.cleared = bool(self.cleared)

    @property
    def position(self) -> tuple[float, float]:
        return (self.x, self.y)

    def clone(self) -> InterferenceSource:
        return InterferenceSource(
            self.channel,
            self.x,
            self.y,
            self.kind,
            self.receive_radius_m,
            self.direction_deg,
            self.cleared,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "x": self.x,
            "y": self.y,
            "kind": self.kind,
            "receive_radius_m": self.receive_radius_m,
            "direction_deg": self.direction_deg,
            "cleared": self.cleared,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> InterferenceSource:
        return cls(
            channel=int(value.get("channel", 1)),
            x=float(value.get("x", 0.0)),
            y=float(value.get("y", 0.0)),
            kind=str(value.get("kind", "omni")),
            receive_radius_m=float(value.get("receive_radius_m", 1200.0)),
            direction_deg=float(value.get("direction_deg", 0.0)),
            cleared=bool(value.get("cleared", False)),
        )


@dataclass
class SimulatorConfig:
    """全局可调参数。"""

    speed_mps: float = MOBILE_SPEED_MPS
    near_distance_m: float = NEAR_DISTANCE_M
    clear_radius_m: float = CLEAR_RADIUS_M
    coverage_angle_deg: float = DIRECTIONAL_COVERAGE_DEG
    svd_error_deg: float = SVD_ERROR_DEG
    max_virtual_duration_s: float = MAX_VIRTUAL_DURATION_S
    max_real_duration_s: float = MAX_REAL_DURATION_S
    target_radius_m: float = TARGET_RADIUS_M
    expected_robot_id: str | None = None
    random_seed: int | None = None
    measurement_error_mode: str = "fixed_location"
    problem_mode: str = "custom"
    debug_expose_sources: bool = False

    def __post_init__(self) -> None:
        self.speed_mps = _finite_float(self.speed_mps, "speed_mps")
        self.near_distance_m = _finite_float(
            self.near_distance_m, "near_distance_m"
        )
        self.clear_radius_m = _finite_float(
            self.clear_radius_m, "clear_radius_m"
        )
        self.coverage_angle_deg = _finite_float(
            self.coverage_angle_deg, "coverage_angle_deg"
        )
        self.svd_error_deg = _finite_float(self.svd_error_deg, "svd_error_deg")
        self.max_virtual_duration_s = _finite_float(
            self.max_virtual_duration_s, "max_virtual_duration_s"
        )
        self.max_real_duration_s = _finite_float(
            self.max_real_duration_s, "max_real_duration_s"
        )
        self.target_radius_m = _finite_float(
            self.target_radius_m, "target_radius_m"
        )
        if self.speed_mps <= 0.0:
            raise SimulatorError("speed_mps 必须大于 0")
        if self.near_distance_m < 0.0:
            raise SimulatorError("near_distance_m 不能为负")
        if self.clear_radius_m < 0.0:
            raise SimulatorError("clear_radius_m 不能为负")
        if not 0.0 < self.coverage_angle_deg <= 360.0:
            raise SimulatorError("coverage_angle_deg 必须在 (0, 360] 内")
        if self.svd_error_deg < 0.0:
            raise SimulatorError("svd_error_deg 不能为负")
        if self.max_virtual_duration_s <= 0.0 or self.max_real_duration_s <= 0.0:
            raise SimulatorError("时间上限必须大于 0")
        if self.expected_robot_id is not None:
            self.expected_robot_id = _validate_identifier(
                self.expected_robot_id, "expected_robot_id", 64
            )
        if self.random_seed is not None:
            self.random_seed = int(self.random_seed)
        if self.measurement_error_mode not in {"fixed_location", "independent"}:
            raise SimulatorError("measurement_error_mode must be fixed_location or independent")
        if self.problem_mode not in {"custom", "problem3", "problem4"}:
            raise SimulatorError("problem_mode must be custom, problem3, or problem4")
        self.debug_expose_sources = bool(self.debug_expose_sources)


@dataclass(frozen=True)
class SimulatorStatus:
    active: bool
    entered: bool
    ended: bool
    ended_reason: str | None
    virtual_time_s: float
    current_position: tuple[float, float] | None
    current_channel: int
    remaining_real_duration_s: float
    request_count: int


class SimulatorState:
    """线程安全的一次测试会话。"""

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        sources: Iterable[InterferenceSource] | None = None,
        event_sink: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config or SimulatorConfig()
        self.event_sink = event_sink
        self._lock = threading.RLock()
        self._action_lock = threading.Lock()
        self._sources: dict[int, InterferenceSource] = {}
        self._request_records: dict[str, tuple[str, str, int, dict[str, object]]] = {}
        self._behavior_history: list[dict[str, object]] = []
        self._direction_error_cache: dict[tuple[int, float, float], float] = {}
        self._random = random.Random(self.config.random_seed)
        self.active = False
        self.entered = False
        self.ended = False
        self.ended_reason: str | None = None
        self.virtual_time_s = 0.0
        self.current_position: tuple[float, float] | None = None
        self.current_channel = 1
        self._enter_monotonic: float | None = None
        self._set_sources(sources or [])

    def _log(self, kind: str, message: str) -> None:
        if self.event_sink is not None:
            try:
                self.event_sink(kind, message)
            except Exception:
                pass

    def _set_sources(self, sources: Iterable[InterferenceSource]) -> None:
        checked: dict[int, InterferenceSource] = {}
        for source in sources:
            item = source.clone() if isinstance(source, InterferenceSource) else InterferenceSource.from_dict(source)
            if item.channel in checked:
                raise SimulatorError(f"频道 {item.channel} 配置了多个干扰源")
            checked[item.channel] = item
        if self.config.problem_mode == "problem3":
            if not (PROBLEM3_MIN_SOURCES <= len(checked) <= PROBLEM3_MAX_SOURCES):
                raise SimulatorError(
                    f"problem3 requires {PROBLEM3_MIN_SOURCES}..{PROBLEM3_MAX_SOURCES} sources"
                )
            for item in checked.values():
                if item.kind != "omni":
                    raise SimulatorError("problem3 requires all sources to be omni")
                if math.hypot(item.x, item.y) > self.config.target_radius_m + 1e-9:
                    raise SimulatorError("problem3 source lies outside target radius")
                if not (RECEIVE_RADIUS_MIN_M <= item.receive_radius_m <= RECEIVE_RADIUS_MAX_M):
                    raise SimulatorError(
                        f"problem3 receive radius must be {RECEIVE_RADIUS_MIN_M:g}..{RECEIVE_RADIUS_MAX_M:g}"
                    )
        self._sources = checked

    def replace_sources(self, sources: Iterable[InterferenceSource]) -> None:
        with self._lock:
            self._set_sources(sources)
            self._log("system", "干扰源配置已更新；后续检测和清除使用新配置")

    def sources(self) -> tuple[InterferenceSource, ...]:
        if not self.config.debug_expose_sources:
            raise SimulatorError(
                "ground-truth source access is disabled; enable config.debug_expose_sources only for evaluation/debug"
            )
        with self._lock:
            return tuple(item.clone() for item in sorted(self._sources.values(), key=lambda s: s.channel))

    def behavior_history(self) -> tuple[dict[str, object], ...]:
        """返回本局已经执行的位置动作历史，供 GUI 绘制轨迹。"""

        with self._lock:
            return tuple(copy.deepcopy(item) for item in self._behavior_history)

    def _record_behavior(
        self,
        action: str,
        position: tuple[float, float] | None,
        channel: int | None,
        result: str,
        **extra: object,
    ) -> None:
        event: dict[str, object] = {
            "step": len(self._behavior_history) + 1,
            "action": action,
            "result": result,
            "position": None if position is None else {"x": position[0], "y": position[1]},
            "channel": channel,
            "virtual_time_s": self.virtual_time_s,
        }
        event.update(extra)
        self._behavior_history.append(event)

    def status(self) -> SimulatorStatus:
        with self._lock:
            return SimulatorStatus(
                active=self.active,
                entered=self.entered,
                ended=self.ended,
                ended_reason=self.ended_reason,
                virtual_time_s=self.virtual_time_s,
                current_position=self.current_position,
                current_channel=self.current_channel,
                remaining_real_duration_s=self._remaining_real_duration(),
                request_count=len(self._request_records),
            )

    def start_session(self) -> None:
        with self._lock:
            self.active = True
            self.entered = False
            self.ended = False
            self.ended_reason = None
            self.virtual_time_s = 0.0
            self.current_position = None
            self.current_channel = 1
            self._enter_monotonic = None
            self._request_records.clear()
            self._behavior_history.clear()
            self._direction_error_cache.clear()
            self._random = random.Random(self.config.random_seed)
            for source in self._sources.values():
                source.cleared = False
            self._log("system", f"测试会话已启动，共 {len(self._sources)} 个干扰源")

    def stop_session(self, reason: str = "manual_stop") -> None:
        with self._lock:
            if self.active:
                self.ended_reason = reason
            self.active = False
            self.entered = False
            self.ended = True
            self._log("system", f"测试会话结束：{reason}")

    def _remaining_real_duration(self) -> float:
        if not self.active:
            return 0.0
        if self._enter_monotonic is None:
            return float(self.config.max_real_duration_s)
        elapsed = time.monotonic() - self._enter_monotonic
        return max(0.0, float(self.config.max_real_duration_s) - elapsed)

    def _response(
        self,
        accepted: bool,
        *,
        virtual_time_s: float | None = None,
        **extra: object,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "accepted": bool(accepted),
            "real_timestamp_ms": int(time.time() * 1000),
            "virtual_time_s": (
                float(virtual_time_s)
                if virtual_time_s is not None
                else (self.virtual_time_s if accepted else 0.0)
            ),
        }
        payload.update(extra)
        return payload

    def _fail(self, message: str, status: int = HTTPStatus.OK) -> tuple[int, dict[str, object]]:
        self._log("reject", message)
        return status, self._response(False)

    @staticmethod
    def _canonical(payload: Mapping[str, object]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _common_validate(
        self,
        payload: object,
        *,
        allowed_fields: set[str],
    ) -> tuple[str, str, str] | tuple[int, dict[str, object]]:
        if not isinstance(payload, Mapping):
            return 400, self._response(False)
        allowed = {"arena_id", "robot_id", "request_id"}
        if set(payload) - allowed_fields:
            return 200, self._response(False)
        missing = allowed - set(payload)
        if missing:
            return 400, self._response(False)
        try:
            arena_id = payload["arena_id"]
            robot_id = _validate_identifier(payload["robot_id"], "robot_id", 64)
            request_id = _validate_identifier(payload["request_id"], "request_id", 128)
        except SimulatorError:
            return 400, self._response(False)
        if arena_id != ARENA_ID:
            return 200, self._response(False)
        if self.config.expected_robot_id is not None and robot_id != self.config.expected_robot_id:
            return 200, self._response(False)
        return robot_id, request_id, self._canonical(payload)

    def handle(self, path: str, payload: object) -> tuple[int, dict[str, object]]:
        allowed_by_path = {
            "/enter": {"arena_id", "robot_id", "request_id"},
            "/measure": {"arena_id", "robot_id", "request_id", "position", "channel"},
            "/clear": {"arena_id", "robot_id", "request_id", "position", "channel"},
            "/exit": {"arena_id", "robot_id", "request_id"},
        }
        if path not in allowed_by_path:
            return 404, self._response(False)
        validated = self._common_validate(payload, allowed_fields=allowed_by_path[path])
        if not isinstance(validated[0], str):
            return validated  # type: ignore[return-value]
        _robot_id, request_id, canonical = validated

        if not self._action_lock.acquire(blocking=False):
            return 409, self._response(False)
        try:
            record = self._request_records.get(request_id)
            if record is not None:
                old_path, old_canonical, status, response = record
                if old_path == path and old_canonical == canonical:
                    return status, copy.deepcopy(response)
                return 409, self._response(False)

            if path == "/enter":
                status, response = self._handle_enter(payload)
            elif path == "/measure":
                status, response = self._handle_measure(payload)
            elif path == "/clear":
                status, response = self._handle_clear(payload)
            else:
                status, response = self._handle_exit(payload)

            if status == HTTPStatus.OK and response.get("accepted") is True:
                self._request_records[request_id] = (
                    path,
                    canonical,
                    int(status),
                    copy.deepcopy(response),
                )
            return int(status), response
        finally:
            self._action_lock.release()

    def _check_active(self) -> tuple[int, dict[str, object]] | None:
        if not self.active or self.ended:
            return 200, self._response(False)
        if self._remaining_real_duration() <= 0.0:
            self.stop_session("real_timeout")
            return 200, self._response(False)
        return None

    def _handle_enter(self, payload: Mapping[str, object]) -> tuple[int, dict[str, object]]:
        with self._lock:
            failure = self._check_active()
            if failure is not None:
                return failure
            if self.entered:
                return 200, self._response(False)
            self.entered = True
            self.current_position = (0.0, 0.0)
            self.current_channel = 1
            self._enter_monotonic = time.monotonic()
            self._log("enter", "机器狗进入，位置=(0, 0)，频道=1")
            self._record_behavior("enter", (0.0, 0.0), 1, "entered")
            return 200, self._response(
                True,
                virtual_time_s=0.0,
                max_virtual_duration_s=float(self.config.max_virtual_duration_s),
                max_real_duration_s=float(self.config.max_real_duration_s),
                remaining_real_duration_s=float(self._remaining_real_duration()),
            )

    def _handle_measure(self, payload: Mapping[str, object]) -> tuple[int, dict[str, object]]:
        with self._lock:
            failure = self._check_active()
            if failure is not None:
                return failure
            if not self.entered:
                return 200, self._response(False)
            position_payload = payload.get("position")
            if (
                isinstance(position_payload, Mapping)
                and {"x", "y"}.issubset(position_payload)
                and set(position_payload) - {"x", "y"}
            ):
                return 200, self._response(False)
            try:
                position = _validate_position(payload["position"])
                channel = _validate_channel(payload["channel"])
            except (KeyError, SimulatorError):
                return 400, self._response(False)
            move_s = 0.0
            if self.current_position is not None:
                move_s = _distance(self.current_position, position) / self.config.speed_mps
            switch_s = 0.0 if channel == self.current_channel else 1.0
            total_s = move_s + switch_s + 5.0
            self.virtual_time_s += total_s
            self.current_position = position
            self.current_channel = channel

            source = self._sources.get(channel)
            result: str
            if source is None or source.cleared:
                result = "no_signal"
            else:
                distance = _distance(position, source.position)
                in_range = distance <= source.receive_radius_m
                in_coverage = self._point_in_source_coverage(position, source)
                if not in_range or not in_coverage:
                    result = "no_signal"
                elif distance <= self.config.near_distance_m:
                    result = "near"
                else:
                    result = "direction"
            response = self._response(
                True,
                virtual_time_s=self.virtual_time_s,
                remaining_real_duration_s=float(self._remaining_real_duration()),
                measure_result=result,
            )
            if result == "direction" and source is not None:
                true_bearing = _bearing_deg(position, source.position)
                error = self._direction_error_deg(channel, position)
                response["svd_deg"] = round((true_bearing + error) % 360.0, 2)
            self._record_behavior(
                "measure",
                position,
                channel,
                result,
                svd_deg=response.get("svd_deg"),
            )
            self._log(
                "measure",
                f"位置=({position[0]:g}, {position[1]:g}) 频道={channel} 结果={result} 虚拟时间={self.virtual_time_s:g}s",
            )
            if self.virtual_time_s >= self.config.max_virtual_duration_s:
                self.stop_session("virtual_timeout")
            return 200, response

    def _direction_error_deg(
        self,
        channel: int,
        position: tuple[float, float],
    ) -> float:
        """Return the measurement error for a channel/location.

        The official rule fixes the electromagnetic error at the same location,
        so the default mode caches one error per rounded (channel, x, y) key.
        The independent mode is retained as a legacy/diagnostic switch.
        """

        if self.config.measurement_error_mode == "independent":
            return self._random.uniform(-self.config.svd_error_deg, self.config.svd_error_deg)
        key = (int(channel), round(float(position[0]), 6), round(float(position[1]), 6))
        if key not in self._direction_error_cache:
            self._direction_error_cache[key] = self._random.uniform(
                -self.config.svd_error_deg,
                self.config.svd_error_deg,
            )
        return self._direction_error_cache[key]

    def _point_in_source_coverage(
        self,
        point: tuple[float, float],
        source: InterferenceSource,
    ) -> bool:
        if source.kind == "omni":
            return True
        bearing = _bearing_deg(source.position, point)
        return _angle_difference(bearing, source.direction_deg) <= self.config.coverage_angle_deg / 2.0

    def _handle_clear(self, payload: Mapping[str, object]) -> tuple[int, dict[str, object]]:
        with self._lock:
            failure = self._check_active()
            if failure is not None:
                return failure
            if not self.entered:
                return 200, self._response(False)
            position_payload = payload.get("position")
            if (
                isinstance(position_payload, Mapping)
                and {"x", "y"}.issubset(position_payload)
                and set(position_payload) - {"x", "y"}
            ):
                return 200, self._response(False)
            try:
                position = _validate_position(payload["position"])
                channel = _validate_channel(payload["channel"])
            except (KeyError, SimulatorError):
                return 400, self._response(False)
            move_s = 0.0
            if self.current_position is not None:
                move_s = _distance(self.current_position, position) / self.config.speed_mps
            self.current_position = position
            source = self._sources.get(channel)
            can_clear = (
                source is not None
                and not source.cleared
                and _distance(position, source.position) <= self.config.clear_radius_m
            )
            action_s = 5.0 if can_clear else 3.0
            self.virtual_time_s += move_s + action_s
            if can_clear and source is not None:
                source.cleared = True
                result = "success"
            else:
                result = "no_target_in_range"
            response = self._response(
                True,
                virtual_time_s=self.virtual_time_s,
                remaining_real_duration_s=float(self._remaining_real_duration()),
                clear_result=result,
            )
            self._record_behavior("clear", position, channel, result)
            self._log(
                "clear",
                f"位置=({position[0]:g}, {position[1]:g}) 频道={channel} 结果={result} 虚拟时间={self.virtual_time_s:g}s",
            )
            if self.virtual_time_s >= self.config.max_virtual_duration_s:
                self.stop_session("virtual_timeout")
            return 200, response

    def _handle_exit(self, payload: Mapping[str, object]) -> tuple[int, dict[str, object]]:
        with self._lock:
            failure = self._check_active()
            if failure is not None:
                return failure
            if not self.entered:
                return 200, self._response(False)
            response = self._response(True, virtual_time_s=self.virtual_time_s, exit_reason="user_exit")
            self._log("exit", f"机器狗主动退出，虚拟时间={self.virtual_time_s:g}s")
            self.stop_session("user_exit")
            return 200, response


class _RequestHandler(BaseHTTPRequestHandler):
    state: SimulatorState

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/enter", "/measure", "/clear", "/exit"}:
            self._send_json(404, {"accepted": False, "real_timestamp_ms": int(time.time() * 1000), "virtual_time_s": 0.0})
            return
        content_type = self.headers.get("Content-Type", "")
        if not re.fullmatch(r"application/json(?:\s*;\s*charset=utf-8)?", content_type, re.IGNORECASE):
            self._send_json(415, {"accepted": False, "real_timestamp_ms": int(time.time() * 1000), "virtual_time_s": 0.0})
            return
        content_encoding = self.headers.get("Content-Encoding", "identity")
        if content_encoding.lower() != "identity":
            self._send_json(415, {"accepted": False, "real_timestamp_ms": int(time.time() * 1000), "virtual_time_s": 0.0})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_json(413 if length > MAX_REQUEST_BYTES else 400, {"accepted": False, "real_timestamp_ms": int(time.time() * 1000), "virtual_time_s": 0.0})
            return
        body = self.rfile.read(length)
        if body.startswith(b"\xef\xbb\xbf"):
            self._send_json(400, {"accepted": False, "real_timestamp_ms": int(time.time() * 1000), "virtual_time_s": 0.0})
            return
        try:
            text = body.decode("utf-8")
            payload = json.loads(text, object_pairs_hook=self._no_duplicates)
        except (UnicodeDecodeError, ValueError):
            self._send_json(400, {"accepted": False, "real_timestamp_ms": int(time.time() * 1000), "virtual_time_s": 0.0})
            return
        status, response = self.state.handle(self.path, payload)
        self._send_json(status, response)

    @staticmethod
    def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SimulatorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(
    host: str,
    port: int,
    state: SimulatorState,
) -> SimulatorHTTPServer:
    """创建绑定到指定地址的 HTTP 服务器。调用方负责 shutdown/server_close。"""

    class Handler(_RequestHandler):
        pass

    Handler.state = state
    return SimulatorHTTPServer((host, int(port)), Handler)


def load_sources_json(path: str) -> list[InterferenceSource]:
    """从 JSON 文件读取干扰源列表。"""

    import pathlib

    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise SimulatorError("JSON 顶层必须是干扰源数组")
    return [InterferenceSource.from_dict(item) for item in payload]


def save_sources_json(path: str, sources: Iterable[InterferenceSource]) -> None:
    """保存干扰源配置到 JSON 文件。"""

    import pathlib

    payload = [source.clone().to_dict() for source in sources]
    pathlib.Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "DEFAULT_PORT",
    "ARENA_ID",
    "TARGET_RADIUS_M",
    "SimulatorError",
    "InterferenceSource",
    "SimulatorConfig",
    "SimulatorStatus",
    "SimulatorState",
    "SimulatorHTTPServer",
    "make_server",
    "load_sources_json",
    "save_sources_json",
]
