"""Layered coverage, bounded local gain optimization and interruptible outer patrol."""
from dataclasses import asdict, replace
import math
from .candidates import Candidates
from .fallback import next_point as fallback_next
from .gains import Predictor, worst_service
from .geometry import channel_circle
from .model import Action, InconsistentState
from .templates import Template, four


class Scheduler:
    def __init__(self, cfg, coverage):
        self.cfg, self.coverage = cfg, coverage
        self.predictor = Predictor(cfg, coverage)
        self.candidates = Candidates(cfg)
        self.phase = 'initial'
        self.locked = None
        self.pending_template = []
        self.station = None
        self.station_budget = cfg.detour_budget_s
        self.finish_budget = getattr(cfg, 'finish_extra_budget_s', 30.)
        self.stage_records = []
        self.target_records = []
        self.template_records = []
        self.decisions = 0
        self.fallback_actions = 0
        self.max_budget_used = 0.

    def _station_mandatory(self, action, world):
        return bool(self.station is not None and action.kind == 'measure'
            and math.dist(action.point, self.station.point)<1e-6
            and world.channels[action.channel].status == 'UNKNOWN'
            and action.channel in self.coverage.pending_channels(self.station, world))

    def _budget_cost(self, world, template):
        if self.station is None:
            return math.inf
        p, anchor = template.actions[0].point, self.station.point
        detour = max(0., (math.dist(world.position, p)+math.dist(p, anchor)
                         -math.dist(world.position, anchor))/self.cfg.speed)
        # Reserve entry and exit switches for each optional operation. This is conservative.
        for action in template.actions:
            if not self._station_mandatory(action, world):
                detour += worst_service(action, action.channel, self.cfg)+2*self.cfg.switch_seconds*(action.kind=='measure')
        return detour

    def _finish_cost(self, template):
        return sum(worst_service(a, a.channel, self.cfg)+2*self.cfg.switch_seconds*(a.kind=='measure')
                   for a in template.actions if a.channel != self.locked)

    def _annotate(self, world, action, template_name, template_score, context):
        details = dict(action.details)
        details.update(phase=self.phase, template=template_name, template_score=template_score,
                       start_position=tuple(world.position), start_virtual_time=world.virtual_time,
                       is_primary=context == 'finish' and action.channel == self.locked)
        if context == 'coverage':
            mandatory = self._station_mandatory(action, world)
            details.update(budget_context='coverage', budget_role='mandatory' if mandatory else 'optional',
                           budget_station=self.station.id, budget_anchor=tuple(self.station.point),
                           budget_remaining_before=self.station_budget)
            if action.kind=='measure' and math.dist(action.point, self.station.point)<1e-6:
                details['station_id'] = self.station.id
        elif context == 'finish':
            details.update(budget_context='finish', budget_role='primary' if action.channel==self.locked else 'extra',
                           budget_remaining_before=self.finish_budget, locked_channel=self.locked)
        value = self.predictor.evaluate(world, (action,))
        details['forecast'] = asdict(value.forecasts[0])
        details['expected_time'] = value.time
        self.decisions += 1
        return replace(action, score=value.score, details=details)

    def _select(self, world, primaries, context, allow_templates=True):
        best = None
        for primary in primaries:
            templates = four(world, primary, self.predictor, self.cfg) if allow_templates else [Template((primary,))]
            for template in templates:
                if context=='coverage' and self._budget_cost(world, template)>self.station_budget+1e-8:
                    continue
                if context=='finish' and self._finish_cost(template)>self.finish_budget+1e-8:
                    continue
                value = self.predictor.evaluate(world, template.actions)
                key = (value.score, -value.time, -len(template.actions), -primary.channel)
                if best is None or key>best[0]:
                    best = (key, template, value)
        if best is None:
            return None
        _, template, value = best
        self.pending_template = [(a, template.name, value.score, context) for a in template.actions[1:]]
        self.template_records.append(dict(phase=self.phase, name=template.name,
            channels=[a.channel for a in template.actions], kinds=[a.kind for a in template.actions],
            expected_score=value.score, expected_time=value.time))
        return self._annotate(world, template.actions[0], template.name, value.score, context)

    def _pending(self, world):
        while self.pending_template:
            action, name, original_score, context = self.pending_template.pop(0)
            if math.dist(world.position, action.point)>1e-6 or not self.predictor.valid(world, action):
                continue
            if context=='finish' and self.locked is None:
                continue
            if context=='coverage' and self._budget_cost(world, Template((action,)))>self.station_budget+1e-8:
                continue
            if context=='finish' and self._finish_cost(Template((action,)))>self.finish_budget+1e-8:
                continue
            f = self.predictor.forecast(world, action)
            if action.kind=='clear' and not f.reliable_clear and f.clear<self.cfg.clear_min_probability:
                continue
            if f.weighted(self.cfg)<=1e-12 and not self._station_mandatory(action, world):
                continue
            return self._annotate(world, action, name, original_score, context)
        return None

    def _lock(self, world, channel, reason):
        self.locked = channel
        self.finish_budget = getattr(self.cfg, 'finish_extra_budget_s', 30.)
        self.pending_template.clear()
        self.target_records.append(dict(channel=channel, reason=reason, start=world.virtual_time,
                                        primary_start=world.channels[channel].primary_actions))

    def _finish(self, world):
        ch = world.channels[self.locked]
        if ch.primary_actions<self.cfg.max_refine_actions and ch.fallback_points is None:
            primaries = self.candidates.actions(world, self.predictor, channel=self.locked)
            action = self._select(world, primaries, 'finish')
            if action is not None:
                return action
        p, index = fallback_next(ch, self.cfg, world.position)
        action = Action(p, 'clear', ch.channel, 'finite_clear_grid', details={'fallback_index': index})
        self.pending_template.clear()
        return self._annotate(world, action, 'finite_clear_grid', 0., 'finish')

    def choose(self, world):
        if world.complete():
            return None
        if self.locked is not None and world.channels[self.locked].status != 'KNOWN':
            # A successful primary clear does not erase the promised other-channel
            # extras. Revalidate them at the same point under the old target's
            # remaining extra budget before releasing this lock.
            pending = self._pending(world)
            if pending is not None:
                return pending
            if self.target_records and self.target_records[-1]['channel']==self.locked:
                self.target_records[-1].update(end=world.virtual_time,
                    primary_end=world.channels[self.locked].primary_actions)
            self.locked = None
            self.pending_template.clear()
        pending = self._pending(world)
        if pending is not None:
            return pending
        if self.locked is not None:
            return self._finish(world)
        station = self.coverage.next_station(world)
        if station is None or station.group == 'E':
            if world.known():
                self.phase = 'inner_cleanup' if station is not None else 'final_cleanup'
                ch = min(world.known(), key=lambda c:(
                    math.dist(world.position, channel_circle(c, self.cfg)[0]), c.channel))
                self._lock(world, ch.channel, self.phase)
                return self._finish(world)
        if station is None:
            if world.complete():
                return None
            raise InconsistentState('Coverage exhausted but an unresolved channel lacks a certificate')
        if self.station is None or self.station.id != station.id:
            if self.station is not None:
                self.stage_records[-1]['remaining'] = self.station_budget
                self.stage_records[-1]['end'] = world.virtual_time
            self.station = station
            self.station_budget = self.cfg.detour_budget_s
            self.stage_records.append(dict(station=station.id, group=station.group,
                start=world.virtual_time, budget=self.station_budget))
        self.phase = 'initial' if station.group=='O' else ('outer_patrol' if station.group=='E' else 'inner_explore')
        pending_channels = self.coverage.pending_channels(station, world)
        if not pending_channels:
            raise InconsistentState('Coverage.next_station returned a resolved station')
        channel = world.radio if world.radio in pending_channels else min(pending_channels)
        primary = Action(tuple(station.point), 'measure', channel, 'coverage_station')
        if not self.predictor.valid(world, primary):
            raise InconsistentState('Coverage scheduled a duplicate or resolved channel observation')
        # Initial 20-channel reconnaissance and outer patrol are predictable mandatory scans.
        if self.phase in ('initial', 'outer_patrol'):
            return self._annotate(world, primary, 'mandatory_station', 0., 'coverage')
        # A mandatory station scan remains the fallback even if its approximate gain is zero.
        primaries = [primary]
        if self.station_budget>=min(self.cfg.clear_failure_seconds, self.cfg.measure_seconds):
            primaries.extend(self.candidates.actions(world, self.predictor, local=True))
        action = self._select(world, primaries, 'coverage')
        if action is None:
            return self._annotate(world, primary, 'mandatory_station', 0., 'coverage')
        return action

    def observe(self, world, action, response):
        """Called exactly once after an accepted action was committed by state.apply."""
        d = action.details
        if d.get('budget_context')=='coverage' and d.get('budget_role')=='optional':
            anchor, origin = d['budget_anchor'], d['start_position']
            elapsed = max(0., world.virtual_time-d['start_virtual_time'])
            progress = (math.dist(origin, anchor)-math.dist(world.position, anchor))/self.cfg.speed
            charge = max(0., elapsed-progress+self.cfg.switch_seconds*(action.kind=='measure'))
            if self.station is None or d['budget_station'] != self.station.id:
                raise InconsistentState('An executed detour belongs to a different station budget')
            self.station_budget -= charge
            if self.station_budget < -1e-5:
                raise InconsistentState('Cumulative station detour budget was exceeded')
            self.station_budget = max(0., self.station_budget)
            self.max_budget_used = max(self.max_budget_used, self.cfg.detour_budget_s-self.station_budget)
        if d.get('budget_context')=='finish' and d.get('budget_role')=='extra':
            elapsed = max(0., world.virtual_time-d['start_virtual_time'])
            self.finish_budget = max(0., self.finish_budget-elapsed-self.cfg.switch_seconds*(action.kind=='measure'))
        if 'fallback_index' in d:
            ch = world.channels[action.channel]
            ch.fallback_index = max(ch.fallback_index, d['fallback_index']+1)
            self.fallback_actions += 1
        if (d.get('phase')=='outer_patrol' and action.kind=='measure'
                and world.channels[action.channel].status=='KNOWN'):
            self.phase = 'outer_intercept'
            self._lock(world, action.channel, 'outer_discovery')

    def summary(self):
        return dict(phase=self.phase, locked=self.locked, decisions=self.decisions,
            fallback_actions=self.fallback_actions, max_station_budget_used=self.max_budget_used,
            stages=self.stage_records, targets=self.target_records,
            templates=self.template_records, pending_template=len(self.pending_template))
