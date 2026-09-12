import math
from .model import World, Channel, Observation, Status, InconsistentState
from .geometry import outer_disk, after_measure


def initial_world(cfg):
    disk = outer_disk((0.,0.),cfg.domain_radius,cfg.circle_sides)
    return World({c:Channel(c,disk.copy()) for c in range(1,21)})


def validate_response(op, result):
    if result.get("accepted") is not True:
        raise ValueError("Unaccepted response cannot update state")
    t = result.get("virtual_time_s")
    if isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t):
        raise ValueError("Missing/invalid virtual time")
    if op is None:
        return
    key = "measure_result" if op.kind == "measure" else "clear_result"
    allowed = ("no_signal","near","direction") if op.kind == "measure" else ("success","no_target_in_range")
    if result.get(key) not in allowed:
        raise ValueError("Unknown result code")
    if result.get(key) == "direction":
        b = result.get("svd_deg")
        if isinstance(b,bool) or not isinstance(b,(int,float)) or not math.isfinite(b) or not 0 <= b < 360:
            raise ValueError("Invalid bearing")


def commit(world, coverage, cfg, request_id, p, op, response):
    validate_response(op,response)
    if request_id in world.committed_ids:
        return False
    if response["virtual_time_s"] < world.virtual_time-1e-8:
        raise InconsistentState("Server virtual clock moved backwards")
    ch = world.channels[op.channel]
    if ch.status in (Status.CLEARED,Status.ABSENT):
        raise InconsistentState("Action targeted an already resolved channel")
    result = response["measure_result"] if op.kind == "measure" else response["clear_result"]
    obs = Observation(p,result,response.get("svd_deg"))
    if op.kind == "measure":
        if p not in ch.measured:
            ch.history.append(obs)
            ch.measured.add(p)
            updated = after_measure(ch.polygon,p,result,obs.bearing,cfg)
            if len(updated):
                ch.polygon = updated
            else:
                # Keep the old conservative region, never reward an empty polygon.
                ch.geometry_failures += 1
            if result == "no_signal":
                coverage.record_no_signal(ch.channel,p)
            else:
                if ch.status == Status.UNKNOWN:
                    ch.discovered_at = response["virtual_time_s"]
                ch.status = Status.KNOWN
                if result == "near":
                    ch.near_point = p
            if ch.status == Status.UNKNOWN and coverage.absent_certificate(ch.channel):
                ch.status = Status.ABSENT
            ch.revision += 1
            ch.circle_cache = None
        world.radio = op.channel
    else:
        if result == "success":
            ch.status = Status.CLEARED
            ch.cleared_at = response["virtual_time_s"]
            ch.near_point = None
        else:
            if ch.near_point == p:
                raise InconsistentState("Clear failed at a certified near point")
            ch.failed_clear.add(p)
            ch.history.append(obs)
        ch.revision += 1
    world.position = p
    world.virtual_time = float(response["virtual_time_s"])
    world.actions += 1
    world.committed_ids.add(request_id)
    return True
