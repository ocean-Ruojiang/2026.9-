"""Conservative convex geometry. Negative observations stay in history."""
import math
import random
import numpy as np


def outer_disk(center, radius, sides):
    a = (np.arange(sides) + .5) * (2 * math.pi / sides)
    return np.asarray(center) + radius / math.cos(math.pi / sides) * np.column_stack(
        (np.cos(a), np.sin(a)))


def clip(poly, normal, bound, eps=0.):
    if len(poly) == 0:
        return poly.copy()
    values = poly @ np.asarray(normal) - (bound + eps)
    inside = values <= 0
    if inside.all():
        return poly.copy()
    if not inside.any():
        return np.empty((0, 2))
    result = []
    for i in range(len(poly)):
        j = (i - 1) % len(poly)
        if inside[i] != inside[j]:
            t = values[j] / (values[j] - values[i])
            result.append(poly[j] + t * (poly[i] - poly[j]))
        if inside[i]:
            result.append(poly[i])
    return np.asarray(result, dtype=float).reshape((-1, 2))


def clip_disk(poly, center, radius, cfg):
    if len(poly) == 0 or np.max(np.linalg.norm(poly - center, axis=1)) <= radius:
        return poly.copy()
    out = poly.copy()
    for a in np.arange(cfg.circle_sides) * (2 * math.pi / cfg.circle_sides):
        n = (math.cos(a), math.sin(a))
        out = clip(out, n, np.dot(n, center) + radius, cfg.geometry_eps)
        if not len(out):
            break
    return out


def after_measure(poly, point, result, bearing, cfg):
    if result == "no_signal":
        return poly.copy()
    if result == "near":
        return clip_disk(poly, point, cfg.near_radius, cfg)
    if result != "direction" or bearing is None:
        raise ValueError("Invalid measurement")
    delta = cfg.error_deg + cfg.reading_step_deg / 2 + cfg.angle_eps
    lo, hi = map(math.radians, (bearing - delta, bearing + delta))
    n1 = (math.sin(lo), -math.cos(lo))
    n2 = (-math.sin(hi), math.cos(hi))
    out = clip(poly, n1, np.dot(n1, point), cfg.geometry_eps)
    out = clip(out, n2, np.dot(n2, point), cfg.geometry_eps)
    return clip_disk(out, point, cfg.recv_max, cfg)


def diameter(poly):
    if not len(poly):
        raise ValueError("Empty polygon has no valid diameter")
    return float(np.max(np.linalg.norm(poly[:, None] - poly[None, :], axis=2)))


def _through(a, b, c):
    # Work relative to a to reduce cancellation on small polygons far from origin.
    u, v = b - a, c - a
    cross = u[0] * v[1] - u[1] * v[0]
    if abs(cross) < 1e-14:
        pairs = [(a, b), (a, c), (b, c)]
        x, y = max(pairs, key=lambda p: np.linalg.norm(p[0] - p[1]))
        center = (x + y) / 2
        return center, max(np.linalg.norm(z - center) for z in (a, b, c))
    center = a + np.array([
        np.dot(u, u) * v[1] - np.dot(v, v) * u[1],
        u[0] * np.dot(v, v) - v[0] * np.dot(u, u)]) / (2 * cross)
    return center, float(np.linalg.norm(center - a))


def enclosing_circle(poly, eps=1e-5):
    if len(poly) == 0:
        raise ValueError("Empty polygon cannot certify localization")
    pts = list(np.asarray(poly))
    random.Random(1729).shuffle(pts)
    center, radius = pts[0].copy(), 0.
    for i, a in enumerate(pts):
        if np.linalg.norm(a - center) <= radius + 1e-9:
            continue
        center, radius = a.copy(), 0.
        for j, b in enumerate(pts[:i]):
            if np.linalg.norm(b - center) <= radius + 1e-9:
                continue
            center, radius = (a + b) / 2, float(np.linalg.norm(a - b) / 2)
            for c in pts[:j]:
                if np.linalg.norm(c - center) > radius + 1e-9:
                    center, radius = _through(a, b, c)
    # The certificate always uses all vertices, irrespective of numeric path.
    radius = float(np.max(np.linalg.norm(poly - center, axis=1))) + eps
    return tuple(map(float, center)), radius


def channel_circle(channel, cfg):
    if channel.circle_cache is None:
        channel.circle_cache = enclosing_circle(channel.polygon, cfg.geometry_eps)
    return channel.circle_cache


def guaranteed(poly, point, cfg):
    return bool(len(poly) and np.max(np.linalg.norm(poly - point, axis=1))
                <= cfg.clear_radius - cfg.geometry_eps)


def contains(poly, points, eps=1e-5):
    pts = np.atleast_2d(points)
    edges = np.roll(poly, -1, axis=0) - poly
    rel = pts[:, None, :] - poly[None, :, :]
    return np.all(edges[None, :, 0] * rel[:, :, 1] -
                  edges[None, :, 1] * rel[:, :, 0] >= -eps, axis=1)


def sample_polygon(poly, n, rng):
    if len(poly) < 3:
        return np.empty((0, 2))
    b, c = poly[1:-1], poly[2:]
    area = np.abs((b[:, 0]-poly[0, 0])*(c[:, 1]-poly[0, 1]) -
                  (b[:, 1]-poly[0, 1])*(c[:, 0]-poly[0, 0]))
    if area.sum() <= 1e-18:
        return np.empty((0, 2))
    idx = rng.choice(len(b), size=n, p=area / area.sum())
    u, v = np.sqrt(rng.random(n)), rng.random(n)
    return ((1-u[:, None]) * poly[0] +
            (u*(1-v))[:, None] * b[idx] + (u*v)[:, None] * c[idx])


def cell_intersects(poly, center, half):
    # Separating axis theorem for convex polygon vs an axis-aligned square.
    q = np.asarray(center)
    if np.any(poly.max(axis=0) < q-half) or np.any(poly.min(axis=0) > q+half):
        return False
    edges = np.roll(poly, -1, axis=0) - poly
    normals = np.column_stack((edges[:, 1], -edges[:, 0]))
    bounds = np.einsum("ij,ij->i", normals, poly)
    lowest_square = normals @ q - half * np.abs(normals).sum(axis=1)
    return not np.any(lowest_square > bounds + 1e-5)
