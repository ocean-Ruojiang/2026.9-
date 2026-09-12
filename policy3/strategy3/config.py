"""Validated algorithm controls; task physics are not experimental parameters."""
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
from pathlib import Path


@dataclass(frozen=True)
class Config:
    name: str = "policy3_default"
    seed: int = 20260913
    domain_radius: float = 1800.0
    recv_min: float = 1000.0
    recv_max: float = 1500.0
    speed: float = 5.0
    near_radius: float = 5.0
    clear_radius: float = 20.0
    error_deg: float = 1.0
    reading_step_deg: float = .01
    measure_seconds: float = 5.0
    switch_seconds: float = 1.0
    clear_success_seconds: float = 5.0
    clear_failure_seconds: float = 3.0
    geometry_eps: float = 1e-7
    angle_eps: float = 1e-7
    coordinate_decimals: int = 6
    circle_sides: int = 96
    grid_initial_m: float = 40.0
    grid_min_m: float = 5.0
    grid_max_cells: int = 1200
    particle_count: int = 192
    orientation_samples: int = 8
    prior_directional: float = .5
    weight_explore: float = 1.0
    weight_refine: float = 4.0
    weight_clear: float = 8.0
    detour_budget_s: float = 90.0
    finish_extra_budget_s: float = 30.0
    clear_min_probability: float = .25
    max_refine_actions: int = 10
    candidate_limit: int = 18
    prediction_outcomes: int = 16
    max_template_ops: int = 3
    q2_provider: str = "heuristic"
    local_radius_m: float = 600.0
    fallback_spacing_m: float = 24.0
    real_limit_s: float = 600.0
    exit_margin_s: float = 4.0
    max_actions: int = 10000
    http_timeout_s: float = 3.0
    http_retries: int = 2

    def validate(self):
        physical = dict(domain_radius=1800.,recv_min=1000.,recv_max=1500.,speed=5.,
            near_radius=5.,clear_radius=20.,error_deg=1.,reading_step_deg=.01,
            measure_seconds=5.,switch_seconds=1.,clear_success_seconds=5.,clear_failure_seconds=3.)
        for key,value in physical.items():
            if getattr(self,key)!=value:
                raise ValueError(f"{key} is fixed by the task: {value}")
        for f in fields(self):
            v=getattr(self,f.name)
            if isinstance(v,(float,int)) and (isinstance(v,bool) or not math.isfinite(v)):
                raise ValueError(f"Invalid numeric value: {f.name}")
        for key in ('circle_sides','grid_max_cells','particle_count','orientation_samples',
                    'max_refine_actions','candidate_limit','prediction_outcomes','max_template_ops','max_actions'):
            if type(getattr(self,key)) is not int or getattr(self,key)<=0:
                raise ValueError(f"{key} must be a positive integer")
        if not 0<self.grid_min_m<=self.grid_initial_m or self.grid_max_cells<16:
            raise ValueError("Invalid adaptive grid controls")
        if self.circle_sides<16 or self.geometry_eps<=0 or self.angle_eps<0:
            raise ValueError("Invalid geometry controls")
        if not 0<self.fallback_spacing_m<2*self.clear_radius/math.sqrt(2):
            raise ValueError("Fallback grid must fit strictly inside the 20 m clear radius")
        if not 0<self.prior_directional<1 or not 0<=self.clear_min_probability<=1:
            raise ValueError("Invalid prior or clearing probability")
        if min(self.weight_explore,self.weight_refine,self.weight_clear)<=0 or min(self.detour_budget_s,self.finish_extra_budget_s)<0:
            raise ValueError("Weights must be positive and detour budget nonnegative")
        if not self.real_limit_s>self.exit_margin_s>=0 or self.local_radius_m<=0:
            raise ValueError("Invalid planning deadline/range")
        if not 3<=self.coordinate_decimals<=12:
            raise ValueError("Coordinate precision must be between 3 and 12")
        if type(self.http_retries) is not int or self.http_retries<0 or self.http_timeout_s<=0:
            raise ValueError("Invalid HTTP controls")
        return self

    @classmethod
    def load(cls,path=None):
        data={} if path is None else json.loads(Path(path).read_text(encoding='utf-8-sig'))
        unknown=set(data)-{f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown configuration fields: {sorted(unknown)}")
        return cls(**data).validate()

    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True).encode()).hexdigest()[:16]


def point(xy,cfg):
    p=tuple(round(float(v),cfg.coordinate_decimals) for v in xy)
    if len(p)!=2 or not all(math.isfinite(v) and abs(v)<=2_000_000 for v in p):
        raise ValueError("Invalid submitted point")
    return p
