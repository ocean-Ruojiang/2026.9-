"""Joint position/type/radius/orientation outcome prediction for action scoring.

Probability calculations are approximations for ranking, never absence certificates.
Both no-signal and positive branches condition the same complete hypotheses.
"""
from dataclasses import dataclass
import math
import numpy as np
from .beliefs import rebuild
from .geometry import guaranteed


@dataclass(frozen=True)
class Forecast:
    explore: float = 0.
    refine: float = 0.
    clear: float = 0.
    service: float = 5.
    no_signal_probability: float = 0.
    no_signal_refine: float = 0.
    reliable_clear: bool = False

    def weighted(self, cfg):
        return (cfg.weight_explore*self.explore + cfg.weight_refine*self.refine
                + cfg.weight_clear*self.clear)


@dataclass(frozen=True)
class Evaluation:
    gain: float
    time: float
    score: float
    forecasts: tuple


def spatial_scale(points, weights):
    """Weighted RMS radius, independent of how many orientation samples share x."""
    total = float(np.sum(weights))
    if total <= 0:
        return 0.
    w = weights/total
    center = w @ points
    return float(np.sqrt(max(0., np.sum(w*np.sum((points-center)**2, axis=1)))))


def received_mask(belief, receiver, cfg):
    delta = np.asarray(receiver)-np.asarray(belief.particles)
    distance = np.linalg.norm(delta, axis=1)
    directional = np.asarray(belief.directional, dtype=bool)
    angle = np.nan_to_num(np.asarray(belief.orientations), nan=0.)
    front = delta[:, 0]*np.cos(angle)+delta[:, 1]*np.sin(angle) >= -cfg.geometry_eps
    return (distance <= np.asarray(belief.radii)+cfg.geometry_eps) & (~directional | front), distance


def measurement_forecast(belief, receiver, cfg):
    points = np.asarray(belief.particles)
    if not len(points):
        return Forecast(service=cfg.measure_seconds)
    weights = np.asarray(belief.weights, dtype=float)
    weights = weights/weights.sum()
    old_scale = spatial_scale(points, weights)
    received, distance = received_mask(belief, receiver, cfg)
    negative = ~received
    near = received & (distance <= cfg.near_radius)
    direction = received & ~near
    p_negative = float(weights[negative].sum())

    def reduction(mask):
        if not np.any(mask) or old_scale <= 1e-10:
            return 0.
        after = spatial_scale(points[mask], weights[mask])
        return float(np.clip(1.-after/old_scale, 0., 1.))

    negative_gain = reduction(negative)
    gain = p_negative*negative_gain + float(weights[near].sum())*reduction(near)
    ids = np.flatnonzero(direction)
    if len(ids):
        # Deterministic weighted quantiles of bearings, with bounded uniform-error quadrature.
        bearings = np.arctan2(points[:, 1]-receiver[1], points[:, 0]-receiver[0])
        reference = math.atan2(float(np.sum(weights[ids]*np.sin(bearings[ids]))),
                               float(np.sum(weights[ids]*np.cos(bearings[ids]))))
        unwrapped = (bearings-reference+math.pi) % (2*math.pi)-math.pi
        ids = ids[np.argsort(unwrapped[ids], kind='stable')]
        mass = float(weights[ids].sum())
        cdf = np.cumsum(weights[ids])/mass
        count = min(cfg.prediction_outcomes, len(ids))
        chosen = ids[np.minimum(np.searchsorted(cdf, (np.arange(count)+.5)/count), len(ids)-1)]
        half = math.radians(cfg.error_deg+cfg.reading_step_deg/2+cfg.angle_eps)
        # Nodes and weights for the bounded uniform measurement-error prior.
        noise = math.sqrt(3./5.)*math.radians(cfg.error_deg)
        for idx in chosen:
            for offset, probability in ((-noise, 5./18.), (0., 4./9.), (noise, 5./18.)):
                reading = round(math.degrees(bearings[idx]+offset)/cfg.reading_step_deg)*cfg.reading_step_deg
                difference = (bearings-math.radians(reading)+math.pi) % (2*math.pi)-math.pi
                compatible = direction & (np.abs(difference) <= half)
                gain += mass/count*probability*reduction(compatible)
    return Forecast(refine=float(np.clip(gain, 0., 1.)), service=cfg.measure_seconds,
                    no_signal_probability=p_negative, no_signal_refine=negative_gain)


def worst_service(action, radio, cfg):
    operation = cfg.measure_seconds if action.kind == 'measure' else max(
        cfg.clear_failure_seconds, cfg.clear_success_seconds)
    return operation + cfg.switch_seconds*(action.kind == 'measure' and radio != action.channel)


class Predictor:
    def __init__(self, cfg, coverage):
        self.cfg, self.coverage = cfg, coverage
        self.cache = {}

    def valid(self, world, action):
        ch = world.channels[action.channel]
        if action.kind == 'clear':
            return ch.status == 'KNOWN' and not any(math.dist(action.point, p)<1e-6 for p in ch.failed_clear)
        return ch.status in ('UNKNOWN', 'KNOWN') and not any(
            math.dist(action.point, o.point)<1e-6 for o in ch.history)

    def forecast(self, world, action):
        ch, cfg = world.channels[action.channel], self.cfg
        if action.kind == 'measure' and ch.status == 'UNKNOWN':
            return Forecast(explore=float(self.coverage.exploration_gain(action.point, action.channel)),
                            service=cfg.measure_seconds)
        key = (ch.channel, ch.revision, tuple(action.point), action.kind)
        if key in self.cache:
            return self.cache[key]
        belief = rebuild(ch, cfg)
        if action.kind == 'measure':
            result = measurement_forecast(belief, action.point, cfg)
        else:
            cell_polygons = getattr(belief, 'cell_polygons', ())
            safe_vertices = np.vstack(cell_polygons) if len(cell_polygons) else ch.polygon
            reliable = guaranteed(safe_vertices, action.point, cfg)
            if reliable:
                probability = 1.
            elif len(belief.particles):
                mask = np.linalg.norm(np.asarray(belief.particles)-action.point, axis=1) <= cfg.clear_radius
                probability = float(np.asarray(belief.weights)[mask].sum())
            else:
                probability = 0.
            result = Forecast(clear=probability,
                service=probability*cfg.clear_success_seconds+(1-probability)*cfg.clear_failure_seconds,
                reliable_clear=reliable)
        if len(self.cache)>30000:
            self.cache.clear()
        self.cache[key] = result
        return result

    def evaluate(self, world, actions):
        if not actions:
            return Evaluation(0., 0., 0., ())
        point = actions[0].point
        if any(math.dist(a.point, point)>1e-6 for a in actions):
            raise ValueError('An operation template must use one shared position')
        duration = math.dist(world.position, point)/self.cfg.speed
        radio, gain, forecasts = world.radio, 0., []
        for action in actions:
            f = self.forecast(world, action)
            duration += f.service
            if action.kind == 'measure':
                duration += (radio != action.channel)*self.cfg.switch_seconds
                radio = action.channel
            gain += f.weighted(self.cfg)
            forecasts.append(f)
        return Evaluation(gain, duration, gain/max(duration, 1e-9), tuple(forecasts))
