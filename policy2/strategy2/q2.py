"""The real Q2 optimizer is an explicit extension point, not silently fabricated."""
from typing import Protocol
from dataclasses import dataclass
import importlib
import math
import numpy as np
from .config import point
from .geometry import channel_circle


@dataclass(frozen=True)
class Q2Result:
    points: tuple[tuple[float, float], ...]
    # Each polygon describes a candidate region; empty when unavailable.
    regions: tuple = ()
    description: str = ""
    exact_worst_diameter: bool = False


class Q2Provider(Protocol):
    def propose(self, channel, current, cfg) -> Q2Result:
        """Use observed history/polygon only; include several points/regions."""
        ...

    def evaluate_worst_diameter(self, polygon, candidate, halfwidth_deg) -> float:
        """Supremum over all feasible new readings, not a sample minimum/maximum."""
        ...


class MissingQ2:
    def propose(self, channel, current, cfg):
        raise NotImplementedError("Connect the team's Q2 optimizer or select heuristic explicitly")

    def evaluate_worst_diameter(self, polygon, candidate, halfwidth_deg):
        raise NotImplementedError("The team's worst-diameter oracle has not been supplied")


class HeuristicQ2(MissingQ2):
    """Runnable baseline only: circle center and transverse observation offsets."""
    def propose(self, channel, current, cfg):
        center, radius = channel_circle(channel, cfg)
        direction = next((h.bearing for h in reversed(channel.history)
                          if h.result == "direction"), 0.)
        angle = math.radians(direction + 90)
        distance = min(300., max(50., radius))
        offset = np.array((math.cos(angle), math.sin(angle))) * distance
        candidates = [center, np.asarray(center)+offset, np.asarray(center)-offset]
        for a in np.linspace(0, 2*math.pi, 8, endpoint=False):
            candidates.append(np.asarray(center)+distance*np.array((math.cos(a), math.sin(a))))
        pts = tuple(dict.fromkeys(point(p, cfg) for p in candidates))
        return Q2Result(pts[:cfg.q2_points_per_target], (),
                        "heuristic center/transverse candidates; NOT Q2 optimum", False)


def load_provider(cfg):
    if cfg.q2_provider == "heuristic":
        return HeuristicQ2()
    if cfg.q2_provider == "missing":
        return MissingQ2()
    module, sep, factory = cfg.q2_provider.partition(":")
    if not sep:
        raise ValueError("q2_provider must be heuristic, missing, or module:factory")
    return getattr(importlib.import_module(module), factory)(cfg)


def region_points(result, cfg):
    points = list(result.points)
    for region in result.regions:
        p = np.asarray(region, dtype=float)
        if p.ndim != 2 or p.shape[1] != 2 or not len(p):
            raise ValueError("Q2 regions must be nonempty N x 2 arrays")
        points.extend(p)
        points.extend((p + np.roll(p, -1, axis=0))/2)
        points.append(p.mean(axis=0))
    return list(dict.fromkeys(point(p, cfg) for p in points))
