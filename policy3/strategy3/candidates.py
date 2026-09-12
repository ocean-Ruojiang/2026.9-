"""Task-generated location candidates; no hidden simulator state is used."""
import math
import numpy as np
from .beliefs import rebuild
from .config import point
from .geometry import channel_circle
from .model import Action
from .q2 import load_provider, region_points


class Candidates:
    def __init__(self, cfg):
        self.cfg = cfg
        self.q2 = load_provider(cfg)

    def positions(self, world, channel):
        cfg = self.cfg
        center, radius = channel_circle(channel, cfg)
        belief = rebuild(channel, cfg)
        points = [world.position, center]
        cell_polygons = getattr(belief, 'cell_polygons', ())
        if len(cell_polygons):
            vertices = np.vstack(cell_polygons)
            # Bounding-box center is a conservative union-cover candidate, not a convexification update.
            points.insert(1, (vertices.min(axis=0)+vertices.max(axis=0))/2)
        if channel.near_point is not None:
            points.insert(0, channel.near_point)
        if len(belief.particles):
            p, w = np.asarray(belief.particles), np.asarray(belief.weights)
            mean = w @ p / w.sum()
            points.append(mean)
            # Candidate clear centers from separate modes, including low-mass branches.
            order = np.argsort(-w, kind='stable')
            selected = []
            for index in order:
                if all(np.linalg.norm(p[index]-q)>cfg.clear_radius for q in selected):
                    selected.append(p[index])
                if len(selected)>=4:
                    break
            points.extend(selected)
        points.extend(region_points(self.q2.propose(channel, world.position, cfg), cfg))
        positives = [o for o in channel.history if o.result in ('direction', 'near')]
        if positives:
            p = np.asarray(positives[-1].point)
            c = np.asarray(center)
            points.extend((p+.5*(c-p), p+.8*(c-p)))
        direction = np.asarray(world.position)-center
        norm = np.linalg.norm(direction)
        if norm>1e-6:
            unit = direction/norm
            points.append(np.asarray(center)+unit*min(100., max(25., radius*.3)))
        return list(dict.fromkeys(point(p, cfg) for p in points))[:cfg.candidate_limit]

    def actions(self, world, predictor, channel=None, local=False):
        targets = [world.channels[channel]] if channel is not None else world.known()
        actions = []
        for ch in targets:
            if ch.status != 'KNOWN':
                continue
            for p in self.positions(world, ch):
                if local and math.dist(world.position, p)>self.cfg.local_radius_m:
                    continue
                for kind in ('clear', 'measure'):
                    action = Action(p, kind, ch.channel, 'task_candidate')
                    if not predictor.valid(world, action):
                        continue
                    value = predictor.forecast(world, action)
                    if kind == 'clear' and not value.reliable_clear and value.clear<self.cfg.clear_min_probability:
                        continue
                    if value.weighted(self.cfg)>1e-12:
                        actions.append(action)
        return actions
