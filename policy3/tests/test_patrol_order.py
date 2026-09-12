from dataclasses import replace
import unittest
from strategy3.config import Config
from strategy3.coverage import Coverage
from strategy3.state import initial_world


class PatrolOrderTests(unittest.TestCase):
    def test_interleaved_sweep_retains_station_despite_detour(self):
        cfg = replace(Config(), patrol_order='interleaved')
        world = initial_world(cfg)
        coverage = Coverage(cfg)
        expected = ['O'] + [f'{g}{i}' for i in range(1,8) for g in ('I','M')]
        for sid in expected:
            station = coverage.next_station(world)
            self.assertEqual(station.id, sid)
            coverage.mark_measured(sid, 1)
            world.position = (-2100., -300.)
            self.assertEqual(coverage.next_station(world).id, sid)
            for channel in range(2,21):
                coverage.mark_measured(sid, channel)
        self.assertEqual(coverage.next_station(world).group, 'E')

    def test_layered_remains_available_and_invalid_order_rejected(self):
        cfg = Config()
        world = initial_world(cfg)
        coverage = Coverage(cfg)
        for channel in range(1,21):
            coverage.mark_measured('O', channel)
        for _ in range(7):
            station = coverage.next_station(world)
            self.assertEqual(station.group, 'I')
            world.position = station.point
            for channel in range(1,21):
                coverage.mark_measured(station.id, channel)
        self.assertEqual(coverage.next_station(world).group, 'M')
        with self.assertRaises(ValueError):
            replace(cfg, patrol_order='bad').validate()
