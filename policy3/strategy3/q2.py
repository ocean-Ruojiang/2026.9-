"""Explicit Q2 extension point; the included baseline is not an optimal oracle."""
from dataclasses import dataclass
import importlib
import math
from typing import Protocol
import numpy as np
from .config import point
from .geometry import channel_circle


@dataclass(frozen=True)
class Q2Result:
    points: tuple = ()
    regions: tuple = ()
    description: str = ""
    exact_worst_diameter: bool = False


class Q2Provider(Protocol):
    def propose(self, channel, current, cfg) -> Q2Result: ...
    def evaluate_worst_diameter(self, polygon, candidate, halfwidth_deg) -> float: ...


class MissingQ2:
    def propose(self, channel, current, cfg):
        raise NotImplementedError("Supply the team's Q2 optimizer or select q2_provider=heuristic")

    def evaluate_worst_diameter(self, polygon, candidate, halfwidth_deg):
        raise NotImplementedError("No exact Q2 worst-diameter oracle has been supplied")


class HeuristicQ2(MissingQ2):
    def propose(self, channel, current, cfg):
        center, radius = channel_circle(channel, cfg)
        positive = next((o for o in reversed(channel.history) if o.result == 'direction'), None)
        angle = math.radians((positive.bearing if positive else 0.) + 90.)
        normal = np.array((math.cos(angle), math.sin(angle)))
        reach = min(180., max(35., radius * .35))
        center = np.asarray(center)
        pts = [center, center + reach*normal, center - reach*normal]
        if positive is not None:
            p = np.asarray(positive.point)
            # Transverse probes can discriminate beam explanations as well as improve bearings.
            for scale in (.5, 1.):
                pts.extend((p + scale*reach*normal, p - scale*reach*normal))
        return Q2Result(tuple(dict.fromkeys(point(p, cfg) for p in pts)), (),
                        'heuristic center and transverse probes; not Q2 optimum', False)


def load_provider(cfg):
    if cfg.q2_provider == 'heuristic':
        return HeuristicQ2()
    if cfg.q2_provider == 'missing':
        return MissingQ2()
    module, separator, factory = cfg.q2_provider.partition(':')
    if not separator:
        raise ValueError('q2_provider must be heuristic, missing, or module:factory')
    return getattr(importlib.import_module(module), factory)(cfg)


def region_points(result, cfg):
    points = list(result.points)
    for region in result.regions:
        p = np.asarray(region, dtype=float)
        if p.ndim != 2 or p.shape[1] != 2 or not len(p):
            raise ValueError('Q2 candidate regions must be nonempty N x 2 arrays')
        points.extend(p)
        points.extend((p + np.roll(p, -1, axis=0))/2)
        points.append(p.mean(axis=0))
    return list(dict.fromkeys(point(p, cfg) for p in points))
