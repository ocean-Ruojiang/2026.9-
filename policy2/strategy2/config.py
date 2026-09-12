from dataclasses import dataclass, asdict, fields
import hashlib
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Config:
    name: str = "strategy2_default"
    algorithm_seed: int = 20260911
    # Physical constants are validated, not tuning parameters.
    domain_radius: float = 1800.0
    speed: float = 5.0
    recv_min: float = 1000.0
    recv_max: float = 1500.0
    near_radius: float = 5.0
    clear_radius: float = 20.0
    error_deg: float = 1.0
    reading_step_deg: float = 0.01
    geometry_eps: float = 1e-5
    angle_eps: float = 1e-7
    coordinate_decimals: int = 6
    circle_sides: int = 128
    coverage_cell_m: float = 60.0
    area_subdivisions: int = 8
    particles: int = 256
    scenarios: int = 32
    posterior_proposal_multiplier: int = 16
    candidate_cap: int = 48
    station_candidate_cap: int = 16
    q2_points_per_target: int = 3
    explore_candidates: int = 8
    clear_points_per_target: int = 2
    # Limit scoring cost, separate from the mandatory station channel list.
    optional_pool_cap: int = 8
    max_ops_per_template: int = 20
    weight_explore: float = 1.0
    weight_refine: float = 4.0
    weight_clear: float = 8.0
    trial_probability: float = 0.20
    station_offset_m: float = 80.0
    detour_budget_s: float = 90.0
    finish_extra_budget_s: float = 30.0
    finish_primary_limit: int = 12
    fallback_cell_m: float = 20.0
    # "module:factory" accepts factory(cfg) -> Q2Provider.
    q2_provider: str = "heuristic"
    real_limit_s: float = 1200.0
    exit_margin_s: float = 3.0
    max_actions: int = 10000
    score_eps: float = 1e-10
    http_timeout_s: float = 3.0
    http_retries: int = 2

    def validate(self):
        physical = dict(domain_radius=1800., speed=5., recv_min=1000.,
                        recv_max=1500., near_radius=5., clear_radius=20.,
                        error_deg=1., reading_step_deg=.01)
        for key, value in physical.items():
            if getattr(self, key) != value:
                raise ValueError(f"{key} is a Q3 physical constant, expected {value}")
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, (float, int)) and not math.isfinite(value):
                raise ValueError(f"Non-finite config: {f.name}")
        integers = ("circle_sides", "area_subdivisions", "particles", "scenarios",
                    "posterior_proposal_multiplier", "candidate_cap",
                    "station_candidate_cap", "q2_points_per_target",
                    "explore_candidates", "clear_points_per_target",
                    "optional_pool_cap", "max_ops_per_template",
                    "finish_primary_limit", "max_actions")
        for key in integers:
            v = getattr(self, key)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise ValueError(f"{key} must be a positive integer")
        if not 0 <= self.station_offset_m <= 100 - self.geometry_eps:
            raise ValueError("Station offset must leave a positive coverage margin")
        if not 0 < self.fallback_cell_m <= 20:
            raise ValueError("Fallback cells must preserve the 20 m covering proof")
        if not 0 <= self.trial_probability <= 1:
            raise ValueError("trial_probability must be in [0,1]")
        if min(self.detour_budget_s, self.finish_extra_budget_s) < 0:
            raise ValueError("Negative budget")
        if self.circle_sides < 16 or self.coverage_cell_m <= 0:
            raise ValueError("Invalid geometry discretization")
        if not (self.real_limit_s > self.exit_margin_s >= 0):
            raise ValueError("Invalid real-time limit")
        if self.geometry_eps <= 0 or self.angle_eps < 0:
            raise ValueError("Invalid numerical margin")
        if not 3 <= self.coordinate_decimals <= 12:
            raise ValueError("Coordinate precision must be 3..12")
        if self.scenarios > self.particles:
            raise ValueError("scenarios must not exceed particles")
        if min(self.weight_explore, self.weight_refine, self.weight_clear) <= 0:
            raise ValueError("Weights must be positive")
        return self

    def digest(self):
        data = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(data).hexdigest()[:16]

    @classmethod
    def load(cls, path=None):
        data = {} if path is None else json.loads(Path(path).read_text(encoding="utf-8-sig"))
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**data).validate()


def point(xy, cfg):
    p = tuple(round(float(x), cfg.coordinate_decimals) for x in xy)
    if len(p) != 2 or not all(math.isfinite(x) and abs(x) <= 2_000_000 for x in p):
        raise ValueError(f"Invalid submitted position: {p}")
    return p
