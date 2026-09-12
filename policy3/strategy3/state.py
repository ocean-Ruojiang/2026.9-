"""Transactional observation updates: accepted responses only, one commit per ID."""
import math
from .geometry import outer_disk, after_measure
from .model import Channel, World, Observation, InconsistentState


def initial_world(cfg):
    disk=outer_disk((0.,0.),cfg.domain_radius,cfg.circle_sides)
    return World({c:Channel(c,disk.copy()) for c in range(1,21)})


def validate_response(action,response):
    if response.get('accepted') is not True:
        raise ValueError('Only accepted responses may update strategy state')
    t=response.get('virtual_time_s')
    if isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) or t<0:
        raise ValueError('Invalid virtual clock')
    if action is None:
        return
    key='measure_result' if action.kind=='measure' else 'clear_result'
    allowed=('no_signal','near','direction') if action.kind=='measure' else ('success','no_target_in_range')
    if response.get(key) not in allowed:
        raise ValueError('Invalid operation result')
    if response.get(key)=='direction':
        b=response.get('svd_deg')
        if isinstance(b,bool) or not isinstance(b,(int,float)) or not math.isfinite(b) or not 0<=b<360:
            raise ValueError('Invalid bearing')


def commit(world,coverage,cfg,request_id,action,response):
    validate_response(action,response)
    if request_id in world.committed_ids:
        return False
    if response['virtual_time_s']<world.virtual_time-1e-7:
        raise InconsistentState('Virtual clock moved backwards')
    channel=world.channels[action.channel]
    if channel.status in ('CLEARED','ABSENT'):
        raise InconsistentState('An action targeted an already resolved channel')
    was_known=channel.status=='KNOWN'
    p=tuple(action.point)
    station_id=action.details.get('station_id')
    if station_id is not None:
        if action.kind!='measure' or station_id not in coverage.by_id:
            raise InconsistentState('Invalid station measurement claim')
        if math.dist(p,coverage.by_id[station_id].point)>1e-6:
            raise InconsistentState('Station coverage claimed at a different point')
    if action.kind=='measure':
        code=response['measure_result']
        bearing=response.get('svd_deg')
        previous=next((o for o in channel.history if o.point==p),None)
        if previous is not None and (previous.result!=code or previous.bearing!=bearing):
            raise InconsistentState('Repeated fixed-location measurement contradicted earlier observation')
        if previous is None:
            updated=after_measure(channel.polygon,p,code,bearing,cfg)
            if len(updated):
                channel.polygon=updated
            else:
                # Keep the previous outer bound, record inconsistency, and let
                # inference/fallback report it. Empty geometry is not success.
                channel.geometry_failures+=1
            channel.history.append(Observation(p,code,bearing,request_id))
            channel.measured.add(p)
            channel.revision+=1
            channel.belief=None
            channel.circle_cache=None
        if code=='no_signal':
            coverage.record_no_signal(channel.channel,p)
            if channel.status=='UNKNOWN' and coverage.absent_certificate(channel.channel):
                channel.status='ABSENT'
        else:
            if channel.status=='UNKNOWN':
                channel.discovered_at=float(response['virtual_time_s'])
            channel.status='KNOWN'
            if code=='near':
                channel.near_point=p
        if station_id is not None:
            coverage.mark_measured(station_id,channel.channel)
        else:
            for station in coverage.stations:
                if math.dist(p,station.point)<1e-7:
                    coverage.mark_measured(station.id,channel.channel)
        world.radio=channel.channel
    else:
        if response['clear_result']=='success':
            channel.status='CLEARED'
            channel.cleared_at=float(response['virtual_time_s'])
            channel.near_point=None
        else:
            if channel.near_point is not None and math.dist(p,channel.near_point)<1e-7:
                raise InconsistentState('Clearing failed at an observed near point')
            if p not in channel.failed_clear:
                channel.failed_clear.append(p)
        channel.revision+=1
        channel.belief=None
    if was_known and action.details.get('is_primary',False):
        channel.primary_actions+=1
    world.position=p
    world.virtual_time=float(response['virtual_time_s'])
    world.actions+=1
    world.committed_ids.add(request_id)
    return True
