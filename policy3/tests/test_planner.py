import math
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from strategy3.config import Config
from strategy3.model import Action, Channel, World, Observation
from strategy3.gains import Forecast, Predictor, measurement_forecast, worst_service
from strategy3.templates import four
from strategy3.fallback import build_points, next_point
from strategy3.scheduler import Scheduler


def square(center=(0, 0), half=50):
    return np.array([[-half, -half], [half, -half], [half, half], [-half, half]], dtype=float)+center


class FakeCoverage:
    def __init__(self, group='I', point=(1000., 0.)):
        self.station = SimpleNamespace(id=group+'00', group=group, point=point)
        self.stage = group
        self.selected = self.station
        self.done_channels = set()

    def next_station(self, world):
        return self.station if self.pending_channels(self.station, world) else None

    def pending_channels(self, station, world):
        return [c.channel for c in world.unknown() if c.channel not in self.done_channels]

    def exploration_gain(self, point, channel):
        return .1


class ForecastTests(unittest.TestCase):
    def test_negative_branch_actually_reduces_position(self):
        b = SimpleNamespace(particles=np.array([[0., 0.], [200., 0.]]),
            weights=np.array([.5, .5]), radii=np.array([1000., 1000.]),
            directional=np.array([True, True]), orientations=np.array([0., 0.]))
        f = measurement_forecast(b, (100., 0.), Config())
        self.assertAlmostEqual(f.no_signal_probability, .5)
        self.assertAlmostEqual(f.no_signal_refine, 1.)
        self.assertGreater(f.refine, .9)

    def test_joint_radius_and_orientation_control_detection(self):
        b = SimpleNamespace(particles=np.array([[0., 0.], [0., 0.], [0., 0.]]),
            weights=np.array([1/3]*3), radii=np.array([1000., 1500., 1500.]),
            directional=np.array([False, False, True]), orientations=np.array([np.nan, np.nan, math.pi]))
        f = measurement_forecast(b, (1200., 0.), Config())
        self.assertAlmostEqual(f.no_signal_probability, 2/3)

    def test_clear_does_not_switch_radio(self):
        cfg, coverage = Config(), FakeCoverage()
        predictor = Predictor(cfg, coverage)
        predictor.forecast = lambda w, a: Forecast(clear=.5, service=5.)
        world = World({1:Channel(1, square()), 2:Channel(2, square())}, radio=1)
        ops = (Action((0., 0.), 'clear', 2), Action((0., 0.), 'measure', 1))
        self.assertEqual(predictor.evaluate(world, ops).time, 10.)
        self.assertEqual(worst_service(ops[0], 1, cfg), 5.)

    def test_same_location_measurement_not_independent(self):
        ch = Channel(1, square(), status='KNOWN', history=[Observation((0.,0.), 'direction', 45.)])
        world = World({1: ch})
        self.assertFalse(Predictor(Config(), FakeCoverage()).valid(world, Action((0.,0.), 'measure', 1)))

    def test_negative_cell_union_can_certify_clear(self):
        ch = Channel(1, square(half=500), status='KNOWN')
        b = SimpleNamespace(cell_polygons=[square(half=3)], particles=np.empty((0,2)), weights=np.empty(0))
        with patch('strategy3.gains.rebuild', return_value=b):
            result = Predictor(Config(), FakeCoverage()).forecast(World({1:ch}), Action((0.,0.), 'clear', 1))
        self.assertTrue(result.reliable_clear)
        self.assertEqual(result.clear, 1.)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.world = World({1:Channel(1, square(), status='KNOWN'),
                            2:Channel(2, square(), status='KNOWN'),
                            3:Channel(3, square())})
        self.predictor = Predictor(self.cfg, FakeCoverage())
        self.predictor.forecast = lambda w,a: Forecast(explore=.1, refine=.2,
                                                       clear=.8 if a.kind=='clear' else 0., service=5.)

    def test_four_templates_preserve_primary_and_unique_channels(self):
        primary = Action((20., 0.), 'clear', 1)
        result = four(self.world, primary, self.predictor, self.cfg)
        self.assertEqual({t.name for t in result}, {'primary','primary+known','primary+unknown','primary+known+unknown'})
        for template in result:
            self.assertEqual(template.actions[0], primary)
            self.assertEqual(len(template.actions), len({a.channel for a in template.actions}))
        combined = next(t for t in result if len(t.actions)==3)
        # The 20 m move is charged once; clear actions retain radio=1.
        self.assertEqual(self.predictor.evaluate(self.world, combined.actions).time, 20.)

    def test_queued_extra_is_revalidated_and_executed(self):
        scheduler = Scheduler(self.cfg, FakeCoverage(point=(0., 0.)))
        scheduler.station = scheduler.coverage.station
        scheduler.phase = 'inner_explore'
        scheduler.predictor = self.predictor
        scheduler.pending_template = [(Action((0.,0.),'clear',2),'primary+known',.2,'coverage')]
        action = scheduler._pending(self.world)
        self.assertEqual(action.channel, 2)
        self.assertEqual(action.details['template'], 'primary+known')
        self.assertEqual(scheduler.pending_template, [])

    def test_successful_primary_clear_keeps_valid_promised_extras(self):
        scheduler = Scheduler(self.cfg, FakeCoverage(point=(0., 0.)))
        scheduler.predictor = self.predictor
        scheduler.locked = 1
        scheduler.phase = 'inner_cleanup'
        self.world.channels[1].status = 'CLEARED'
        scheduler.pending_template = [(Action((0.,0.),'clear',2),'primary+known',.2,'finish')]
        action = scheduler.choose(self.world)
        self.assertEqual(action.channel, 2)
        self.assertEqual(action.details['budget_role'], 'extra')
        self.assertEqual(action.details['locked_channel'], 1)


class SchedulerTests(unittest.TestCase):
    def test_clear_at_station_does_not_claim_measurement_credit(self):
        scheduler = Scheduler(Config(), FakeCoverage(point=(0.,0.)))
        scheduler.station = scheduler.coverage.station
        scheduler.predictor.forecast = lambda w,a: Forecast(clear=1., service=5.)
        world = World({1:Channel(1,square(),status='KNOWN'),2:Channel(2,square())})
        action = scheduler._annotate(world, Action((0.,0.),'clear',1), 'primary', 1., 'coverage')
        self.assertNotIn('station_id', action.details)
        measure = scheduler._annotate(world, Action((0.,0.),'measure',2), 'primary', 1., 'coverage')
        self.assertEqual(measure.details['station_id'], 'I00')

    def test_budget_is_cumulative_across_replanning(self):
        cfg = replace(Config(), detour_budget_s=12.)
        scheduler = Scheduler(cfg, FakeCoverage())
        scheduler.station = scheduler.coverage.station
        world = World({1:Channel(1, square(), status='KNOWN'), 2:Channel(2,square())})
        scheduler.predictor.forecast = lambda w,a: Forecast(clear=.5, service=3.)
        for step in range(2):
            action = scheduler._annotate(world, Action((100.*(step+1),0.),'clear',1), 'primary', .1, 'coverage')
            world.position = action.point
            world.virtual_time += 23.
            scheduler.observe(world, action, {})
        self.assertAlmostEqual(scheduler.station_budget, 6.)
        self.assertAlmostEqual(scheduler.max_budget_used, 6.)

    def test_outer_discovery_interrupts_and_preserves_patrol_station(self):
        scheduler = Scheduler(Config(), FakeCoverage(group='E'))
        world = World({1:Channel(1,square(),status='KNOWN')})
        scheduler.pending_template = [('unused',)]
        action = Action((1000.,0.), 'measure', 1, details={'phase':'outer_patrol'})
        scheduler.observe(world, action, {})
        self.assertEqual(scheduler.locked, 1)
        self.assertEqual(scheduler.phase, 'outer_intercept')
        self.assertEqual(scheduler.pending_template, [])
        self.assertEqual(scheduler.coverage.selected.id, 'E00')

    def test_known_targets_clear_before_outer_patrol(self):
        scheduler = Scheduler(Config(), FakeCoverage(group='E'))
        world = World({1:Channel(1,square(),status='KNOWN'), 2:Channel(2,square())})
        sentinel = Action((0.,0.),'clear',1)
        with patch.object(scheduler, '_finish', return_value=sentinel):
            self.assertEqual(scheduler.choose(world), sentinel)
        self.assertEqual(scheduler.phase, 'inner_cleanup')
        self.assertEqual(scheduler.locked, 1)

    def test_primary_limit_enters_finite_fallback(self):
        scheduler = Scheduler(Config(), FakeCoverage())
        ch = Channel(1, square(half=50), status='KNOWN', primary_actions=10)
        world = World({1:ch})
        scheduler.locked = 1
        scheduler.predictor.forecast = lambda w,a: Forecast(service=3.)
        action = scheduler._finish(world)
        self.assertEqual(action.reason, 'finite_clear_grid')
        self.assertTrue(action.details['is_primary'])
        self.assertEqual(ch.fallback_index, 0)  # Increment only after accepted execution.


class FallbackTests(unittest.TestCase):
    def test_cell_centers_cover_the_whole_positive_polygon(self):
        cfg = Config()
        ch = Channel(1, square((130.,-20.), half=70), status='KNOWN')
        route = np.asarray(build_points(ch, cfg, (0.,0.)))
        xx, yy = np.meshgrid(np.linspace(60.,200.,41), np.linspace(-90.,50.,41))
        points = np.column_stack((xx.ravel(),yy.ravel()))
        self.assertLess(np.max(np.min(np.linalg.norm(points[:,None]-route[None,:],axis=2),axis=1)),20.)

    def test_failed_center_only_skips_fully_excluded_cells(self):
        cfg = Config()
        ch = Channel(1, square(half=4), status='KNOWN')
        p, index = next_point(ch, cfg, (0.,0.))
        ch.failed_clear.append(p)
        q, new_index = next_point(ch, cfg, (0.,0.))
        self.assertNotEqual(p, q)
        self.assertGreater(new_index, index)


if __name__ == '__main__':
    unittest.main()
