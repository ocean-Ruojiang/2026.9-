from dataclasses import dataclass
import math
from .gains import worst_service


@dataclass
class Stage:
    station: int
    point: tuple
    initial_budget: float
    remaining: float
    optional_actions: int = 0
    scan_actions: int = 0

    def extra_upper(self, world, plan, cfg):
        d = (math.dist(world.position,plan.point) + math.dist(plan.point,self.point) -
             math.dist(world.position,self.point))/cfg.speed
        return max(0.,d) + worst_service(plan,world.radio)

    def allows(self, world, plan, cfg):
        return self.extra_upper(world,plan,cfg) <= self.remaining + cfg.score_eps

    def charge(self, old, new, actual_time, cfg):
        cost = actual_time + (math.dist(new,self.point)-math.dist(old,self.point))/cfg.speed
        if cost < -1e-6 or cost > self.remaining + 1e-4:
            raise RuntimeError("Detour ledger inconsistent with accepted action")
        self.remaining = max(0.,self.remaining-cost)
        self.optional_actions += 1
        return cost
