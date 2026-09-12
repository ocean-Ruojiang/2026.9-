from dataclasses import asdict, replace
import math
from .model import Plan, Op, Status, InconsistentState
from .config import point
from .geometry import channel_circle, guaranteed
from .gains import worst_service
from .templates import ordered
from .budget import Stage
from .fallback import ClearGrid
from .candidates import Candidate


class Scheduler:
    def __init__(self, cfg, coverage, candidates, templates, predictor, log):
        self.cfg, self.coverage, self.candidates = cfg, coverage, candidates
        self.templates, self.predictor, self.log = templates, predictor, log
        self.phase = "initial"
        self.stage = None
        self.locked = None
        self.finish_count = 0
        self.finish_extra = 0.
        self.grid = None
        self.stage_records = []
        self.grid_records = []
        self.coverage_done_time = None
        self.pending_finish_extras = []

    def _best(self, world, plans):
        scored = [(self.predictor.evaluate(world,p),p) for p in plans]
        if not scored:
            return None
        value, plan = max(scored, key=lambda pair: (
            pair[0].score, -pair[0].time, pair[0].gain.clear))
        self.log("decision", phase=self.phase, stage=self.stage.station if self.stage else None,
                 locked=self.locked, plan=asdict(plan), evaluation=asdict(value),
                 alternatives=len(scored), remaining=self.stage.remaining if self.stage else None)
        return plan

    def _close_stage(self, reason):
        if self.stage:
            record = {**asdict(self.stage), "reason":reason}
            self.stage_records.append(record)
            self.log("stage_end", **record)
            self.stage = None

    def _station_plan(self, world, i, p):
        ops = ordered([Op("measure",c) for c in self.coverage.needed(world,i)], world.radio)
        return Plan(p,ops,"mandatory_station","station")

    def _open_stage(self, world):
        route = self.coverage.route(world)
        if not route:
            raise InconsistentState("Unknown channels but no pending coverage station")
        i = route[0]
        options = []
        for p in self.candidates.station(world,i):
            self.predictor.check()
            base = self._station_plan(world,i,p)
            known, _ = self.templates.pool(world,p,[o.channel for o in base.ops])
            base_cost = worst_service(base,world.radio)
            def allowed(plan):
                # Include changed switch costs, not just the number of added ops.
                return (worst_service(plan,world.radio)-base_cost <= self.cfg.detour_budget_s)
            # Mandatory scan block keeps its order; append known operations AFTER it.
            plan = base
            score = self.predictor.evaluate(world,plan).score
            for _ in range(self.cfg.max_ops_per_template-len(base.ops)):
                used = {o.channel for o in plan.ops}
                trials = []
                for op in known:
                    if op.channel in used:
                        continue
                    trial = replace(plan,ops=plan.ops+(op,))
                    if allowed(trial):
                        v = self.predictor.evaluate(world,trial).score
                        if v > score+self.cfg.score_eps:
                            trials.append((v,trial))
                if not trials:
                    break
                score, plan = max(trials,key=lambda x:x[0])
            options.append(plan)
        best = self._best(world,options)
        self.stage = Stage(i,best.point,self.cfg.detour_budget_s,self.cfg.detour_budget_s)
        self.log("stage_start", **asdict(self.stage), nominal_route=list(route))

    def _coverage_plan(self, world):
        if self.stage is None:
            self._open_stage(world)
        stage = self.stage
        needed = self.coverage.needed(world,stage.station)
        arrived = math.dist(world.position,stage.point) < 1e-6
        if needed and arrived:
            return self._station_plan(world,stage.station,stage.point)
        if not needed:
            if arrived and stage.remaining >= 3:
                known, _ = self.templates.pool(world,world.position)
                plans = []
                for op in known:
                    candidate = Candidate(world.position,op,"station_extra")
                    plans.extend(self.templates.four(world,candidate,"station_extra",
                                 lambda p:stage.allows(world,p,self.cfg)))
                positive = [p for p in plans if self.predictor.evaluate(world,p).score > self.cfg.score_eps]
                if positive:
                    return self._best(world,positive)
            self._close_stage("tasks_resolved")
            return None
        baseline = self._station_plan(world,stage.station,stage.point)
        plans = [baseline]
        if stage.remaining >= 3:
            for candidate in self.candidates.normal(world):
                self.predictor.check()
                atomic = Plan(candidate.point,(candidate.primary,),candidate.source)
                if not stage.allows(world,atomic,self.cfg):
                    continue
                plans.extend(self.templates.four(world,candidate,"optional",
                             lambda p:stage.allows(world,p,self.cfg)))
        return self._best(world,plans)

    def _finish_plan(self, world):
        if self.locked is not None and world.channels[self.locked].status == Status.CLEARED:
            self.log("target_end",channel=self.locked,primary_ops=self.finish_count,
                     extra_remaining=self.finish_extra)
            self.locked, self.grid = None, None
            self.pending_finish_extras.clear()
        if self.locked is None:
            if not world.known():
                raise InconsistentState("No remaining target but completion certificate missing")
            ch = min(world.known(),key=lambda c:(
                math.dist(world.position,channel_circle(c,self.cfg)[0]),
                c.discovered_at if c.discovered_at is not None else math.inf,c.channel))
            self.locked = ch.channel
            self.finish_count = 0
            self.finish_extra = self.cfg.finish_extra_budget_s
            self.grid = None
            self.log("target_start",channel=ch.channel)
        ch = world.channels[self.locked]
        center, radius = channel_circle(ch,self.cfg)
        for p in (world.position,point(center,self.cfg)):
            if guaranteed(ch.polygon,p,self.cfg):
                return Plan(p,(Op("clear",ch.channel),),"guaranteed","finish",Op("clear",ch.channel))
        # Extras proposed after the previous primary stay at the same position.
        while self.pending_finish_extras and self.grid is None:
            p, op = self.pending_finish_extras.pop(0)
            trial = Plan(p,(op,),"same_site_extra","finish_extra",op)
            if (p == world.position and self.predictor.valid(world,p,op) and
                worst_service(trial,world.radio) <= self.finish_extra and
                self.predictor.evaluate(world,trial).score > self.cfg.score_eps):
                return trial
        if self.grid is None and self.finish_count < self.cfg.finish_primary_limit:
            plans = []
            for candidate in self.candidates.normal(world,self.locked):
                self.predictor.check()
                plans.extend(self.templates.four(
                    world,candidate,"finish",
                    # Primary doesn't consume the extra budget.
                    lambda p: (worst_service(p,world.radio)-
                               worst_service(replace(p,ops=(p.primary,)),world.radio)
                               <= self.finish_extra),
                    primary_first=True))
            positive = [p for p in plans if self.predictor.evaluate(world,p).score > self.cfg.score_eps]
            if positive:
                plan = self._best(world,positive)
                self.pending_finish_extras = [(plan.point,o) for o in plan.ops[1:]]
                return replace(plan,ops=(plan.primary,))
        if self.grid is None:
            self.grid = ClearGrid(ch,self.cfg)
            record = {"channel":ch.channel,"radius":radius,"grid_cells":self.grid.total,
                      "start_time":world.virtual_time,"primary_ops":self.finish_count}
            self.grid_records.append(record)
            self.log("grid_start",**record)
        p = self.grid.next_point(ch)
        if p is None:
            raise InconsistentState("Finite clear grid exhausted without success; inspect geometry")
        return Plan(p,(Op("clear",ch.channel),),"finite_grid","grid",Op("clear",ch.channel))

    def next_plan(self, world):
        for _ in range(30):
            self.predictor.check()
            if world.complete():
                self._close_stage("complete")
                return None
            near = next((c for c in world.known() if c.near_point is not None),None)
            if near:
                return Plan(near.near_point,(Op("clear",near.channel),),"near","near")
            if self.phase == "initial":
                for c in world.channels.values():
                    if (0.,0.) not in c.measured and c.status != Status.CLEARED:
                        return Plan((0.,0.),(Op("measure",c.channel),),"initial","initial")
                self.phase = "coverage"
            if self.phase == "coverage":
                if not world.unknown():
                    self.coverage_done_time = world.virtual_time
                    self._close_stage("all_channels_resolved")
                    self.phase = "finish"
                    self.log("coverage_done",virtual_time=world.virtual_time)
                else:
                    plan = self._coverage_plan(world)
                    if plan:
                        return plan
                    continue
            if self.phase == "finish":
                return self._finish_plan(world)
        raise InconsistentState("Scheduler did not progress through finite internal transitions")

    def after_action(self, world, plan, op, old, elapsed):
        if plan.mode in ("optional","station_extra"):
            if self.stage is None:
                raise InconsistentState("Optional action without active stage")
            cost = self.stage.charge(old,world.position,elapsed,self.cfg)
            self.log("budget_charge",station=self.stage.station,charge=cost,
                     remaining=self.stage.remaining)
        elif plan.mode == "station":
            self.stage.scan_actions += 1
        elif plan.mode == "finish":
            self.finish_count += 1
        elif plan.mode == "finish_extra":
            self.finish_extra -= elapsed
            if self.finish_extra < -1e-5:
                raise InconsistentState("Finish extra budget overrun")
