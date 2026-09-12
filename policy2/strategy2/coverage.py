import itertools
import math
import numpy as np
from .model import Status


class Coverage:
    def __init__(self, cfg):
        self.cfg = cfg
        r = 900 * math.sqrt(3)
        self.anchors = [(0., 0.)] + [
            (r*math.cos(k*math.pi/3), r*math.sin(k*math.pi/3)) for k in range(6)]
        self.evidence = np.zeros((7, 20), dtype=bool)
        self.witnesses = {}
        self.revision = 0
        h, R = cfg.coverage_cell_m, cfg.domain_radius
        z = np.arange(-R, R + h, h)
        x, y = np.meshgrid(z+h/2, z+h/2)
        centers = np.column_stack((x.ravel(), y.ravel()))
        nearest = np.maximum(np.abs(centers) - h/2, 0)
        self.centers = centers[np.linalg.norm(nearest, axis=1) <= R]
        k = cfg.area_subdivisions
        shifts = (np.arange(k) + .5) * h/k - h/2
        area = np.zeros(len(self.centers))
        for dx in shifts:
            for dy in shifts:
                area += np.linalg.norm(self.centers + (dx, dy), axis=1) <= R
        self.areas = area * h*h / (k*k)
        self.half_diag = h / math.sqrt(2)
        self.excluded = np.zeros((20, len(self.centers)), dtype=bool)
        self.mask_cache = {}

    def guaranteed_cells(self, point):
        if point not in self.mask_cache:
            self.mask_cache[point] = (
                np.linalg.norm(self.centers-point, axis=1) + self.half_diag
                <= self.cfg.recv_min - self.cfg.geometry_eps)
            if len(self.mask_cache) > 512:
                last = self.mask_cache[point]
                self.mask_cache.clear()
                self.mask_cache[point] = last
        return self.mask_cache[point]

    def record_no_signal(self, channel, point):
        self.excluded[channel-1] |= self.guaranteed_cells(point)
        for i, anchor in enumerate(self.anchors):
            if math.dist(point, anchor) + 900 <= self.cfg.recv_min - self.cfg.geometry_eps:
                self.evidence[i, channel-1] = True
                self.witnesses[(i, channel)] = point
        self.revision += 1

    def absent_certificate(self, channel):
        return bool(self.evidence[:, channel-1].all())

    def needed(self, world, station):
        return [c.channel for c in world.unknown()
                if not self.evidence[station, c.channel-1]]

    def pending_stations(self, world):
        return [i for i in range(1, 7) if self.needed(world, i)]

    def gain(self, channel, point):
        mask = self.guaranteed_cells(point) & ~self.excluded[channel-1]
        return float(self.areas[mask].sum() / (math.pi*self.cfg.domain_radius**2))

    def explore_points(self, world, count):
        unknown = [c.channel-1 for c in world.unknown()]
        if not unknown:
            return []
        weights = (~self.excluded[unknown]).sum(axis=0) * self.areas
        indices = np.argsort(-weights, kind="stable")
        chosen = []
        for j in indices:
            if weights[j] <= 0:
                break
            p = tuple(self.centers[j])
            if all(math.dist(p, q) >= 400 for q in chosen):
                chosen.append(p)
                if len(chosen) >= count:
                    break
        return chosen

    def route(self, world):
        pending = self.pending_stations(world)
        def length(order):
            seq = [world.position] + [self.anchors[i] for i in order]
            return sum(math.dist(a,b) for a,b in zip(seq, seq[1:]))
        return min(itertools.permutations(pending), key=lambda o: (length(o), o),
                   default=())
