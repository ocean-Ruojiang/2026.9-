import math
import unittest
from dataclasses import replace

import numpy as np

from strategy3 import angles
from strategy3.beliefs import point_feasibility, rebuild, possible_signal
from strategy3.config import Config
from strategy3.geometry import outer_disk, after_measure
from strategy3.grid import cell_possible, build_grid
from strategy3.model import Observation, Channel


class AngleTests(unittest.TestCase):
    def test_wrap_and_open_boundary(self):
        a = angles.arc(0, math.pi / 2, True)
        self.assertTrue(angles.contains(a, 0))
        self.assertTrue(angles.contains(a, 3 * math.pi / 2))
        self.assertFalse(angles.contains(a, math.pi))
        b = angles.arc(math.pi, math.pi / 2, False)
        self.assertEqual(angles.intersect(a, b), [])

    def test_boundary_only_positive_solution_survives(self):
        a = angles.intersect(angles.arc(0, math.pi / 2),
                             angles.arc(math.pi, math.pi / 2))
        self.assertTrue(a)
        self.assertAlmostEqual(angles.total_width(a), 0)
        self.assertEqual(len(angles.sample(a, 8)), 2)

    def test_narrow_arc_not_lost(self):
        a = angles.intersect(angles.arc(0, math.pi / 2),
            angles.arc(math.pi - 1e-8, math.pi / 2, False))
        self.assertTrue(a)
        self.assertLess(angles.total_width(a), 1e-7)
        self.assertTrue(all(angles.contains(a, v) for v in angles.sample(a, 4)))


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = replace(Config(), grid_max_cells=192, particle_count=96)

    def test_flanking_negatives_reject_far_positions(self):
        history = [Observation((0., 0.), 'direction', 0.),
                   Observation((800., 30.), 'no_signal'),
                   Observation((800., -30.), 'no_signal')]
        for point in ((900., 10.), (1200., 10.)):
            f = point_feasibility(point, history, self.cfg)
            self.assertFalse(f.omni)
            self.assertFalse(f.directional)
        f = point_feasibility((500., 0.), history, self.cfg)
        self.assertFalse(f.omni)
        self.assertTrue(f.directional)

    def test_one_negative_keeps_narrow_directional_explanation(self):
        history = [Observation((0., 0.), 'direction', 0.),
                   Observation((800., 0.), 'no_signal')]
        f = point_feasibility((1200., 10.), history, self.cfg)
        self.assertFalse(f.omni)
        self.assertTrue(f.directional)
        self.assertLess(math.degrees(angles.total_width(f.orientation_intervals)), 1.)

    def test_old_negative_activated_by_new_positive(self):
        x = (0., 0.)
        before = [Observation((900., 0.), 'direction', 180.),
                  Observation((1100., 0.), 'no_signal')]
        self.assertTrue(point_feasibility(x, before, self.cfg).omni)
        after = before + [Observation((1300., 0.), 'direction', 180.)]
        f = point_feasibility(x, after, self.cfg)
        self.assertFalse(f.omni)
        self.assertFalse(f.directional)

    def test_radius_upper_endpoint_singleton_is_feasible(self):
        hist = [Observation((1500., 0.), 'direction', 180.),
                Observation((1600., 0.), 'no_signal')]
        f = point_feasibility((0., 0.), hist, self.cfg)
        self.assertTrue(f.omni)
        self.assertEqual(f.lower_radius, 1500.)
        self.assertEqual(f.omni_upper_radius, 1500.)

    def test_failed_center_does_not_delete_cell(self):
        # Center is inside a failed clearing disk, but corner (19,19) is not.
        possible = cell_possible((-20., -20., 20., 20.), [], [], [(0., 0.)], self.cfg)
        self.assertTrue(possible[0] or possible[1])
        inside = cell_possible((-5., -5., 5., 5.), [], [], [(0., 0.)], self.cfg)
        self.assertEqual(inside[:2], (False, False))

    def test_true_static_hypotheses_survive_cell_rejection(self):
        rng = np.random.default_rng(1824)
        for _ in range(80):
            true = rng.uniform(-700, 700, 2)
            alpha = rng.uniform(0, 2 * math.pi)
            radius = rng.uniform(1000, 1500)
            unit = np.array([math.cos(alpha), math.sin(alpha)])
            history = []
            for point in rng.uniform(-1800, 1800, (12, 2)):
                vector = point - true
                d = np.linalg.norm(vector)
                if d <= radius and np.dot(unit, vector) >= 0:
                    b = math.degrees(math.atan2(-vector[1], -vector[0]))
                    history.append(Observation(tuple(point), 'direction', b))
                else:
                    history.append(Observation(tuple(point), 'no_signal'))
            positives = [o for o in history if o.result == 'direction']
            negatives = [o for o in history if o.result == 'no_signal']
            low = np.floor(true / 40.) * 40.
            flags = cell_possible((*low, *(low + 40)), positives, negatives, [], self.cfg)
            self.assertTrue(flags[1])

    def test_rebuild_particles_explain_history_and_keep_fixed_radius(self):
        history = [Observation((0., 0.), 'direction', 0.),
                   Observation((800., 30.), 'no_signal'),
                   Observation((800., -30.), 'no_signal')]
        polygon = outer_disk((0, 0), self.cfg.domain_radius, self.cfg.circle_sides)
        for o in history:
            polygon = after_measure(polygon, o.point, o.result, o.bearing, self.cfg)
        c = Channel(1, polygon, 'KNOWN', history=history, revision=3)
        b = rebuild(c, self.cfg)
        self.assertTrue(b.valid_samples)
        self.assertIs(b, rebuild(c, self.cfg))
        self.assertAlmostEqual(float(b.weights.sum()), 1.)
        self.assertTrue(np.all(b.directional))
        self.assertTrue(np.all(b.particles[:, 0] < 800))
        self.assertTrue(np.any(b.radii > 1000.1))
        for x, radius, alpha in zip(b.particles, b.radii, b.orientations):
            unit = np.array([math.cos(alpha), math.sin(alpha)])
            for o in history:
                vector = np.array(o.point) - x
                visible = np.linalg.norm(vector) <= radius and np.dot(unit, vector) >= -1e-8
                self.assertEqual(visible, o.result != 'no_signal')
        self.assertTrue(possible_signal(c, (100., 100.), self.cfg))
        self.assertFalse(possible_signal(c, (4000., 4000.), self.cfg))

    def test_empty_samples_do_not_mark_known_channel_absent(self):
        c = Channel(1, np.array([[2000., 0.], [2001., 0.], [2000., 1.]]),
                    'KNOWN', history=[Observation((0., 0.), 'direction', 0.)])
        b = rebuild(c, self.cfg)
        self.assertFalse(b.valid_samples)
        self.assertEqual(c.status, 'KNOWN')
        self.assertTrue(b.diagnostics['degraded'])

    def test_type_mass_and_single_particle_normalization(self):
        history = [Observation((0., 0.), 'direction', 45.)]
        polygon = after_measure(outer_disk((0, 0), 1800, 96),
                                (0, 0), 'direction', 45., self.cfg)
        channel = Channel(1, polygon, 'KNOWN', history=history, revision=1)
        belief = rebuild(channel, self.cfg)
        # With equal prior type weights, one positive is twice as likely for
        # an omni source as for a uniformly oriented directional source.
        self.assertAlmostEqual(float(belief.weights[belief.directional].sum()), 1 / 3, places=10)
        one = rebuild(channel, replace(self.cfg, particle_count=1))
        self.assertEqual(len(one.particles), 1)
        self.assertAlmostEqual(float(one.weights.sum()), 1.)


if __name__ == '__main__':
    unittest.main()
