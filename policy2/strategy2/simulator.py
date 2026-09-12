"""Local research environment, not the official simulator or its random generator.

Truth is accessed only by this transport and by evaluation AFTER the policy ends.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import random


@dataclass
class Target:
    channel: int
    x: float
    y: float
    rho: float
    alive: bool = True


def make_targets(seed, scenario="uniform"):
    rng = random.Random(seed)
    n = rng.randint(10,16)
    channels = rng.sample(range(1,21),n)
    targets = []
    for index,c in enumerate(channels):
        angle = rng.uniform(0,2*math.pi)
        if scenario == "edge":
            radius = rng.uniform(1700,1800)
            rho = 1000. if index % 2 == 0 else rng.uniform(1000,1050)
        elif scenario == "clustered":
            cx,cy = ((1100,300) if index%2 else (-900,-500))
            x,y = cx+rng.gauss(0,80),cy+rng.gauss(0,80)
            targets.append(Target(c,x,y,rng.uniform(1000,1500)))
            continue
        elif scenario in ("uniform","near","smooth"):
            radius = 1800*math.sqrt(rng.random())
            rho = rng.uniform(1000,1500)
        else:
            raise ValueError(f"Unknown local scenario: {scenario}")
        x,y = radius*math.cos(angle),radius*math.sin(angle)
        if scenario == "near" and index == 0:
            x,y = 2.,-1.
        targets.append(Target(c,x,y,rho))
    return targets


class LocalTransport:
    def __init__(self, seed=1, scenario="uniform", targets=None):
        self.seed,self.scenario = seed,scenario
        rows = make_targets(seed,scenario) if targets is None else targets
        self._targets = {t.channel:Target(t.channel,t.x,t.y,t.rho) for t in rows}
        self._cache = {}
        self._bodies = {}
        self._position,self._radio,self._time = (0.,0.),1,0.
        self._entered,self._exited = False,False

    def _error(self, channel,p):
        if self.scenario == "smooth":
            return math.sin(p[0]/240 + p[1]/370 + self.seed + channel)
        raw = f"{self.seed}:{channel}:{float(p[0]).hex()}:{float(p[1]).hex()}".encode()
        integer = int.from_bytes(hashlib.blake2b(raw,digest_size=8).digest(),"big")
        return integer/(2**64-1)*2-1

    def send(self, prepared, timeout=3):
        rid,payload = prepared.request_id,prepared.payload()
        if rid in self._cache:
            if self._bodies[rid] != prepared.body:
                return {"accepted":False,"virtual_time_s":0}
            return json.loads(json.dumps(self._cache[rid]))
        path = prepared.path
        response = {"accepted":True,"virtual_time_s":self._time,"real_timestamp_ms":0}
        if path == "/enter":
            if self._entered:
                return {"accepted":False,"virtual_time_s":0}
            self._entered = True
            response.update(max_virtual_duration_s=360000,max_real_duration_s=1200,
                            remaining_real_duration_s=1200)
        elif not self._entered or self._exited:
            return {"accepted":False,"virtual_time_s":0}
        elif path == "/exit":
            self._exited = True
            response["exit_reason"] = "user_exit"
        elif path in ("/measure","/clear"):
            p = (payload["position"]["x"],payload["position"]["y"])
            c = payload["channel"]
            if (isinstance(c,bool) or not isinstance(c,int) or not 1 <= c <= 20 or
                not all(math.isfinite(x) and abs(x)<=2_000_000 for x in p)):
                return {"accepted":False,"virtual_time_s":0}
            target = self._targets.get(c)
            distance = math.inf if target is None or not target.alive else math.dist(p,(target.x,target.y))
            self._time += math.dist(self._position,p)/5
            self._position = p
            if path == "/measure":
                self._time += 5+(c != self._radio)
                self._radio = c
                if target is None or not target.alive or distance > target.rho:
                    response["measure_result"] = "no_signal"
                elif distance <= 5:
                    response["measure_result"] = "near"
                else:
                    bearing = math.degrees(math.atan2(target.y-p[1],target.x-p[0]))+self._error(c,p)
                    response.update(measure_result="direction",
                                    svd_deg=(math.floor((bearing%360)*100+.5)/100)%360)
            else:
                success = distance <= 20
                self._time += 5 if success else 3
                response["clear_result"] = "success" if success else "no_target_in_range"
                if success:
                    target.alive = False
            response["virtual_time_s"] = self._time
        else:
            raise ValueError("Unknown endpoint")
        self._cache[rid],self._bodies[rid] = response,prepared.body
        return json.loads(json.dumps(response))

    def evaluation_after_run(self):
        return {"true_count":len(self._targets),
                "true_cleared":sum(not t.alive for t in self._targets.values()),
                "environment_seed":self.seed,"local_scenario":self.scenario}
