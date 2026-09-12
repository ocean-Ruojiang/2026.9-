from dataclasses import dataclass, field
import numpy as np

Point=tuple[float,float]


@dataclass(frozen=True)
class Observation:
    point: Point
    result: str
    bearing: float | None = None
    request_id: str = ""


@dataclass
class Channel:
    channel: int
    polygon: np.ndarray
    status: str = "UNKNOWN"
    history: list[Observation] = field(default_factory=list)
    failed_clear: list[Point] = field(default_factory=list)
    measured: set[Point] = field(default_factory=set)
    revision: int = 0
    belief: object = None
    near_point: Point | None = None
    primary_actions: int = 0
    fallback_points: list | None = None
    fallback_index: int = 0
    discovered_at: float | None = None
    cleared_at: float | None = None
    geometry_failures: int = 0
    circle_cache: object = None


@dataclass
class World:
    channels: dict[int,Channel]
    position: Point = (0.,0.)
    radio: int = 1
    virtual_time: float = 0.
    actions: int = 0
    committed_ids: set[str] = field(default_factory=set)

    def unknown(self):
        return [c for c in self.channels.values() if c.status=="UNKNOWN"]

    def known(self):
        return [c for c in self.channels.values() if c.status=="KNOWN"]

    def complete(self):
        return sum(c.status=="CLEARED" for c in self.channels.values())>=16 or all(
            c.status in ("CLEARED","ABSENT") for c in self.channels.values())


@dataclass(frozen=True)
class Action:
    point: Point
    kind: str
    channel: int
    reason: str = ""
    score: float = 0.
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in ('measure','clear') or type(self.channel) is not int or not 1<=self.channel<=20:
            raise ValueError("Invalid action")


class InconsistentState(RuntimeError):
    pass
