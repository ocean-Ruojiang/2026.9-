from dataclasses import dataclass, field
from enum import Enum
import numpy as np

Point = tuple[float, float]


class Status(str, Enum):
    UNKNOWN = "UNKNOWN"
    KNOWN = "KNOWN"
    CLEARED = "CLEARED"
    ABSENT = "ABSENT"


@dataclass(frozen=True)
class Observation:
    point: Point
    result: str
    bearing: float | None = None


@dataclass
class Channel:
    channel: int
    polygon: np.ndarray
    status: Status = Status.UNKNOWN
    history: list[Observation] = field(default_factory=list)
    measured: set[Point] = field(default_factory=set)
    failed_clear: set[Point] = field(default_factory=set)
    revision: int = 0
    discovered_at: float | None = None
    cleared_at: float | None = None
    near_point: Point | None = None
    geometry_failures: int = 0
    circle_cache: tuple | None = None
    posterior_cache: object = None
    posterior_revision: int = -1


@dataclass
class World:
    channels: dict[int, Channel]
    position: Point = (0., 0.)
    radio: int = 1
    virtual_time: float = 0.
    actions: int = 0
    committed_ids: set[str] = field(default_factory=set)

    def unknown(self):
        return [c for c in self.channels.values() if c.status == Status.UNKNOWN]

    def known(self):
        return [c for c in self.channels.values() if c.status == Status.KNOWN]

    def complete(self):
        cleared = sum(c.status == Status.CLEARED for c in self.channels.values())
        return cleared >= 16 or all(
            c.status in (Status.CLEARED, Status.ABSENT) for c in self.channels.values())


@dataclass(frozen=True)
class Op:
    kind: str
    channel: int

    def __post_init__(self):
        if self.kind not in ("measure", "clear") or not 1 <= self.channel <= 20:
            raise ValueError("Invalid operation")


@dataclass(frozen=True)
class Gain:
    explore: float = 0.
    refine: float = 0.
    clear: float = 0.

    def __add__(self, other):
        return Gain(self.explore + other.explore, self.refine + other.refine,
                    self.clear + other.clear)

    def weighted(self, cfg):
        return (cfg.weight_explore * self.explore +
                cfg.weight_refine * self.refine + cfg.weight_clear * self.clear)


@dataclass(frozen=True)
class Forecast:
    gain: Gain
    service: float
    reliable: bool = False


@dataclass(frozen=True)
class Plan:
    point: Point
    ops: tuple[Op, ...]
    source: str
    mode: str = "optional"
    primary: Op | None = None


@dataclass(frozen=True)
class Evaluation:
    gain: Gain
    time: float
    score: float
    switches: int


class PlanningDeadline(RuntimeError):
    pass


class InconsistentState(RuntimeError):
    pass
