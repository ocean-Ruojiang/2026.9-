from dataclasses import asdict
from pathlib import Path
import json
import math
import time
from .client import Client, UncertainRequest, RejectedRequest
from .journal import Journal, json_default
from .coverage import Coverage
from .state import initial_world, commit, validate_response
from .model import Status, PlanningDeadline
from .q2 import load_provider
from .gains import Predictor
from .candidates import Candidates
from .templates import Templates
from .scheduler import Scheduler


def run(cfg, transport, robot_id, output):
    """Transport is the only observation source. No truth access in the planner."""
    cfg.validate()
    directory = Path(output)
    directory.mkdir(parents=True,exist_ok=False)
    (directory/"config.json").write_text(json.dumps(asdict(cfg),ensure_ascii=False,indent=2),encoding="utf-8")
    log = Journal(directory/"events.jsonl")
    log("configuration",config=asdict(cfg),digest=cfg.digest(),q2_provider=cfg.q2_provider)
    world = initial_world(cfg)
    coverage = Coverage(cfg)
    client = Client(transport,robot_id,cfg,log)
    deadline = math.inf
    def check():
        if time.monotonic() >= deadline-cfg.exit_margin_s:
            raise PlanningDeadline("Insufficient real time for further planning")
    predictor = Predictor(cfg,coverage,check)
    provider = load_provider(cfg)
    candidates = Candidates(cfg,coverage,provider)
    templates = Templates(cfg,predictor)
    scheduler = Scheduler(cfg,coverage,candidates,templates,predictor,log)
    started = time.monotonic()
    entered = False
    reason,error = "not_started",None
    measurements=failed=success=switch_count=0
    path_length=planning=0.
    virtual_limit=360000.
    try:
        _,response = client.call("/enter")
        validate_response(None,response)
        entered = True
        remaining = response.get("remaining_real_duration_s")
        if not isinstance(remaining,(int,float)) or not math.isfinite(remaining) or remaining < 0:
            raise ValueError("Missing/invalid actual remaining real duration")
        deadline = time.monotonic()+min(cfg.real_limit_s,remaining)
        client.deadline = deadline
        world.virtual_time = float(response["virtual_time_s"])
        virtual_limit = min(360000.,float(response.get("max_virtual_duration_s",360000.)))
        while not world.complete():
            check()
            if world.actions >= cfg.max_actions:
                reason = "action_limit"
                break
            before_plan = time.monotonic()
            try:
                plan = scheduler.next_plan(world)
            finally:
                planning += time.monotonic()-before_plan
            if plan is None:
                break
            for op in plan.ops:
                check()
                if world.complete():
                    break
                ch = world.channels[op.channel]
                if ch.status in (Status.CLEARED,Status.ABSENT):
                    continue
                if op.kind == "measure" and plan.point in ch.measured:
                    continue
                if world.actions >= cfg.max_actions:
                    reason = "action_limit"
                    break
                movement = math.dist(world.position,plan.point)
                upper = movement/cfg.speed+5+(op.kind=="measure" and op.channel!=world.radio)
                if world.virtual_time+upper > virtual_limit:
                    reason = "virtual_timeout"
                    break
                # Station plans are committed blocks. Ordinary plans may be interrupted.
                old,old_time,old_radio = world.position,world.virtual_time,world.radio
                rid,result = client.call("/"+op.kind,plan.point,op)
                if commit(world,coverage,cfg,rid,plan.point,op,result):
                    elapsed = world.virtual_time-old_time
                    scheduler.after_action(world,plan,op,old,elapsed)
                    if not world.unknown() and scheduler.coverage_done_time is None:
                        scheduler.coverage_done_time = world.virtual_time
                    path_length += movement
                    if op.kind == "measure":
                        measurements += 1
                        switch_count += op.channel != old_radio
                    elif result["clear_result"] == "success":
                        success += 1
                    else:
                        failed += 1
                    log("commit",request_id=rid,mode=plan.mode,point=plan.point,
                        op=asdict(op),elapsed=elapsed,virtual_time=world.virtual_time,
                        channel_status=ch.status.value,known=len(world.known()),
                        unknown=len(world.unknown()),cleared=success,
                        geometry_failures=ch.geometry_failures)
                if op.kind == "measure" and result["measure_result"] == "near":
                    break
                if plan.mode in ("optional","station_extra"):
                    if op.kind == "clear" or result.get("measure_result") == "direction":
                        break
            if reason in ("action_limit","virtual_timeout"):
                break
        if world.complete():
            scheduler._close_stage("complete")
            reason = "complete"
        elif reason == "not_started":
            reason = "incomplete"
    except PlanningDeadline as exc:
        reason,error = "real_timeout",str(exc)
    except UncertainRequest as exc:
        reason,error = "communication_uncertain",str(exc)
    except RejectedRequest as exc:
        reason,error = "request_rejected",str(exc)
    except Exception as exc:
        reason,error = "error",f"{type(exc).__name__}: {exc}"
        import traceback
        log("exception",traceback=traceback.format_exc())
    finally:
        # Never issue a fresh action after an ambiguous in-flight operation.
        exit_status = "not_sent"
        if entered and client.pending is None and time.monotonic() < deadline:
            try:
                _,result = client.call("/exit")
                exit_status = result.get("exit_reason","accepted")
            except Exception as exc:
                exit_status = f"unconfirmed: {exc}"
        discovered = [c.discovered_at for c in world.channels.values() if c.discovered_at is not None]
        cleared = sum(c.status==Status.CLEARED for c in world.channels.values())
        posterior_diagnostics = [{
            "channel":c.channel,"degraded":c.posterior_cache.degraded,
            "ess":c.posterior_cache.ess,"proposals":c.posterior_cache.proposals}
            for c in world.channels.values() if c.posterior_cache is not None]
        result = dict(
            strategy="策略2",variant=cfg.name,config_hash=cfg.digest(),q2_provider=cfg.q2_provider,
            reason=reason,error=error,complete_certificate=world.complete(),cleared_count=cleared,
            virtual_total_s=world.virtual_time,avg_clear_s=world.virtual_time/cleared if cleared else None,
            real_runtime_s=time.monotonic()-started,planning_time_s=planning,
            path_length_m=path_length,measure_count=measurements,switch_count=switch_count,
            clear_success=success,clear_failed=failed,actions=world.actions,
            coverage_done_s=scheduler.coverage_done_time,
            last_discovery_s=max(discovered) if discovered else None,
            stages=scheduler.stage_records,active_stage=asdict(scheduler.stage) if scheduler.stage else None,
            grid_fallbacks=scheduler.grid_records,exit_status=exit_status,
            geometry_inconsistent=sum(c.geometry_failures for c in world.channels.values()),
            posterior_diagnostics=posterior_diagnostics,
            channels={str(c.channel):dict(status=c.status.value,discovered_at=c.discovered_at,
                      cleared_at=c.cleared_at,observations=len(c.history))
                      for c in world.channels.values()},
            coverage_evidence=coverage.evidence.tolist(),
            coverage_witnesses={f"{i}:{c}":p for (i,c),p in coverage.witnesses.items()})
        log("summary",**result)
        (directory/"summary.json").write_text(json.dumps(
            result,ensure_ascii=False,indent=2,allow_nan=False,default=json_default),encoding="utf-8")
        log.close()
    return result
