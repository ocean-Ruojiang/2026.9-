"""Online execution reads observations only; never imports simulator truth."""
from dataclasses import asdict, replace
import os
from pathlib import Path
import time
import traceback
from .client import Client
from .config import Config, point
from .coverage import Coverage
from .journal import Journal, write_json
from .state import initial_world, commit, validate_response


def run(config_path=None,output=None,base_url=None,robot_id=None):
    start=time.monotonic()
    cpu_start=time.process_time()
    cfg=Config.load(config_path)
    folder=Path(output) if output else Path(os.environ.get('SIM_RUN_DIR','results/online'))/'strategy3'
    log=Journal(folder)
    write_json(folder/'config.json',asdict(cfg))
    world=initial_world(cfg)
    coverage=None
    scheduler=None
    planning=0.
    inference_seconds=0.
    entered=False
    exited=False
    reason='initializing'
    error=None
    client=Client(base_url or os.environ.get('SIM_BASE_URL','http://127.0.0.1:2027'),
                  robot_id or os.environ.get('SIM_ROBOT_ID','local'),cfg,log)
    try:
        from .beliefs import rebuild
        from .scheduler import Scheduler
        coverage=Coverage(cfg)
        scheduler=Scheduler(cfg,coverage)
        _,response=client.call('/enter')
        validate_response(None,response)
        entered=True
        world.virtual_time=float(response['virtual_time_s'])
        remaining=min(cfg.real_limit_s,float(response.get('remaining_real_duration_s',cfg.real_limit_s)))
        deadline=time.monotonic()+remaining-cfg.exit_margin_s
        client.deadline=deadline
        log('entered',config_digest=cfg.digest(),coverage=coverage.summary(),remaining_real_s=remaining)
        reason='running'
        while not world.complete():
            if time.monotonic()>=deadline:
                reason='strategy_real_limit'
                break
            if world.actions>=cfg.max_actions:
                reason='strategy_action_limit'
                break
            t=time.monotonic()
            action=scheduler.choose(world)
            planning+=time.monotonic()-t
            if action is None:
                reason='no_action_with_unresolved_channels'
                break
            action=replace(action,point=point(action.point,cfg))
            log('decision',virtual_time_s=world.virtual_time,position=world.position,action=action,
                phase=coverage.stage)
            rid,response=client.call('/'+action.kind,action)
            t=time.monotonic()
            committed=commit(world,coverage,cfg,rid,action,response)
            if committed:
                channel=world.channels[action.channel]
                if channel.status=='KNOWN':
                    ti=time.monotonic()
                    belief=rebuild(channel,cfg)
                    inference_seconds+=time.monotonic()-ti
                    log('inference',channel=channel.channel,revision=channel.revision,
                        retained_area_m2=belief.area,diagnostics=belief.diagnostics)
                scheduler.observe(world,action,response)
                log('state',virtual_time_s=world.virtual_time,actions=world.actions,
                    channel=channel.channel,status=channel.status,
                    history_count=len(channel.history),failed_clears=len(channel.failed_clear),
                    known_count=len(world.known()),unknown_count=len(world.unknown()))
            planning+=time.monotonic()-t
        if world.complete():
            reason='complete'
    except Exception as exc:
        reason='error'
        error=f'{type(exc).__name__}: {exc}'
        log('error',error=error,traceback=traceback.format_exc())
        print(traceback.format_exc(),flush=True)
    finally:
        if entered and client.pending is None:
            try:
                client.deadline=time.monotonic()+min(cfg.exit_margin_s,cfg.http_timeout_s)
                _,response=client.call('/exit')
                validate_response(None,response)
                exited=True
                world.virtual_time=float(response['virtual_time_s'])
            except Exception as exc:
                error=(error+'; ' if error else '')+f'exit: {type(exc).__name__}: {exc}'
                log('exit_error',error=str(exc))
        complete=world.complete() and exited and reason=='complete'
        summary=dict(schema_version=1,policy='policy3',complete=complete,reason=reason,error=error,
            config_digest=cfg.digest(),virtual_time_s=world.virtual_time,
            real_runtime_s=time.monotonic()-start,strategy_cpu_time_s=time.process_time()-cpu_start,
            planning_time_s=planning,inference_time_s=inference_seconds,actions=world.actions,
            cleared_count=sum(c.status=='CLEARED' for c in world.channels.values()),
            discovered_count=sum(c.discovered_at is not None for c in world.channels.values()),
            channels={str(c.channel):dict(status=c.status,history_count=len(c.history),
                positive_count=sum(o.result!='no_signal' for o in c.history),
                negative_count=sum(o.result=='no_signal' for o in c.history),
                failed_clear_count=len(c.failed_clear),primary_actions=c.primary_actions,
                geometry_failures=c.geometry_failures,discovered_at=c.discovered_at,cleared_at=c.cleared_at,
                fallback_index=c.fallback_index) for c in world.channels.values()},
            coverage=None if coverage is None else coverage.summary(),
            scheduler=None if scheduler is None else scheduler.summary(),
            timing_note='CPU is process_time; real runtime includes initialization, planning, logging and HTTP; planning includes inference/state bookkeeping.')
        write_json(folder/'summary.json',summary)
        log('finished',summary=summary)
        log.close()
        print(f"policy3 complete={complete} cleared={summary['cleared_count']} virtual={world.virtual_time:.3f}s wall={summary['real_runtime_s']:.3f}s cpu={summary['strategy_cpu_time_s']:.3f}s",flush=True)
    return 0 if complete else 2
