"""Joint positive/negative inference without simulator truth access.

The conservative cell union and the approximate posterior are deliberately
separate.  Empty particles or tiny posterior weights never certify absence.
Spatial/type priors are uniform, radius is uniform on [1000,1500], orientation
is uniform on the circle, and bearings have a bounded uniform-error model.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from . import angles
from .grid import build_grid, distance_bounds
from .geometry import sample_polygon


@dataclass
class PointFeasibility:
    lower_radius: float
    omni_upper_radius: float
    orientation_intervals: list[angles.Interval]
    omni: bool
    directional: bool
    forced_negatives: int


@dataclass
class Belief:
    cells: np.ndarray
    cell_polygons: list[np.ndarray]
    cell_areas: np.ndarray
    particles: np.ndarray
    weights: np.ndarray
    radii: np.ndarray
    orientations: np.ndarray
    directional: np.ndarray
    area: float
    diagnostics: dict
    revision: int = -1
    config_signature: tuple = ()

    @property
    def valid_samples(self):
        return bool(len(self.particles))


def _records(history):
    """Same-location fixed sensor errors are not independent replicates."""
    seen = set()
    unique = []
    for o in history:
        key = (tuple(o.point), o.result, o.bearing)
        if key not in seen:
            seen.add(key)
            unique.append(o)
    return unique


def _point_feasibility(point, positives, negatives, cfg, failures=()):
    x = np.asarray(point, dtype=float)
    lower = float(cfg.recv_min)
    impossible = lambda: PointFeasibility(lower, lower, [], False, False, 0)
    if np.linalg.norm(x) > cfg.domain_radius + cfg.geometry_eps:
        return impossible()
    if any(np.linalg.norm(x - p) <= cfg.clear_radius for p in failures):
        return impossible()
    for o in positives:
        vector = x - np.asarray(o.point)
        distance = float(np.linalg.norm(vector))
        if distance > cfg.recv_max + cfg.geometry_eps:
            return impossible()
        if o.result == 'near' and distance > cfg.near_radius + cfg.geometry_eps:
            return impossible()
        if o.result == 'direction':
            if distance <= cfg.near_radius or o.bearing is None:
                return impossible()
            bearing = math.degrees(math.atan2(vector[1], vector[0]))
            error = abs((bearing - o.bearing + 180.0) % 360.0 - 180.0)
            delta = cfg.error_deg + cfg.reading_step_deg / 2 + getattr(cfg, 'angle_eps', 0.0)
            if error > delta + 1e-9:
                return impossible()
        lower = max(lower, distance)
    if lower > cfg.recv_max:
        return impossible()
    negative_distances = [float(np.linalg.norm(np.asarray(o.point) - x)) for o in negatives]
    omni_upper = min([float(cfg.recv_max)] + negative_distances)
    omni = all(d > lower for d in negative_distances)
    forced = [(o, d) for o, d in zip(negatives, negative_distances) if d <= lower]
    allowed = angles.full_circle()
    for o in positives:
        vector = np.asarray(o.point) - x
        if float(np.linalg.norm(vector)) <= 1e-12:
            continue
        allowed = angles.intersect(allowed,
            angles.arc(math.atan2(vector[1], vector[0]), math.pi / 2, closed=True))
        if not allowed:
            break
    for o, distance in forced:
        if distance <= 1e-12:
            allowed = []
            break
        vector = np.asarray(o.point) - x
        allowed = angles.intersect(allowed,
            angles.arc(math.atan2(vector[1], vector[0]) + math.pi,
                       math.pi / 2, closed=False))
        if not allowed:
            break
    return PointFeasibility(lower, omni_upper, allowed, omni, bool(allowed), len(forced))


def point_feasibility(point, history, cfg, failures=()) -> PointFeasibility:
    """Exact type/orientation existence check at a single spatial point.

    Choosing rho=L is an existence witness shared by all observations.  It is
    NOT the radius estimate used for predicting future observations.
    """
    records = _records(history)
    return _point_feasibility(point,
        [o for o in records if o.result in ('direction', 'near')],
        [o for o in records if o.result == 'no_signal'], cfg, failures)


def radius_upper(point, orientation, negatives, cfg) -> float:
    """Upper bound for one fixed orientation; front-side negatives only."""
    upper = float(cfg.recv_max)
    unit = np.array([math.cos(orientation), math.sin(orientation)])
    x = np.asarray(point)
    for o in negatives:
        vector = np.asarray(o.point) - x
        if float(np.dot(unit, vector)) >= 0.0:
            upper = min(upper, float(np.linalg.norm(vector)))
    return upper


def _signature(cfg):
    return tuple(getattr(cfg, name) for name in (
        'grid_initial_m', 'grid_min_m', 'grid_max_cells', 'particle_count',
        'orientation_samples', 'prior_directional', 'seed', 'recv_min',
        'recv_max', 'domain_radius', 'clear_radius', 'near_radius',
        'error_deg', 'reading_step_deg', 'geometry_eps', 'angle_eps'))


def rebuild(channel, cfg) -> Belief:
    signature = _signature(cfg)
    previous = channel.belief
    if (isinstance(previous, Belief) and previous.revision == channel.revision
            and previous.config_signature == signature):
        return previous
    records = _records(channel.history)
    positives = [o for o in records if o.result in ('direction', 'near')]
    negatives = [o for o in records if o.result == 'no_signal']
    # Search for never-detected channels is carried by the independently
    # verified station/channel coverage ledger.  Avoid twenty full-domain
    # posterior rebuilds on every station scan.
    if not positives:
        result = Belief(np.empty((0, 4)), [], np.empty(0), np.empty((0, 2)),
            np.empty(0), np.empty(0), np.empty(0), np.empty(0, dtype=bool),
            math.pi * cfg.domain_radius**2,
            {'deferred_unknown': True, 'degraded': False, 'history': len(records),
             'negative_records': len(negatives), 'cells': 0}, channel.revision, signature)
        channel.belief = result
        return result

    grid = build_grid(channel.polygon, records, channel.failed_clear, cfg)
    rng = np.random.default_rng(cfg.seed + 104729 * channel.channel + 1009 * channel.revision)
    states, masses = [], []
    omni_count = directional_count = sampled_cells = 0
    forced_used = 0
    radius_span = cfg.recv_max - cfg.recv_min
    prior_d = cfg.prior_directional

    for poly, cell_area in zip(grid.polygons, grid.areas):
        # A cell centroid represents area only in the approximate posterior.
        # If it is infeasible, try interior samples; the cell itself survives.
        points = [np.mean(poly, axis=0)]
        viable = []
        for attempt in range(3):
            if attempt:
                random_points = sample_polygon(poly, 2, rng)
                if len(random_points):
                    points = list(random_points)
                else:
                    points = list(poly)
            for x in points:
                feasible = _point_feasibility(x, positives, negatives, cfg, channel.failed_clear)
                if feasible.omni or feasible.directional:
                    viable.append((x, feasible))
            if viable:
                break
        if not viable:
            continue
        sampled_cells += 1
        spatial_mass = max(float(cell_area), 1e-12) / len(viable)
        for x, feasible in viable:
            lower = feasible.lower_radius
            forced_used += feasible.forced_negatives
            if feasible.omni:
                omni_count += 1
                width = max(0.0, feasible.omni_upper_radius - lower)
                # Keep a boundary-only witness in the approximate pool.  Its
                # tiny weight cannot remove the conservative cell branch.
                mass = spatial_mass * (1 - prior_d) * max(width / radius_span, 1e-12)
                radius = lower + width * rng.random()
                states.append((x[0], x[1], radius, math.nan, False))
                masses.append(mass)
            if feasible.directional:
                directional_count += 1
                choices = angles.sample(feasible.orientation_intervals, cfg.orientation_samples)
                angular_fraction = max(angles.total_width(feasible.orientation_intervals) / angles.TAU,
                                       1e-12)
                for alpha in choices:
                    upper = radius_upper(x, alpha, negatives, cfg)
                    if upper < lower:
                        continue
                    # If an in-range negative lies on a floating-point arc
                    # endpoint, discard this sample, never its spatial cell.
                    unit = np.array([math.cos(alpha), math.sin(alpha)])
                    if any(np.linalg.norm(np.asarray(o.point) - x) <= lower and
                           np.dot(unit, np.asarray(o.point) - x) >= 0.0 for o in negatives):
                        continue
                    if any(np.dot(unit, np.asarray(o.point) - x) < -1e-8 for o in positives):
                        continue
                    width = max(0.0, upper - lower)
                    mass = spatial_mass * prior_d * angular_fraction / max(1, len(choices))
                    mass *= max(width / radius_span, 1e-12)
                    radius = lower + width * rng.random()
                    states.append((x[0], x[1], radius, alpha, True))
                    masses.append(mass)

    if states:
        pool = np.asarray(states, dtype=float)
        probabilities = np.asarray(masses, dtype=float)
        probabilities /= probabilities.sum()
        count = min(int(cfg.particle_count), len(pool))
        if len(pool) > count:
            # Stratify by type first, preserving the integrated O/D posterior
            # mass exactly.  This also avoids periodic aliasing between one
            # omni row and eight direction rows at each spatial quadrature
            # point.  Within each type use independent stratified offsets.
            types = pool[:, 4].astype(bool)
            mass_d = float(probabilities[types].sum())
            if types.any() and (~types).any() and count >= 2:
                count_d = min(count - 1, max(1, int(round(count * mass_d))))
            else:
                count_d = count if mass_d >= .5 else 0
            selected, selected_weights = [], []
            for is_directional, number in ((False, count - count_d), (True, count_d)):
                if number == 0:
                    continue
                source_indices = np.flatnonzero(types == is_directional)
                mass = float(probabilities[source_indices].sum())
                conditional = probabilities[source_indices] / mass
                quantiles = (np.arange(number) + rng.random(number)) / number
                indices = np.searchsorted(np.cumsum(conditional), quantiles, side='right')
                selected.extend(source_indices[np.minimum(indices, len(source_indices) - 1)])
                selected_weights.extend([mass / number] * number)
            pool = pool[selected]
            probabilities = np.asarray(selected_weights)
            probabilities /= probabilities.sum()
        positions, radii, orientations, directional = (
            pool[:, :2], pool[:, 2], pool[:, 3], pool[:, 4].astype(bool))
    else:
        positions = np.empty((0, 2))
        probabilities = radii = orientations = np.empty(0)
        directional = np.empty(0, dtype=bool)

    diagnostics = dict(grid.diagnostics)
    diagnostics.update(
        cells=len(grid.cells), retained_area=float(grid.areas.sum()),
        history=len(records), raw_history=len(channel.history),
        positive_records=len(positives), negative_records=len(negatives),
        omni_possible_cells=int(grid.omni_possible.sum()),
        directional_possible_cells=int(grid.directional_possible.sum()),
        omni_feasible_points=omni_count, directional_feasible_points=directional_count,
        sampled_cells=sampled_cells, forced_negative_uses=forced_used,
        particles=len(positions), degraded=not bool(len(positions)),
        empty_conservative_region=not bool(len(grid.cells)),
        posterior_directional=float(probabilities[directional].sum()) if len(positions) else None,
        posterior_is_approximate=True,
    )
    result = Belief(grid.cells, grid.polygons, grid.areas, positions, probabilities,
                    radii, orientations, directional, float(grid.areas.sum()), diagnostics,
                    channel.revision, signature)
    channel.belief = result
    return result


def possible_signal(channel, point, cfg) -> bool:
    """Safe coarse no-signal certificate; uncertainty returns True.

    Never infer impossibility from particles.  Only use a lower bound on
    distance to the entire conservative positive-feedback outer polygon.
    """
    if channel.status in ('CLEARED', 'ABSENT'):
        return False
    if not len(channel.polygon):
        return True
    lo = np.min(channel.polygon, axis=0)
    hi = np.max(channel.polygon, axis=0)
    return distance_bounds((*lo, *hi), point)[0] <= cfg.recv_max + cfg.geometry_eps
