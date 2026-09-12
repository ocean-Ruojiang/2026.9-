from dataclasses import dataclass
import copy
import math
import numpy as np
from .config import point
from .model import Op
from .geometry import channel_circle
from .beliefs import posterior
from .q2 import region_points


@dataclass(frozen=True)
class Candidate:
    point: tuple
    primary: Op
    source: str


class Candidates:
    def __init__(self, cfg, coverage, provider):
        self.cfg, self.coverage, self.provider = cfg, coverage, provider
        self.q2_cache, self.clear_cache = {}, {}

    def q2(self, ch, world):
        # Current position may affect a team's Q2 optimizer.
        key = (ch.channel, ch.revision, world.position)
        if key not in self.q2_cache:
            # External agents receive a snapshot; candidate generation cannot edit live state.
            result = self.provider.propose(copy.deepcopy(ch), world.position, self.cfg)
            self.q2_cache[key] = region_points(result, self.cfg)
            if len(self.q2_cache) > 256:
                self.q2_cache = {key: self.q2_cache[key]}
        return self.q2_cache[key]

    def clear_points(self, ch):
        key = (ch.channel, ch.revision)
        if key in self.clear_cache:
            return self.clear_cache[key]
        cfg = self.cfg
        result = [point(channel_circle(ch, cfg)[0], cfg)]
        belief = posterior(ch, cfg)
        if not belief.degraded:
            xy = belief.particles[:,:2]
            masses = (np.linalg.norm(xy[:,None]-xy[None,:], axis=2) <= cfg.clear_radius).sum(axis=1)
            for i in np.argsort(-masses, kind="stable"):
                p = point(xy[i], cfg)
                if all(math.dist(p, q) > cfg.clear_radius/2 for q in result):
                    result.append(p)
                    if len(result) >= cfg.clear_points_per_target + 1:
                        break
        self.clear_cache[key] = result
        if len(self.clear_cache) > 256:
            self.clear_cache = {key: result}
        return result

    def normal(self, world, only_channel=None):
        groups = []
        known = [world.channels[only_channel]] if only_channel else world.known()
        for ch in known:
            items = [Candidate(p, Op("clear", ch.channel), "clear") for p in self.clear_points(ch)]
            items += [Candidate(p, Op("measure", ch.channel), "q2") for p in self.q2(ch, world)]
            items += [Candidate(world.position, Op("measure", ch.channel), "current"),
                      Candidate(world.position, Op("clear", ch.channel), "current")]
            groups.append(items)
        if only_channel is None and world.unknown():
            pts = ([self.coverage.anchors[i] for i in self.coverage.pending_stations(world)] +
                   self.coverage.explore_points(world, self.cfg.explore_candidates) + [world.position])
            for p in dict.fromkeys(point(p, self.cfg) for p in pts):
                c = max(world.unknown(), key=lambda ch: (self.coverage.gain(ch.channel,p), -ch.channel))
                groups.append([Candidate(p, Op("measure",c.channel), "explore")])
        # Fair round-robin, not a nearest-only truncation across channels.
        output, seen = [], set()
        while groups and len(output) < self.cfg.candidate_cap:
            next_groups = []
            for group in groups:
                if not group:
                    continue
                item = group.pop(0)
                key = (item.point, item.primary)
                if key not in seen:
                    output.append(item)
                    seen.add(key)
                if group:
                    next_groups.append(group)
                if len(output) >= self.cfg.candidate_cap:
                    break
            groups = next_groups
        return output

    def station(self, world, i):
        cfg = self.cfg
        b = np.asarray(self.coverage.anchors[i])
        radius = cfg.station_offset_m
        output = [point(b, cfg)]
        if radius == 0:
            return output
        groups = []
        for ch in world.known():
            groups.append(self.q2(ch, world) + self.clear_points(ch))
        groups.append([world.position])
        groups.append([b + radius*np.array((math.cos(a),math.sin(a)))
                       for a in np.linspace(0,2*math.pi,8,endpoint=False)])
        while groups and len(output) < cfg.station_candidate_cap:
            next_groups = []
            for group in groups:
                if not group:
                    continue
                p = np.asarray(group.pop(0))
                delta = p-b
                if np.linalg.norm(delta) > radius:
                    p = b + delta / np.linalg.norm(delta) * (radius-2*cfg.geometry_eps)
                p = point(p, cfg)
                if p not in output and math.dist(p,b) <= radius+cfg.geometry_eps:
                    output.append(p)
                if group:
                    next_groups.append(group)
                if len(output) >= cfg.station_candidate_cap:
                    break
            groups = next_groups
        return output
