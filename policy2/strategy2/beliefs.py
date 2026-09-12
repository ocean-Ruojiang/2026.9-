"""History-conditioned joint samples (x,y,rho); never a completion certificate."""
from dataclasses import dataclass
import math
import numpy as np
from .geometry import sample_polygon


@dataclass
class Posterior:
    particles: np.ndarray
    scenarios: np.ndarray
    errors: np.ndarray
    ess: float
    proposals: int
    degraded: bool


def posterior(channel, cfg):
    if channel.posterior_revision == channel.revision:
        return channel.posterior_cache
    rng = np.random.default_rng(np.random.SeedSequence(
        [cfg.algorithm_seed, channel.channel, channel.revision]))
    accepted, log_weights = [], []
    proposal_count = 0
    batch = max(256, cfg.particles * 2)
    limit = cfg.particles * cfg.posterior_proposal_multiplier
    while proposal_count < limit:
        xy = sample_polygon(channel.polygon, min(batch, limit-proposal_count), rng)
        if not len(xy):
            break
        proposal_count += len(xy)
        valid = np.linalg.norm(xy, axis=1) <= cfg.domain_radius
        low = np.full(len(xy), cfg.recv_min)
        high = np.full(len(xy), cfg.recv_max)
        lw = np.zeros(len(xy))
        for obs in channel.history:
            rel = xy - obs.point
            d = np.linalg.norm(rel, axis=1)
            if obs.result == "no_signal":
                high = np.minimum(high, d)
            elif obs.result == "no_target_in_range":
                valid &= d > cfg.clear_radius
            elif obs.result in ("direction", "near"):
                low = np.maximum(low, d)
                if obs.result == "near":
                    valid &= d <= cfg.near_radius
                else:
                    valid &= d > cfg.near_radius
                    theta = np.degrees(np.arctan2(rel[:,1], rel[:,0]))
                    delta = (obs.bearing-theta+180) % 360 - 180
                    half = cfg.reading_step_deg / 2
                    width = np.maximum(0, np.minimum(cfg.error_deg, delta+half) -
                                       np.maximum(-cfg.error_deg, delta-half))
                    valid &= width > 0
                    lw += np.log(np.maximum(width/(2*cfg.error_deg), 1e-300))
        width = high-low
        valid &= width > 0
        lw += np.log(np.maximum(width/(cfg.recv_max-cfg.recv_min), 1e-300))
        idx = np.flatnonzero(valid)
        if len(idx):
            rho = low[idx] + (high[idx]-low[idx]) * rng.uniform(1e-12, 1-1e-12, len(idx))
            accepted.append(np.column_stack((xy[idx], rho)))
            log_weights.append(lw[idx])
        if sum(len(x) for x in accepted) >= cfg.particles * 2:
            break
    if not accepted:
        result = Posterior(np.empty((0,3)), np.empty((0,3)), np.empty(0),
                           0., proposal_count, True)
    else:
        rows = np.concatenate(accepted)
        logs = np.concatenate(log_weights)
        weights = np.exp(logs - logs.max())
        weights /= weights.sum()
        ess = float(1 / np.sum(weights**2))
        quantiles = (np.arange(cfg.particles) + rng.random()) / cfg.particles
        indices = np.searchsorted(np.cumsum(weights), quantiles, side="right")
        particles = rows[np.minimum(indices, len(rows)-1)]
        # Paired scenarios and error quantiles are reused across all candidate points.
        idx = np.floor((np.arange(cfg.scenarios)+.5)*cfg.particles/cfg.scenarios).astype(int)
        errors = ((np.arange(cfg.scenarios)+.5)/cfg.scenarios*2-1)*cfg.error_deg
        rng.shuffle(errors)
        result = Posterior(particles, particles[idx], errors, ess,
                           proposal_count, len(rows) < 8 or ess < 4)
    channel.posterior_cache = result
    channel.posterior_revision = channel.revision
    return result


def predict_measure(row, error, point, cfg):
    d = math.dist(row[:2], point)
    if d > row[2]:
        return "no_signal", None
    if d <= cfg.near_radius:
        return "near", None
    bearing = math.degrees(math.atan2(row[1]-point[1], row[0]-point[0])) + error
    # Decimal rounding convention, normalize a rounded 360 back to 0.
    bearing = (math.floor((bearing % 360) / cfg.reading_step_deg + .5) *
               cfg.reading_step_deg) % 360
    return "direction", bearing
