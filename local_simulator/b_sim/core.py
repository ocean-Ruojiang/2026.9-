from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Source:
    channel: int
    x: float
    y: float
    radius: float
    direction: float | None = None


def generate_scenario(seed, count=None, layout="uniform", radius_mode="random", directional_count=0):
    rng = random.Random(seed)
    count = rng.randint(10, 16) if count is None else count
    if not 10 <= count <= 16 or not 0 <= directional_count <= count:
        raise ValueError("Target count must be 10..16; directional count must be 0..count")
    if layout not in {"uniform", "edge", "clustered", "mixed"}:
        raise ValueError("Unknown layout")
    if radius_mode not in {"random", "minimum", "maximum"}:
        raise ValueError("Unknown radius mode")
    channels = rng.sample(range(1, 21), count)
    clusters = []
    for _ in range(3):
        a, r = rng.random() * math.tau, 1400 * math.sqrt(rng.random())
        clusters.append((r * math.cos(a), r * math.sin(a)))
    sources = []
    for i, channel in enumerate(channels):
        kind = rng.choice(["uniform", "edge", "clustered"]) if layout == "mixed" else layout
        if kind == "clustered":
            cx, cy = rng.choice(clusters)
            while True:
                x, y = rng.gauss(cx, 180), rng.gauss(cy, 180)
                if math.hypot(x, y) <= 1800:
                    break
        else:
            a = rng.random() * math.tau
            r = (math.sqrt(rng.uniform(1600**2, 1800**2)) if kind == "edge"
                 else 1800 * math.sqrt(rng.random()))
            x, y = r * math.cos(a), r * math.sin(a)
        radius = {"minimum": 1000., "maximum": 1500.}.get(radius_mode)
        if radius is None:
            radius = rng.uniform(1000, 1500)
        sources.append(asdict(Source(channel, x, y, radius,
                                     rng.uniform(0, 360) if i < directional_count else None)))
    return {"schema_version": 1, "seed": seed, "layout": layout,
            "radius_mode": radius_mode, "sources": sources}


def validate_scenario(scenario):
    sources = [Source(**s) for s in scenario["sources"]]
    if not 10 <= len(sources) <= 16:
        raise ValueError("Scenario must contain 10..16 sources")
    if len({s.channel for s in sources}) != len(sources):
        raise ValueError("Duplicate channels")
    for s in sources:
        if type(s.channel) is not int or not 1 <= s.channel <= 20:
            raise ValueError("Invalid channel")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (s.x, s.y, s.radius)):
            raise ValueError("Invalid source coordinates/radius")
        if math.hypot(s.x, s.y) > 1800 + 1e-9 or not 1000 <= s.radius <= 1500:
            raise ValueError("Source outside physical bounds")
        if s.direction is not None and (isinstance(s.direction, bool)
                or not isinstance(s.direction, (int, float))
                or not math.isfinite(s.direction) or not 0 <= s.direction < 360):
            raise ValueError("Invalid directional orientation")
    return sources


class SessionClosed(ConnectionError):
    pass


def decode_request(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def bad_constant(value):
        raise ValueError("Non-finite JSON constant")

    obj = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant)

    def depth(value):
        if isinstance(value, dict):
            return 1 + max((depth(v) for v in value.values()), default=0)
        if isinstance(value, list):
            return 1 + max((depth(v) for v in value), default=0)
        return 0

    if not isinstance(obj, dict) or depth(obj) > 16:
        raise ValueError("JSON object required; maximum nesting 16")
    return obj


class Simulator:
    def __init__(self, scenario, robot_id="local", noise="smooth", noise_cell_m=20.,
                 real_limit_s=1200., window_limit_s=1500., virtual_limit_s=360000.,
                 clock=time.monotonic):
        if noise not in {"smooth", "hash", "zero"}:
            raise ValueError("Unknown noise model")
        if not self.valid_id(robot_id, 64):
            raise ValueError("Invalid configured robot_id")
        if any(not math.isfinite(v) or v <= 0 for v in
               (noise_cell_m, real_limit_s, window_limit_s, virtual_limit_s)):
            raise ValueError("Limits and noise cell width must be finite and positive")
        self.scenario = copy.deepcopy(scenario)
        self.sources = {s.channel: s for s in validate_scenario(scenario)}
        self.robot_id, self.noise, self.noise_cell_m = robot_id, noise, noise_cell_m
        self.seed = scenario.get("seed", 0)
        self.real_limit_s, self.window_limit_s = real_limit_s, window_limit_s
        self.virtual_limit_us = round(virtual_limit_s * 1_000_000)
        self.clock, self.opened_at = clock, clock()
        self.started_at = self.ended_at = None
        self.end_reason = None
        self.position, self.channel, self.virtual_us = (0., 0.), 1, 0
        self.cleared, self.detected = set(), set()
        self.counts = dict(measure=0, switches=0, clear_success=0, clear_failure=0,
                           direction=0, near=0, no_signal=0)
        self.move_distance = 0.
        self.move_us = 0
        self.cache, self.events = {}, []
        self.lock = threading.Lock()

    @staticmethod
    def valid_id(value, limit):
        try:
            return (isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= limit
                    and not any(unicodedata.category(c).startswith("C") for c in value))
        except UnicodeError:
            return False

    def response(self, accepted, **extra):
        return dict(accepted=accepted, real_timestamp_ms=time.time_ns() // 1_000_000,
                    virtual_time_s=self.virtual_us / 1_000_000 if accepted else 0, **extra)

    def expired(self):
        if self.end_reason is not None:
            return True
        now = self.clock()
        if now >= self.opened_at + self.window_limit_s:
            self.finish("window_timeout")
        elif self.started_at is not None and now >= self.started_at + self.real_limit_s:
            self.finish("real_timeout")
        elif self.virtual_us >= self.virtual_limit_us:
            self.finish("virtual_timeout")
        return self.end_reason is not None

    def finish(self, reason):
        if self.end_reason is None:
            self.end_reason, self.ended_at = reason, self.clock()

    def _hash(self, channel, x, y):
        payload = json.dumps([self.seed, channel, x, y], separators=(",", ":")).encode()
        n = int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
        return 2 * (n / (2**64 - 1)) - 1

    def error(self, channel, point):
        x, y = point
        if self.noise == "zero":
            return 0.
        if self.noise == "hash":
            return self._hash(channel, float(x) + 0., float(y) + 0.)
        x, y = x / self.noise_cell_m, y / self.noise_cell_m
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        # Smooth interpolation: adjacent points have correlated bounded errors.
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        return sum(wx * wy * self._hash(channel, ix + dx, iy + dy)
                   for dx, wx in [(0, 1-fx), (1, fx)]
                   for dy, wy in [(0, 1-fy), (1, fy)])

    def _measure(self, point, channel):
        source = self.sources.get(channel)
        if source is None or channel in self.cleared:
            return {"measure_result": "no_signal"}
        dx, dy = point[0] - source.x, point[1] - source.y
        distance = math.hypot(dx, dy)
        if distance > source.radius:
            return {"measure_result": "no_signal"}
        if source.direction is not None:
            projection = dx * math.cos(math.radians(source.direction)) + dy * math.sin(math.radians(source.direction))
            if projection < -1e-10:
                return {"measure_result": "no_signal"}
        self.detected.add(channel)
        if distance <= 5:
            return {"measure_result": "near"}
        true = math.degrees(math.atan2(-dy, -dx)) % 360
        reading = round((true + self.error(channel, point)) * 100)
        # Keep the reported rounded reading inside the specified +/-1 degree bound.
        reading = max(math.ceil((true - 1) * 100), min(math.floor((true + 1) * 100), reading))
        return {"measure_result": "direction", "svd_deg": (reading % 36000) / 100}

    def _validate(self, path, body):
        common = {"arena_id", "robot_id", "request_id"}
        required = common | ({"position", "channel"} if path in {"/measure", "/clear"} else set())
        if not isinstance(body, dict) or not required <= body.keys():
            return 400
        if not self.valid_id(body["robot_id"], 64) or not self.valid_id(body["request_id"], 128):
            return 400
        if not isinstance(body["arena_id"], str):
            return 400
        if "position" in required:
            p, c = body["position"], body["channel"]
            if not isinstance(p, dict) or not {"x", "y"} <= p.keys():
                return 400
            for v in (p["x"], p["y"]):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > 2_000_000 or not math.isfinite(v):
                    return 400
            if isinstance(c, bool) or not isinstance(c, (int, float)) or not 1 <= c <= 20 or not math.isfinite(c) or int(c) != c:
                return 400
        if body.keys() != required or ("position" in required and body["position"].keys() != {"x", "y"}):
            return 200
        if body["arena_id"] != "default" or body["robot_id"] != self.robot_id:
            return 200
        return None

    def handle(self, path, body):
        if not self.lock.acquire(blocking=False):
            return 409, self.response(False)
        try:
            if path not in {"/enter", "/measure", "/clear", "/exit"}:
                return 404, self.response(False)
            invalid = self._validate(path, body)
            if invalid is not None:
                return invalid, self.response(False)
            request_id = body["request_id"]
            # Accepted requests are replayable even after exit; never execute them twice.
            if request_id in self.cache:
                old_path, old_body, old_response = self.cache[request_id]
                if path == old_path and body == old_body:
                    return 200, copy.deepcopy(old_response)
                return 409, self.response(False)
            if self.expired():
                raise SessionClosed(self.end_reason)
            if path == "/enter":
                if self.started_at is not None:
                    return 200, self.response(False)
                self.started_at = self.clock()
                remaining = min(self.real_limit_s, self.opened_at + self.window_limit_s - self.started_at)
                result = self.response(True, max_virtual_duration_s=self.virtual_limit_us / 1_000_000,
                                       max_real_duration_s=self.real_limit_s,
                                       remaining_real_duration_s=max(0, math.floor(remaining)))
            elif self.started_at is None:
                return 200, self.response(False)
            elif path == "/exit":
                self.finish("user_exit")
                result = self.response(True, exit_reason="user_exit")
            else:
                point = (float(body["position"]["x"]), float(body["position"]["y"]))
                channel = int(body["channel"])
                distance = math.dist(self.position, point)
                move_us = round(distance / 5 * 1_000_000)
                self.move_distance += distance
                self.move_us += move_us
                self.virtual_us += move_us
                self.position = point
                if path == "/measure":
                    switched = int(channel != self.channel)
                    self.channel = channel
                    self.virtual_us += (5 + switched) * 1_000_000
                    self.counts["measure"] += 1
                    self.counts["switches"] += switched
                    data = self._measure(point, channel)
                    self.counts[data["measure_result"]] += 1
                else:
                    source = self.sources.get(channel)
                    success = (source is not None and channel not in self.cleared
                               and math.dist(point, (source.x, source.y)) <= 20)
                    if success:
                        self.cleared.add(channel)
                    self.counts["clear_success" if success else "clear_failure"] += 1
                    self.virtual_us += (5 if success else 3) * 1_000_000
                    data = {"clear_result": "success" if success else "no_target_in_range"}
                result = self.response(True, **data)
                if self.virtual_us >= self.virtual_limit_us:
                    self.finish("virtual_timeout")
            self.cache[request_id] = path, copy.deepcopy(body), copy.deepcopy(result)
            self.events.append({"index": len(self.events), "path": path,
                                "request": copy.deepcopy(body), "response": copy.deepcopy(result),
                                "position": self.position, "receiver_channel": self.channel})
            return 200, result
        finally:
            self.lock.release()

    def summary(self):
        now = self.ended_at if self.ended_at is not None else self.clock()
        cleared = len(self.cleared)
        return dict(seed=self.seed, target_count=len(self.sources), detected_count=len(self.detected),
                    cleared_count=cleared, clearance_ratio=cleared / len(self.sources),
                    all_cleared=cleared == len(self.sources),
                    virtual_time_s=self.virtual_us / 1_000_000,
                    average_time_per_cleared_s=self.virtual_us / 1_000_000 / cleared if cleared else None,
                    real_time_s=now-self.started_at if self.started_at is not None else 0.,
                    move_distance_m=self.move_distance, move_time_s=self.move_us / 1_000_000,
                    end_reason=self.end_reason, actions=len(self.events),
                    cleared_channels=sorted(self.cleared),
                    remaining_channels=sorted(set(self.sources)-self.cleared), **self.counts)
