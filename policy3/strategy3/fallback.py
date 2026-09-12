"""Persistent finite clearing cover of the conservative positive polygon."""
import math
import numpy as np
from .config import point
from .geometry import cell_intersects
from .model import InconsistentState


def build_points(channel, cfg, start):
    h = cfg.fallback_spacing_m
    if h/math.sqrt(2)>=cfg.clear_radius:
        raise ValueError('A fallback cell must fit strictly within clearing radius')
    poly = np.asarray(channel.polygon)
    if not len(poly):
        raise InconsistentState('A discovered channel has no conservative positive region')
    low, high = np.floor(poly.min(axis=0)/h).astype(int), np.floor(poly.max(axis=0)/h).astype(int)
    points = []
    for j in range(low[1], high[1]+1):
        columns = range(low[0], high[0]+1) if (j-low[1])%2 == 0 else range(high[0], low[0]-1, -1)
        for i in columns:
            p = point(((i+.5)*h, (j+.5)*h), cfg)
            if cell_intersects(poly, p, h/2):
                points.append(p)
    # Greedy order changes travel only, never the finite coverage set.
    left = np.asarray(points, dtype=float).reshape((-1, 2))
    route, here = [], np.asarray(start)
    while len(left):
        index = int(np.argmin(np.sum((left-here)**2, axis=1)))
        here = left[index]
        route.append(tuple(map(float, here)))
        left = np.delete(left, index, axis=0)
    return route


def next_point(channel, cfg, current):
    if channel.fallback_points is None:
        channel.fallback_points = build_points(channel, cfg, current)
        channel.fallback_index = 0
    half = cfg.fallback_spacing_m/2
    corners = np.array([[-half, -half], [-half, half], [half, -half], [half, half]])
    while channel.fallback_index<len(channel.fallback_points):
        p = channel.fallback_points[channel.fallback_index]
        # A center rejection is insufficient; every skipped cell needs a full-cell reason.
        outside = not cell_intersects(channel.polygon, p, half)
        already_excluded = any(np.max(np.linalg.norm(corners+np.asarray(p)-q, axis=1))
            <=cfg.clear_radius-cfg.geometry_eps for q in channel.failed_clear)
        if outside or already_excluded:
            channel.fallback_index += 1
            continue
        return p, channel.fallback_index
    raise InconsistentState('Finite conservative clearing cover exhausted without finding known source')
