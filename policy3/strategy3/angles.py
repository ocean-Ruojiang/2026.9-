"""Circular interval operations; endpoint-only feasible bearings are preserved.

All angles are radians.  Hard feasibility uses intervals, never angular bins.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

TAU = 2.0 * math.pi


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float
    left_closed: bool = True
    right_closed: bool = True

    @property
    def width(self) -> float:
        return max(0.0, self.hi - self.lo)


def full_circle() -> list[Interval]:
    return [Interval(0.0, TAU)]


def arc(center: float, half_width: float, closed: bool = True) -> list[Interval]:
    """Return an arc on [0, 2*pi], splitting correctly at the circular seam."""
    if half_width >= math.pi:
        return full_circle()
    center %= TAU
    lo, hi = center - half_width, center + half_width
    if lo < 0.0:
        return [Interval(0.0, hi, True, closed),
                Interval(lo + TAU, TAU, closed, True)]
    if hi > TAU:
        return [Interval(0.0, hi - TAU, True, closed),
                Interval(lo, TAU, closed, True)]
    result = [Interval(lo, hi, closed, closed)]
    # Zero and 2*pi represent the same physical orientation.  Retain both
    # representations for exact endpoint intersections at the seam.
    if closed and lo == 0.0:
        result.append(Interval(TAU, TAU))
    if closed and hi == TAU:
        result.append(Interval(0.0, 0.0))
    return result


def intersect(left: list[Interval], right: list[Interval]) -> list[Interval]:
    result = []
    for a in left:
        for b in right:
            lo, hi = max(a.lo, b.lo), min(a.hi, b.hi)
            if lo > hi:
                continue
            lc = (a.left_closed if lo == a.lo else True) and (
                b.left_closed if lo == b.lo else True)
            rc = (a.right_closed if hi == a.hi else True) and (
                b.right_closed if hi == b.hi else True)
            if lo < hi or (lc and rc):
                result.append(Interval(lo, hi, lc, rc))
    # Duplicate endpoint arcs are harmless mathematically but would distort
    # diagnostic counts and fallback sampling.
    return sorted(set(result), key=lambda v: (v.lo, v.hi))


def contains(intervals: list[Interval], angle: float) -> bool:
    a = angle % TAU
    probes = (a, TAU) if a == 0.0 else (a,)
    return any((a > i.lo or (a == i.lo and i.left_closed)) and
               (a < i.hi or (a == i.hi and i.right_closed))
               for a in probes for i in intervals)


def total_width(intervals: list[Interval]) -> float:
    return sum(i.width for i in intervals)


def sample(intervals: list[Interval], count: int) -> list[float]:
    """Deterministic stratified interior samples, including isolated solutions."""
    if not intervals or count <= 0:
        return []
    positive = [i for i in intervals if i.width > 0.0]
    length = total_width(positive)
    if length == 0.0:
        return list(dict.fromkeys(i.lo % TAU for i in intervals))[:count]
    positions = [(k + 0.5) * length / count for k in range(count)]
    out = []
    for value in positions:
        offset = 0.0
        for i in positive:
            if value <= offset + i.width:
                a = i.lo + value - offset
                if a <= i.lo and not i.left_closed:
                    a = math.nextafter(i.lo, i.hi)
                if a >= i.hi and not i.right_closed:
                    a = math.nextafter(i.hi, i.lo)
                out.append(a % TAU)
                break
            offset += i.width
    return out
