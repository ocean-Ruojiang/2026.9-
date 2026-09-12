import math
import numpy as np
from .model import Gain, Forecast, Evaluation, Status
from .geometry import channel_circle, enclosing_circle, after_measure, guaranteed
from .beliefs import posterior, predict_measure


def progress(radius, cfg):
    return max(0., min(1., math.log(cfg.domain_radius/max(radius, cfg.clear_radius)) /
                       math.log(cfg.domain_radius/cfg.clear_radius)))


def switches(ops, radio):
    n = 0
    for op in ops:
        if op.kind == "measure":
            n += op.channel != radio
            radio = op.channel
    return int(n)


def worst_service(plan, radio):
    return sum(5 for _ in plan.ops) + switches(plan.ops, radio)


class Predictor:
    def __init__(self, cfg, coverage, check_deadline=lambda: None):
        self.cfg, self.coverage, self.check = cfg, coverage, check_deadline
        self.cache = {}
        self.calls = 0

    def atom(self, world, p, op):
        self.check()
        ch, cfg = world.channels[op.channel], self.cfg
        if ch.status in (Status.CLEARED, Status.ABSENT):
            return Forecast(Gain(), 5. if op.kind == "measure" else 3.)
        key = (ch.channel, ch.revision, p, op.kind,
               self.coverage.revision if ch.status == Status.UNKNOWN else -1)
        if key in self.cache:
            return self.cache[key]
        self.calls += 1
        if op.kind == "measure" and ch.status == Status.UNKNOWN:
            result = Forecast(Gain(explore=self.coverage.gain(ch.channel, p)), 5.)
        elif op.kind == "measure" and p in ch.measured:
            result = Forecast(Gain(), 5.)
        elif op.kind == "clear" and guaranteed(ch.polygon, p, cfg):
            result = Forecast(Gain(clear=1.), 5., True)
        else:
            belief = posterior(ch, cfg)
            if op.kind == "clear":
                prob = 0. if belief.degraded else float(np.mean(
                    np.linalg.norm(belief.scenarios[:,:2] - p, axis=1) <= cfg.clear_radius))
                result = Forecast(Gain(clear=prob), 3+2*prob)
            elif belief.degraded:
                result = Forecast(Gain(), 5.)
            else:
                before = progress(channel_circle(ch, cfg)[1], cfg)
                values = []
                for row, error in zip(belief.scenarios, belief.errors):
                    self.check()
                    outcome, bearing = predict_measure(row, error, p, cfg)
                    if outcome == "no_signal":
                        values.append(0.)
                    else:
                        updated = after_measure(ch.polygon, p, outcome, bearing, cfg)
                        # No optimistic score for inconsistent numeric geometry.
                        if not len(updated):
                            values.append(0.)
                        else:
                            radius = enclosing_circle(updated, cfg.geometry_eps)[1]
                            values.append(max(0., progress(radius, cfg)-before))
                result = Forecast(Gain(refine=float(np.mean(values))), 5.)
        if len(self.cache) > 12000:
            self.cache.clear()
        self.cache[key] = result
        return result

    def evaluate(self, world, plan):
        gain, service = Gain(), 0.
        for op in plan.ops:
            f = self.atom(world, plan.point, op)
            gain, service = gain + f.gain, service + f.service
        n = switches(plan.ops, world.radio)
        duration = math.dist(world.position, plan.point)/self.cfg.speed + service+n
        if not plan.ops or duration <= 0:
            raise ValueError("Empty or zero-duration plan")
        return Evaluation(gain, duration, gain.weighted(self.cfg)/duration, n)

    def valid(self, world, p, op):
        ch = world.channels[op.channel]
        if ch.status in (Status.CLEARED, Status.ABSENT):
            return False
        if op.kind == "measure":
            return p not in ch.measured
        if ch.status != Status.KNOWN or p in ch.failed_clear:
            return False
        f = self.atom(world, p, op)
        return f.reliable or f.gain.clear >= self.cfg.trial_probability
